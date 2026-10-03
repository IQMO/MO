"""Shared connection policy for concurrent private-state SQLite stores."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_state_db(path: str | Path, *, foreign_keys: bool = False) -> sqlite3.Connection:
    """Open a row-addressable WAL connection with MO's state-store timeout."""
    connection = sqlite3.connect(path, timeout=10.0, isolation_level="DEFERRED")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys=ON")
    return connection
