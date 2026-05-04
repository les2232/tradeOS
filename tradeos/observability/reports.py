"""Reporting utilities.

This module will produce human-readable and machine-readable summaries for
backtests, live runs, and reconciliation outcomes.
"""

from collections.abc import Iterable

from tradeos.storage.repositories import EventRecord


def summarize_event_counts(events: Iterable[EventRecord]) -> dict[str, int]:
    """Count events by event type."""

    counts: dict[str, int] = {}
    for event in events:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    return counts
