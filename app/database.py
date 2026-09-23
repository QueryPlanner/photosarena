"""SQLite connection and forward-only schema migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

LATEST_SCHEMA_VERSION = 1

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE photos (
        id TEXT PRIMARY KEY,
        object_key TEXT NOT NULL UNIQUE,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        created_at INTEGER NOT NULL
    );

    CREATE TABLE games (
        id TEXT PRIMARY KEY,
        total_rounds INTEGER NOT NULL CHECK (total_rounds = 5),
        created_at INTEGER NOT NULL
    );

    CREATE TABLE game_rounds (
        game_id TEXT NOT NULL REFERENCES games(id),
        round_number INTEGER NOT NULL CHECK (round_number BETWEEN 1 AND 5),
        left_photo_id TEXT NOT NULL REFERENCES photos(id),
        right_photo_id TEXT NOT NULL REFERENCES photos(id),
        PRIMARY KEY (game_id, round_number),
        CHECK (left_photo_id <> right_photo_id)
    );

    CREATE TABLE ballots (
        id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        game_id TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        consumed_at INTEGER,
        UNIQUE (game_id, round_number),
        FOREIGN KEY (game_id, round_number)
            REFERENCES game_rounds(game_id, round_number)
    );

    CREATE TABLE votes (
        id TEXT PRIMARY KEY,
        ballot_id TEXT NOT NULL UNIQUE REFERENCES ballots(id),
        game_id TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        winner_photo_id TEXT NOT NULL REFERENCES photos(id),
        loser_photo_id TEXT NOT NULL REFERENCES photos(id),
        cast_at INTEGER NOT NULL,
        UNIQUE (game_id, round_number),
        FOREIGN KEY (game_id, round_number)
            REFERENCES game_rounds(game_id, round_number),
        CHECK (winner_photo_id <> loser_photo_id)
    );

    CREATE INDEX votes_cast_at_idx ON votes(cast_at, id);

    CREATE TRIGGER votes_are_immutable_update
    BEFORE UPDATE ON votes
    BEGIN
        SELECT RAISE(ABORT, 'vote history is immutable');
    END;

    CREATE TRIGGER votes_are_immutable_delete
    BEFORE DELETE ON votes
    BEGIN
        SELECT RAISE(ABORT, 'vote history is immutable');
    END;
    """,
)


class SchemaVersionError(RuntimeError):
    """The database was created by a newer version of the application."""


def connect(database_path: str | Path) -> sqlite3.Connection:
    """Open a configured SQLite connection, creating its parent if needed."""
    raw_path = str(database_path)
    path = Path(raw_path)
    if raw_path != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(raw_path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    if raw_path != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
    return connection


def migrate(connection: sqlite3.Connection) -> int:
    """Apply pending additive migrations and return the current schema version."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
    ).fetchone()
    current_version = int(row["version"])
    if current_version > LATEST_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema {current_version} is newer than supported "
            f"schema {LATEST_SCHEMA_VERSION}."
        )

    for index in range(current_version, LATEST_SCHEMA_VERSION):
        version = index + 1
        script = (
            "BEGIN IMMEDIATE;\n"
            + MIGRATIONS[index]
            + "\nINSERT INTO schema_migrations(version, applied_at) "
            + f"VALUES ({version}, unixepoch());\nCOMMIT;"
        )
        try:
            connection.executescript(script)
        except Exception:
            connection.rollback()
            raise
    return LATEST_SCHEMA_VERSION
