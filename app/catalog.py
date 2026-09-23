"""Validate an image manifest and synchronize public photo metadata."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

PHOTO_ID = re.compile(r"^[a-f0-9]{64}$")


class CatalogError(ValueError):
    """The configured image manifest is absent or invalid."""


def load_manifest(manifest_path: str | Path) -> list[dict[str, str]]:
    path = Path(manifest_path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError("The photo manifest could not be read.") from error
    photos = document.get("photos") if isinstance(document, dict) else None
    if not isinstance(photos, list) or len(photos) < 2:
        raise CatalogError("The photo manifest must contain at least two photos.")

    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for item in photos:
        if not isinstance(item, dict):
            raise CatalogError("Every photo manifest entry must be an object.")
        photo_id = item.get("id")
        object_key = item.get("object_key")
        if not isinstance(photo_id, str) or not PHOTO_ID.fullmatch(photo_id):
            raise CatalogError("A photo manifest entry has an invalid content ID.")
        if (
            not isinstance(object_key, str)
            or not object_key.startswith("photos/")
            or not object_key.endswith(".jpg")
            or ".." in object_key.split("/")
        ):
            raise CatalogError("A photo manifest entry has an invalid object key.")
        if photo_id in seen_ids or object_key in seen_keys:
            raise CatalogError("The photo manifest contains duplicate entries.")
        seen_ids.add(photo_id)
        seen_keys.add(object_key)
        normalized.append({"id": photo_id, "object_key": object_key})
    return normalized


def synchronize_catalog(
    connection: sqlite3.Connection, manifest_path: str | Path
) -> int:
    """Upsert manifest photos and deactivate assets no longer in the manifest."""
    photos = load_manifest(manifest_path)
    active_ids = {photo["id"] for photo in photos}
    now = int(time.time())
    connection.execute("BEGIN IMMEDIATE")
    try:
        for photo in photos:
            connection.execute(
                "INSERT INTO photos(id, object_key, active, created_at) "
                "VALUES (?, ?, 1, ?) "
                "ON CONFLICT(id) DO UPDATE SET object_key=excluded.object_key, "
                "active=1",
                (photo["id"], photo["object_key"], now),
            )
        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            connection.execute(
                f"UPDATE photos SET active=0 WHERE id NOT IN ({placeholders})",
                tuple(active_ids),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return len(photos)


def image_url(object_key: str, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(object_key, safe='/')}"
