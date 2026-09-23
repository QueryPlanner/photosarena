"""Small standard-library HTTP server for PhotosArena."""

from __future__ import annotations

import base64
import binascii
import html
import json
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from app.application import create_service
from app.database import LATEST_SCHEMA_VERSION, connect
from app.service import PhotosArenaService, ServiceError
from app.settings import Settings

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 8192


def make_handler(settings: Settings, service: PhotosArenaService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PhotosArena/1.0"

        def log_message(self, format: str, *args: object) -> None:
            # Keep IP addresses and request data out of application logs.
            return

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._health()
            elif path == "/owner":
                if self._authorized_owner():
                    self._owner_page()
            elif path == "/api/owner/ranking":
                if self._authorized_owner():
                    self._json(HTTPStatus.OK, {"ranking": service.ranking()})
            elif path == "/":
                self._file("index.html", "text/html; charset=utf-8")
            elif path == "/static/app.css":
                self._file("app.css", "text/css; charset=utf-8")
            elif path == "/static/app.js":
                self._file("app.js", "text/javascript; charset=utf-8")
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            try:
                payload = self._read_json()
                if path == "/api/games":
                    if payload != {}:
                        raise ServiceError(400, "The game request must be empty.")
                    response = service.start_game()
                elif path == "/api/votes":
                    ballot = payload.get("ballot_id")
                    choice = payload.get("choice_id")
                    if not isinstance(ballot, str) or not isinstance(choice, str):
                        raise ServiceError(400, "A ballot and selected photo are required.")
                    response = service.submit_vote(ballot, choice)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                    return
                self._json(HTTPStatus.OK, response)
            except ServiceError as error:
                self._json(error.status, {"error": str(error)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON request."})

        def _read_json(self) -> dict[str, object]:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Content-Type must be application/json.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("Invalid Content-Length.") from error
            if not 0 < length <= MAX_BODY_BYTES:
                raise ValueError("Request body size is invalid.")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("A JSON object is required.")
            return value

        def _authorized_owner(self) -> bool:
            header = self.headers.get("Authorization", "")
            encoded = header.removeprefix("Basic ") if header.startswith("Basic ") else ""
            try:
                decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                decoded = ":"
            username, separator, password = decoded.partition(":")
            username_ok = secrets_compare(username, "owner")
            password_ok = secrets_compare(password, settings.owner_secret)
            if separator and username_ok and password_ok:
                return True
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Basic realm="PhotosArena owner"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def _owner_page(self) -> None:
            ranking = service.ranking()
            items = "\n".join(
                "<li><b>#{} · Elo {}</b> <img src=\"{}\" alt=\"\" "
                "width=\"96\" height=\"72\"> <span>{} wins · {} comparisons</span></li>".format(
                    index,
                    item["rating"],
                    html.escape(str(item["image_url"]), quote=True),
                    item["votes"],
                    item["comparisons"],
                )
                for index, item in enumerate(ranking, start=1)
            )
            document = (
                "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width\">"
                "<title>PhotosArena owner results</title><style>body{font:16px system-ui;"
                "max-width:760px;margin:40px auto;padding:0 18px;background:#f8f7f4}"
                "li{display:flex;gap:14px;align-items:center;padding:12px;margin:10px 0;"
                "background:white;border:2px solid #ddd;border-radius:14px}img{object-fit:cover;"
                "border:2px solid #555;border-radius:9px}</style><h1>Global crowd ranking</h1>"
                f"<ol>{items}</ol></html>"
            )
            self._bytes(HTTPStatus.OK, document.encode("utf-8"), "text/html; charset=utf-8")

        def _health(self) -> None:
            try:
                connection = connect(settings.database_path)
                try:
                    check = connection.execute("PRAGMA quick_check").fetchone()[0]
                    version = connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                    photo_count = connection.execute(
                        "SELECT COUNT(*) FROM photos WHERE active=1"
                    ).fetchone()[0]
                finally:
                    connection.close()
                healthy = (
                    check == "ok"
                    and version == LATEST_SCHEMA_VERSION
                    and photo_count >= 2
                )
                status = HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE
                self._json(
                    status,
                    {"status": "ok" if healthy else "not_ready", "photos": photo_count},
                )
            except sqlite3.Error:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "database_error"})

        def _file(self, filename: str, content_type: str) -> None:
            path = STATIC_DIR / filename
            try:
                body = path.read_bytes()
            except OSError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            self._bytes(HTTPStatus.OK, body, content_type)

        def _json(self, status: int | HTTPStatus, payload: object) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self._bytes(status, body, "application/json; charset=utf-8")

        def _bytes(self, status: int | HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def secrets_compare(first: str, second: str) -> bool:
    import hmac

    return hmac.compare_digest(first.encode("utf-8"), second.encode("utf-8"))


def main() -> None:
    settings = Settings.from_environment()
    service = create_service(settings)
    handler = make_handler(settings, service)
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), handler)
    server.daemon_threads = True
    print(f"PhotosArena listening on port {settings.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
