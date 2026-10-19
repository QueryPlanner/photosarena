from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.backup import backup_database
from app.database import connect, migrate


class BackupTests(unittest.TestCase):
    def test_first_deploy_retry_backs_up_existing_committed_wal_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            connection = connect(source)
            migrate(connection)
            connection.execute(
                "INSERT INTO photos(id,object_key,created_at) VALUES ('a','photos/a.jpg',1)"
            )
            destination = root / "backups" / "copy.sqlite3"
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "app.backup", "--allow-missing"],
                    env={
                        **os.environ,
                        "DATABASE_PATH": str(source),
                        "PHOTOSARENA_BACKUP_DEST": str(destination),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                connection.close()
            copied = sqlite3.connect(destination)
            try:
                self.assertEqual(copied.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(copied.execute("SELECT COUNT(*) FROM photos").fetchone()[0], 1)
            finally:
                copied.close()

    def test_refuses_to_replace_source_with_itself(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            source.touch()
            with self.assertRaisesRegex(ValueError, "must differ"):
                backup_database(str(source), str(source))

    def test_failed_backup_preserves_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "corrupt.sqlite3"
            source.write_bytes(b"invalid sqlite data")
            destination = Path(directory) / "previous.sqlite3"
            destination.write_bytes(b"previous backup")
            with self.assertRaises(sqlite3.DatabaseError):
                backup_database(str(source), str(destination))
            self.assertEqual(destination.read_bytes(), b"previous backup")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_cli_requires_backup_destination_even_for_first_deployment(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "app.backup", "--allow-missing"],
            env={**os.environ, "PHOTOSARENA_BACKUP_DEST": ""},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PHOTOSARENA_BACKUP_DEST is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
