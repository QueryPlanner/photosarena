from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.backup import backup_database
from app.database import connect, migrate


class BackupTests(unittest.TestCase):
    def test_online_backup_contains_committed_wal_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            connection = connect(source)
            migrate(connection)
            connection.execute(
                "INSERT INTO photos(id,object_key,created_at) VALUES ('a','photos/a.jpg',1)"
            )
            destination = backup_database(str(source), str(root / "backups" / "copy.sqlite3"))
            connection.close()
            copied = sqlite3.connect(destination)
            try:
                self.assertEqual(copied.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(copied.execute("SELECT COUNT(*) FROM photos").fetchone()[0], 1)
            finally:
                copied.close()


if __name__ == "__main__":
    unittest.main()
