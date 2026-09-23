from __future__ import annotations

import base64
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.server import make_handler

from tests.helpers import AppFixture


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = AppFixture()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.fixture.settings, self.fixture.service)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.fixture.close()

    def test_health_and_game_route(self) -> None:
        with urlopen(f"{self.base_url}/healthz") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["status"], "ok")
        request = Request(
            f"{self.base_url}/api/games",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            payload = json.loads(response.read())
            self.assertEqual(payload["round"], 1)
            self.assertEqual(len(payload["choices"]), 2)

    def test_owner_results_require_basic_auth(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            urlopen(f"{self.base_url}/api/owner/ranking")
        self.assertEqual(raised.exception.code, 401)
        raised.exception.close()

        raw = f"owner:{self.fixture.settings.owner_secret}".encode()
        authorization = base64.b64encode(raw).decode()
        request = Request(
            f"{self.base_url}/api/owner/ranking",
            headers={"Authorization": f"Basic {authorization}"},
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(len(json.loads(response.read())["ranking"]), 5)


if __name__ == "__main__":
    unittest.main()
