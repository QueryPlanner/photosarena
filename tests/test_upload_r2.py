from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOADER = PROJECT_ROOT / "tools" / "upload_r2.py"
ACCOUNT_ID = "a" * 32
API_TOKEN = "synthetic-test-token-never-print"


class FakeR2Handler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    response_status = 200

    def do_PUT(self) -> None:  # noqa: N802 - HTTP handler method name
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        parsed = urlsplit(self.path)
        decoded_path = unquote(parsed.path)
        object_key = decoded_path.split("/objects/", 1)[-1]
        type(self).requests.append(
            {
                "path": decoded_path,
                "object_key": object_key,
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
                "body": body,
            }
        )
        status = type(self).response_status
        if status == 200:
            payload = json.dumps(
                {"success": True, "result": {"key": object_key}}
            ).encode("utf-8")
        else:
            payload = b'{"success":false,"errors":[{"message":"synthetic denial"}]}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class UploadR2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeR2Handler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/client/v4"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)

    def setUp(self) -> None:
        FakeR2Handler.requests = []
        FakeR2Handler.response_status = 200
        self.temp_dir = tempfile.TemporaryDirectory()
        self.processed_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_image_and_manifest(self) -> tuple[bytes, str, str]:
        image_bytes = b"synthetic processed image fixture; contains no personal photo"
        photo_id = hashlib.sha256(image_bytes).hexdigest()
        object_key = f"photos/{photo_id}.jpg"
        (self.processed_dir / f"{photo_id}.jpg").write_bytes(image_bytes)
        (self.processed_dir / "manifest.json").write_text(
            json.dumps({"photos": [{"id": photo_id, "object_key": object_key}]}),
            encoding="utf-8",
        )
        return image_bytes, photo_id, object_key

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID,
                "CLOUDFLARE_API_TOKEN": API_TOKEN,
                "R2_BUCKET_NAME": "photosarena",
                "CLOUDFLARE_API_BASE_URL": self.base_url,
            }
        )
        return env

    def _run_uploader(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(UPLOADER), str(self.processed_dir)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )

    def test_uploads_hash_named_jpeg_using_documented_put_route(self) -> None:
        image_bytes, photo_id, object_key = self._write_image_and_manifest()

        result = self._run_uploader(self._environment())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(FakeR2Handler.requests), 1)
        request = FakeR2Handler.requests[0]
        self.assertEqual(
            request["path"],
            f"/client/v4/accounts/{ACCOUNT_ID}/r2/buckets/photosarena/objects/{object_key}",
        )
        self.assertEqual(request["authorization"], f"Bearer {API_TOKEN}")
        self.assertTrue(str(request["content_type"]).startswith("multipart/form-data; boundary="))
        self.assertIn(f'name="body"; filename="{photo_id}.jpg"'.encode(), request["body"])
        self.assertIn(b"Content-Type: image/jpeg", request["body"])
        self.assertIn(image_bytes, request["body"])
        self.assertIn(f"Uploaded {object_key}", result.stdout)
        self.assertNotIn(API_TOKEN, result.stdout + result.stderr)
        self.assertNotIn(image_bytes.decode(), result.stdout + result.stderr)

    def test_requires_environment_credentials_without_network_request(self) -> None:
        self._write_image_and_manifest()
        env = self._environment()
        del env["CLOUDFLARE_API_TOKEN"]

        result = self._run_uploader(env)

        self.assertEqual(result.returncode, 1)
        self.assertIn("CLOUDFLARE_API_TOKEN", result.stderr)
        self.assertNotIn(API_TOKEN, result.stdout + result.stderr)
        self.assertEqual(FakeR2Handler.requests, [])

    def test_refuses_to_send_token_over_plain_http_to_a_remote_host(self) -> None:
        self._write_image_and_manifest()
        env = self._environment()
        env["CLOUDFLARE_API_BASE_URL"] = "http://api.cloudflare.com/client/v4"

        result = self._run_uploader(env)

        self.assertEqual(result.returncode, 1)
        self.assertIn("loopback test endpoint", result.stderr)
        self.assertEqual(FakeR2Handler.requests, [])
        self.assertNotIn(API_TOKEN, result.stdout + result.stderr)

    def test_rejects_content_hash_mismatch_before_network_request(self) -> None:
        image_bytes, photo_id, object_key = self._write_image_and_manifest()
        (self.processed_dir / f"{photo_id}.jpg").write_bytes(image_bytes + b" changed")

        result = self._run_uploader(self._environment())

        self.assertEqual(result.returncode, 1)
        self.assertIn("contents do not match", result.stderr)
        self.assertEqual(FakeR2Handler.requests, [])
        self.assertNotIn(object_key, result.stdout)

    def test_does_not_expose_response_body_or_token_on_api_failure(self) -> None:
        self._write_image_and_manifest()
        FakeR2Handler.response_status = 403

        result = self._run_uploader(self._environment())

        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 403", result.stderr)
        self.assertNotIn(API_TOKEN, result.stdout + result.stderr)
        self.assertNotIn("synthetic denial", result.stdout + result.stderr)

    def test_rejects_manifest_fields_that_could_leak_source_filenames(self) -> None:
        _, photo_id, object_key = self._write_image_and_manifest()
        (self.processed_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "photos": [
                        {"id": photo_id, "object_key": object_key, "source": "private-name.jpg"}
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = self._run_uploader(self._environment())

        self.assertEqual(result.returncode, 1)
        self.assertIn("only id and object_key", result.stderr)
        self.assertEqual(FakeR2Handler.requests, [])


if __name__ == "__main__":
    unittest.main()
