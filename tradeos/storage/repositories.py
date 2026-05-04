"""Repository interfaces.

This module will expose explicit persistence operations for orders, fills,
positions, events, and datasets.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class EventRecord:
    """Persisted append-only event record."""

    event_id: str
    event_type: str
    timestamp: str
    symbol: str
    payload: dict[str, Any]


class EventStore(Protocol):
    """Append-only event store interface."""

    def initialize(self) -> None:
        """Create storage resources if needed."""

    def append_event(
        self,
        event_type: str,
        symbol: str,
        payload: dict[str, Any],
    ) -> EventRecord:
        """Append one event and return the persisted record."""

    def list_events(self) -> list[EventRecord]:
        """Return all events in insertion order."""

    def list_events_by_symbol(self, symbol: str) -> list[EventRecord]:
        """Return events for one symbol in insertion order."""
