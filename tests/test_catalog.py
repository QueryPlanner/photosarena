from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.catalog import CatalogError, load_manifest, synchronize_catalog
from app.database import connect, migrate


class CatalogTests(unittest.TestCase):
    def test_manifest_validates_assets_and_synchronizes_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "manifest.json"
            ids = ["a" * 64, "b" * 64]
            path.write_text(
                json.dumps(
                    {"photos": [{"id": item, "object_key": f"photos/{item}.jpg"} for item in ids]}
                ),
                encoding="utf-8",
            )
            connection = connect(root / "db.sqlite3")
            migrate(connection)
            self.assertEqual(len(load_manifest(path)), 2)
            self.assertEqual(synchronize_catalog(connection, path), 2)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM photos WHERE active=1").fetchone()[0],
                2,
            )
            connection.close()

    def test_rejects_missing_duplicate_or_unsafe_manifest_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text("{\"photos\": []}", encoding="utf-8")
            with self.assertRaises(CatalogError):
                load_manifest(path)
            path.write_text(
                json.dumps(
                    {
                        "photos": [
                            {"id": "a" * 64, "object_key": "photos/../leak.jpg"},
                            {"id": "b" * 64, "object_key": "photos/b.jpg"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CatalogError):
                load_manifest(path)


if __name__ == "__main__":
    unittest.main()
