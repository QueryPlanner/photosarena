"""Create an online SQLite backup, including committed WAL contents."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path


def backup_database(source_path: str, destination_path: str) -> Path:
    source = Path(source_path)
    destination = Path(destination_path)
    if not source.is_file():
        raise FileNotFoundError("The SQLite database does not exist.")
    if source.resolve() == destination.resolve():
        raise ValueError("The backup destination must differ from the database.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(source) as source_connection:
            with sqlite3.connect(temporary) as destination_connection:
                source_connection.backup(destination_connection)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def main() -> None:
    source = os.environ.get("DATABASE_PATH", "/data/photosarena.sqlite3")
    destination = os.environ.get("PHOTOSARENA_BACKUP_DEST", "")
    if not destination:
        raise SystemExit("PHOTOSARENA_BACKUP_DEST is required.")
    print(f"SQLite backup created at {backup_database(source, destination)}")


if __name__ == "__main__":
    main()
