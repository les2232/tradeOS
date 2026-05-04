"""Event definitions.

This module will define typed operational and trading events used for audit,
debugging, and reporting.
"""

from dataclasses import dataclass
from collections.abc import Iterable

from tradeos.storage.repositories import EventRecord


@dataclass(frozen=True)
class CycleSummary:
    """Read-only dashboard summary for one completed trading cycle."""

    symbol: str
    signal_action: str | None
    signal_reason: str | None
    risk_status: str | None
    risk_reason: str | None
    reconciliation_status: str | None
    execution_allowed: bool
    order_created: bool
    fill_received: bool
    cycle_reason: str


def build_cycle_summaries(events: Iterable[EventRecord]) -> list[CycleSummary]:
    """Build summaries from ordered EventStore records."""

    summaries: list[CycleSummary] = []
    current: dict[str, EventRecord] = {}

    for event in events:
        if event.event_type == "cycle_started":
            current = {"cycle_started": event}
            continue

        if not current:
            current = {}

        current[event.event_type] = event
        if event.event_type == "cycle_completed":
            summaries.append(_summary_from_events(current))
            current = {}

    return summaries


def summarize_latest_cycle(events: Iterable[EventRecord]) -> CycleSummary | None:
    """Return the most recent completed cycle summary."""

    summaries = build_cycle_summaries(events)
    if not summaries:
        return None
    return summaries[-1]


def _summary_from_events(events: dict[str, EventRecord]) -> CycleSummary:
    completed = events["cycle_completed"]
    signal = events.get("signal_generated")
    risk = events.get("risk_decision")
    reconciliation = events.get("reconciliation_result")

    return CycleSummary(
        symbol=completed.symbol,
        signal_action=_payload_value(signal, "action"),
        signal_reason=_payload_value(signal, "reason"),
        risk_status=_payload_value(risk, "status"),
        risk_reason=_payload_value(risk, "reason"),
        reconciliation_status=_payload_value(reconciliation, "status"),
        execution_allowed=bool(completed.payload.get("execution_allowed", False)),
        order_created=bool(completed.payload.get("order_created", False)),
        fill_received=bool(completed.payload.get("fill_received", False)),
        cycle_reason=str(completed.payload.get("reason", "")),
    )


def _payload_value(event: EventRecord | None, key: str) -> str | None:
    if event is None:
        return None
    value = event.payload.get(key)
    if value is None:
        return None
    return str(value)
