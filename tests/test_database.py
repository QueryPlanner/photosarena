from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import LATEST_SCHEMA_VERSION, SchemaVersionError, connect, migrate


class DatabaseTests(unittest.TestCase):
    def test_migration_is_idempotent_and_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "photosarena.sqlite3"
            connection = connect(path)
            self.assertEqual(migrate(connection), LATEST_SCHEMA_VERSION)
            self.assertEqual(migrate(connection), LATEST_SCHEMA_VERSION)
            connection.close()

            reopened = connect(path)
            version = reopened.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            tables = {
                row["name"]
                for row in reopened.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            reopened.close()
            self.assertEqual(version, LATEST_SCHEMA_VERSION)
            self.assertTrue({"photos", "ballots", "votes"}.issubset(tables))

    def test_migration_rejects_a_newer_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "db.sqlite3")
            migrate(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, 0)",
                (LATEST_SCHEMA_VERSION + 1,),
            )
            with self.assertRaises(SchemaVersionError):
                migrate(connection)
            connection.close()

    def test_vote_history_cannot_be_updated_or_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "db.sqlite3")
            migrate(connection)
            connection.executemany(
                "INSERT INTO photos(id,object_key,created_at) VALUES (?,?,0)",
                [("a", "photos/a.jpg"), ("b", "photos/b.jpg")],
            )
            connection.execute("INSERT INTO games(id,total_rounds,created_at) VALUES ('g',5,0)")
            connection.execute(
                "INSERT INTO game_rounds(game_id,round_number,left_photo_id,right_photo_id) "
                "VALUES ('g',1,'a','b')"
            )
            connection.execute(
                "INSERT INTO ballots(id,token_hash,game_id,round_number,created_at,consumed_at) "
                "VALUES ('ballot','hash','g',1,0,1)"
            )
            connection.execute(
                "INSERT INTO votes("
                "id, ballot_id, game_id, round_number, winner_photo_id, loser_photo_id, cast_at) "
                "VALUES ('vote','ballot','g',1,'a','b',1)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE votes SET cast_at=0 WHERE id='vote'")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM votes WHERE id='vote'")
            connection.close()


if __name__ == "__main__":
    unittest.main()
