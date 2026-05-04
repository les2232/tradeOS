from datetime import datetime
import sqlite3

from tradeos.storage.sqlite import SQLiteEventStore


def test_initialize_creates_database_table() -> None:
    store = SQLiteEventStore(":memory:")
    store.initialize()

    with store._connect() as connection:
        table_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'events'"
        ).fetchone()[0]
    assert table_count == 1


def test_append_event_stores_event() -> None:
    store = SQLiteEventStore(":memory:")
    event = store.append_event("cycle_completed", "AAPL", {"reason": "test"})

    events = store.list_events()
    assert events == [event]


def test_list_events_returns_appended_events() -> None:
    store = SQLiteEventStore(":memory:")
    first = store.append_event("signal", "AAPL", {"value": 1})
    second = store.append_event("risk", "MSFT", {"value": 2})

    assert store.list_events() == [first, second]


def test_list_events_by_symbol_filters_correctly() -> None:
    store = SQLiteEventStore(":memory:")
    aapl = store.append_event("signal", "AAPL", {"value": 1})
    store.append_event("signal", "MSFT", {"value": 2})

    assert store.list_events_by_symbol("AAPL") == [aapl]


def test_multiple_events_preserve_insertion_order() -> None:
    store = SQLiteEventStore(":memory:")
    for index in range(5):
        store.append_event("event", "AAPL", {"index": index})

    assert [event.payload["index"] for event in store.list_events()] == [0, 1, 2, 3, 4]


def test_payload_round_trips_correctly() -> None:
    store = SQLiteEventStore(":memory:")
    payload = {"nested": {"approved": True}, "values": [1, 2, 3]}
    store.append_event("cycle", "NVDA", payload)

    assert store.list_events()[0].payload == payload


def test_event_timestamp_is_present() -> None:
    store = SQLiteEventStore(":memory:")
    event = store.append_event("cycle", "AAPL", {})

    parsed = datetime.fromisoformat(event.timestamp)
    assert parsed.tzinfo is not None


def test_event_id_is_unique() -> None:
    store = SQLiteEventStore(":memory:")
    first = store.append_event("cycle", "AAPL", {})
    second = store.append_event("cycle", "AAPL", {})

    assert first.event_id != second.event_id
