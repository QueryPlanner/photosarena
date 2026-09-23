from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.application import create_service
from app.catalog import synchronize_catalog
from app.database import connect, migrate
from app.settings import Settings

PHOTO_IDS = [f"{value:064x}" for value in range(1, 6)]


class AppFixture:
    def __init__(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database_path = str(self.root / "data" / "photosarena.sqlite3")
        self.manifest_path = self.root / "photo-manifest.json"
        self.manifest_path.write_text(
            json.dumps(
                {
                    "photos": [
                        {"id": photo_id, "object_key": f"photos/{photo_id}.jpg"}
                        for photo_id in PHOTO_IDS
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.settings = Settings(
            database_path=self.database_path,
            manifest_path=str(self.manifest_path),
            photo_base_url="https://photos.example.test",
            owner_secret="owner-secret-for-tests-at-least-32-chars",
            ballot_secret="ballot-secret-for-tests-at-least-32-chars",
            port=0,
        )
        connection = connect(self.database_path)
        try:
            migrate(connection)
            synchronize_catalog(connection, self.manifest_path)
        finally:
            connection.close()
        self.service = create_service(self.settings)

    def close(self) -> None:
        self.tempdir.cleanup()
