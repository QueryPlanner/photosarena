"""Environment-backed runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str
    manifest_path: str
    photo_base_url: str
    owner_secret: str
    ballot_secret: str
    port: int

    @classmethod
    def from_environment(cls) -> Settings:
        photo_base_url = os.environ.get("PHOTO_BASE_URL", "").strip()
        if not photo_base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("PHOTO_BASE_URL must be an HTTPS public image URL.")
        owner_secret = os.environ.get("OWNER_SECRET", "")
        if len(owner_secret) < 32:
            raise ValueError("OWNER_SECRET must contain at least 32 characters.")
        ballot_secret = os.environ.get("BALLOT_SECRET", "")
        if len(ballot_secret.encode("utf-8")) < 32:
            raise ValueError("BALLOT_SECRET must contain at least 32 bytes.")
        port = int(os.environ.get("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535.")
        return cls(
            database_path=os.environ.get("DATABASE_PATH", "/data/photosarena.sqlite3"),
            manifest_path=os.environ.get("PHOTO_MANIFEST_PATH", "/app/photo-manifest.json"),
            photo_base_url=photo_base_url,
            owner_secret=owner_secret,
            ballot_secret=ballot_secret,
            port=port,
        )
