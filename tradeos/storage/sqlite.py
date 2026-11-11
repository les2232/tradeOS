"""SQLite storage adapter.

This module will contain SQLite connection and migration boundaries for local
state once persistence requirements are defined.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from tradeos.storage.repositories import EventRecord


class SQLiteEventStore:
    """Simple append-only SQLite store for cycle events."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Create the event table if it does not already exist."""

        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def append_event(
        self,
        event_type: str,
        symbol: str,
        payload: dict[str, Any],
    ) -> EventRecord:
        """Append an event and return the persisted record."""

        self.initialize()
        record = EventRecord(
            event_id=str(uuid4()),
            event_type=event_type,
            timestamp=datetime.now(UTC).isoformat(),
            symbol=symbol,
            payload=dict(payload),
        )
        payload_json = json.dumps(record.payload, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events (
                    event_id,
                    event_type,
                    timestamp,
                    symbol,
                    payload
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    record.event_type,
                    record.timestamp,
                    record.symbol,
                    payload_json,
                ),
            )
        return record

    def list_events(self) -> list[EventRecord]:
        """Return all events in insertion order."""

        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, event_type, timestamp, symbol, payload
                FROM events
                ORDER BY sequence ASC
                """
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def list_events_by_symbol(self, symbol: str) -> list[EventRecord]:
        """Return events for one symbol in insertion order."""

        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, event_type, timestamp, symbol, payload
                FROM events
                WHERE symbol = ?
                ORDER BY sequence ASC
                """,
                (symbol,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
            return _NonClosingConnection(self._memory_connection)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def read_events_readonly(db_path: str | Path) -> list[tuple[int, EventRecord]]:
    """Return (sequence, event) pairs without creating, migrating or locking the store.

    The database is opened read-only so analysis can run beside a live paper
    session. A missing file or a database without an events table yields [].
    """

    path = Path(db_path)
    if not path.exists():
        return []
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT sequence, event_id, event_type, timestamp, symbol, payload
            FROM events
            ORDER BY sequence ASC
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        connection.close()
    return [(int(row["sequence"]), _row_to_event(row)) for row in rows]


def _row_to_event(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=row["event_id"],
        event_type=row["event_type"],
        timestamp=row["timestamp"],
        symbol=row["symbol"],
        payload=json.loads(row["payload"]),
    )


class _NonClosingConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
