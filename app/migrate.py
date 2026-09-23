"""Run pending SQLite schema migrations."""

from __future__ import annotations

import os

from app.database import connect, migrate


def main() -> None:
    database_path = os.environ.get("DATABASE_PATH", "/data/photosarena.sqlite3")
    connection = connect(database_path)
    try:
        version = migrate(connection)
    finally:
        connection.close()
    print(f"SQLite schema is at version {version}.")


if __name__ == "__main__":
    main()
