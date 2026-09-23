#!/usr/bin/env python3
"""Upload processed PhotosArena images through Cloudflare's R2 API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


DEFAULT_API_BASE_URL = "https://api.cloudflare.com/client/v4"
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
PHOTO_ID_RE = re.compile(r"^[0-9a-f]{64}$")
ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,62}[a-z0-9]$")
CONTENT_TYPES = {".jpg": "image/jpeg"}


class UploadError(Exception):
    """A safe-to-display validation or upload failure."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise UploadError(f"Required environment variable is missing: {name}")
    return value


def _validated_base_url() -> str:
    raw = os.environ.get("CLOUDFLARE_API_BASE_URL", DEFAULT_API_BASE_URL).strip()
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise UploadError("CLOUDFLARE_API_BASE_URL must be an HTTP(S) origin or path")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise UploadError("Plain HTTP is allowed only for a loopback test endpoint")
    return raw.rstrip("/")


def _load_manifest(manifest_path: Path, processed_dir: Path) -> list[tuple[str, str, Path]]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UploadError("Could not read a valid photo manifest") from exc

    if not isinstance(manifest, dict) or set(manifest) != {"photos"}:
        raise UploadError("Photo manifest must contain only a photos array")
    photos = manifest["photos"]
    if not isinstance(photos, list) or not photos:
        raise UploadError("Photo manifest must contain at least one photo")

    records: list[tuple[str, str, Path]] = []
    seen: set[str] = set()
    for item in photos:
        if not isinstance(item, dict) or set(item) != {"id", "object_key"}:
            raise UploadError("Each manifest photo must contain only id and object_key")
        photo_id, object_key = item["id"], item["object_key"]
        if not isinstance(photo_id, str) or not PHOTO_ID_RE.fullmatch(photo_id):
            raise UploadError("Photo ids must be lowercase SHA-256 hex strings")
        if photo_id in seen:
            raise UploadError("Photo manifest contains a duplicate id")
        seen.add(photo_id)

        extension = Path(object_key).suffix if isinstance(object_key, str) else ""
        if extension != ".jpg" or object_key != f"photos/{photo_id}.jpg":
            raise UploadError("Photo object key must match its generated id")

        local_path = processed_dir / f"{photo_id}{extension}"
        try:
            if local_path.is_symlink() or not local_path.is_file():
                raise UploadError("A processed image file is missing or not a regular file")
            data_digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise UploadError("Could not read a processed image file") from exc
        if data_digest != photo_id:
            raise UploadError("Processed image contents do not match the manifest id")
        records.append((photo_id, object_key, local_path))
    return records


def _multipart_body(photo_id: str, extension: str, image_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"photosarena-{uuid.uuid4().hex}"
    filename = f"{photo_id}{extension}"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="body"; filename="{filename}"\r\n'
        f"Content-Type: {CONTENT_TYPES[extension]}\r\n\r\n"
    ).encode("ascii")
    footer = f"\r\n--{boundary}--\r\n".encode("ascii")
    return header + image_bytes + footer, f"multipart/form-data; boundary={boundary}"


def _object_url(base_url: str, account_id: str, bucket: str, object_key: str) -> str:
    path = "/".join(
        (
            "accounts",
            urllib.parse.quote(account_id, safe=""),
            "r2",
            "buckets",
            urllib.parse.quote(bucket, safe=""),
            "objects",
            urllib.parse.quote(object_key, safe="/-_.~"),
        )
    )
    return f"{base_url}/{path}"


def _upload_one(
    *,
    api_base_url: str,
    account_id: str,
    bucket: str,
    token: str,
    object_key: str,
    local_path: Path,
    timeout_seconds: float = 60,
) -> None:
    extension = local_path.suffix
    try:
        image_bytes = local_path.read_bytes()
    except OSError as exc:
        raise UploadError("Could not read a processed image file") from exc
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise UploadError("Processed image exceeds Cloudflare's 300 MB upload limit")

    body, content_type = _multipart_body(local_path.stem, extension, image_bytes)
    request = urllib.request.Request(
        _object_url(api_base_url, account_id, bucket, object_key),
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_payload = response.read()
    except urllib.error.HTTPError as exc:
        raise UploadError(f"Cloudflare upload failed with HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UploadError("Cloudflare upload could not be reached") from None

    try:
        decoded: Any = json.loads(response_payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise UploadError("Cloudflare returned an invalid upload response") from None
    result = decoded.get("result") if isinstance(decoded, dict) else None
    if (
        not isinstance(decoded, dict)
        or decoded.get("success") is not True
        or not isinstance(result, dict)
        or result.get("key") != object_key
    ):
        raise UploadError("Cloudflare did not confirm the expected object key")


def upload_manifest(processed_dir: Path, manifest_path: Path) -> int:
    account_id = _required_env("CLOUDFLARE_ACCOUNT_ID")
    if not ACCOUNT_ID_RE.fullmatch(account_id):
        raise UploadError("CLOUDFLARE_ACCOUNT_ID must be a 32-character hex id")
    token = _required_env("CLOUDFLARE_API_TOKEN")
    bucket = _required_env("R2_BUCKET_NAME")
    if not BUCKET_RE.fullmatch(bucket):
        raise UploadError("R2_BUCKET_NAME must be a valid lowercase bucket name")
    api_base_url = _validated_base_url()

    records = _load_manifest(manifest_path, processed_dir)
    for _, object_key, local_path in records:
        _upload_one(
            api_base_url=api_base_url,
            account_id=account_id,
            bucket=bucket,
            token=token,
            object_key=object_key,
            local_path=local_path,
        )
        print(f"Uploaded {object_key}")
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upload processed PhotosArena images to a Cloudflare R2 bucket."
    )
    parser.add_argument(
        "processed_dir",
        type=Path,
        help="Directory containing the hash-named images and manifest.json",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Manifest path (defaults to <processed_dir>/manifest.json)",
    )
    args = parser.parse_args(argv)
    manifest_path = args.manifest or args.processed_dir / "manifest.json"

    try:
        count = upload_manifest(args.processed_dir, manifest_path)
    except UploadError as exc:
        print(f"upload_r2: {exc}", file=sys.stderr)
        return 1
    print(f"Uploaded {count} photo(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
