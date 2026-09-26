from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.settings import Settings

BASE_ENVIRONMENT = {
    "PHOTO_BASE_URL": "https://photos.example.test",
    "OWNER_SECRET": "owner-secret-for-tests-at-least-32-chars",
    "BALLOT_SECRET": "ballot-secret-for-tests-at-least-32-chars",
}


class SettingsTests(unittest.TestCase):
    def test_host_defaults_to_loopback(self) -> None:
        with patch.dict(os.environ, BASE_ENVIRONMENT, clear=True):
            settings = Settings.from_environment()
        self.assertEqual(settings.host, "127.0.0.1")

    def test_host_can_bind_inside_a_container(self) -> None:
        environment = {**BASE_ENVIRONMENT, "HOST": "0.0.0.0"}
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_environment()
        self.assertEqual(settings.host, "0.0.0.0")

    def test_host_rejects_unexpected_bind_addresses(self) -> None:
        environment = {**BASE_ENVIRONMENT, "HOST": "192.0.2.10"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "HOST must be"):
                Settings.from_environment()


if __name__ == "__main__":
    unittest.main()
