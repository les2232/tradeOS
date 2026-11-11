"""Split an append-only event log into paper sessions.

A supervised paper session is bracketed by ``paper_session_started`` and
``paper_session_ended`` events. Everything else (manual checks, CLI passes)
falls outside any session and is reported as unsessioned activity.
"""

from dataclasses import dataclass, field
from typing import Any

from tradeos.storage.repositories import EventRecord


SequencedEvent = tuple[int, EventRecord]


def ref(sequence: int) -> str:
    """Citation id for an event: its sequence number in the event store."""

    return f"E{sequence}"


@dataclass
class SessionWindow:
    """Events belonging to one paper session, or to unsessioned activity."""

    session_id: str | None
    events: list[SequencedEvent] = field(default_factory=list)
    start_event: SequencedEvent | None = None
    end_event: SequencedEvent | None = None
    # True when a new session started before this one recorded its end.
    superseded_without_end: bool = False

    @property
    def is_session(self) -> bool:
        return self.session_id is not None

    def summary(self) -> dict[str, Any]:
        start_payload = self.start_event[1].payload if self.start_event else {}
        config = start_payload.get("session") or {}
        end_payload = self.end_event[1].payload if self.end_event else {}
        return {
            "session_id": self.session_id,
            "started_at": self.start_event[1].timestamp if self.start_event else None,
            "ended_at": self.end_event[1].timestamp if self.end_event else None,
            "end_state": end_payload.get("state") if self.end_event else None,
            "ended_reason": end_payload.get("ended_reason") if self.end_event else None,
            "strategy": (config.get("strategy") or {}).get("strategy_mode"),
            "watchlist": config.get("watchlist"),
            "event_count": len(self.events),
            "first_ref": ref(self.events[0][0]) if self.events else None,
            "last_ref": ref(self.events[-1][0]) if self.events else None,
            "recorded_end": self.end_event is not None,
        }


def split_sessions(events: list[SequencedEvent]) -> tuple[list[SessionWindow], SessionWindow]:
    """Return (sessions in order, unsessioned activity)."""

    sessions: list[SessionWindow] = []
    unsessioned = SessionWindow(session_id=None)
    current: SessionWindow | None = None

    for item in events:
        event = item[1]
        if event.event_type == "paper_session_started":
            if current is not None:
                current.superseded_without_end = True
            current = SessionWindow(
                session_id=str(event.payload.get("session_id") or f"unknown-{item[0]}"),
                events=[item],
                start_event=item,
            )
            sessions.append(current)
            continue
        if current is None:
            unsessioned.events.append(item)
            continue
        current.events.append(item)
        if event.event_type == "paper_session_ended":
            current.end_event = item
            current = None

    return sessions, unsessioned


def list_sessions(events: list[SequencedEvent]) -> list[dict[str, Any]]:
    """Summaries of every recorded session, newest first."""

    sessions, _ = split_sessions(events)
    return [window.summary() for window in reversed(sessions)]


def select_window(events: list[SequencedEvent], session_id: str | None) -> SessionWindow:
    """Pick a session by id, ``"latest"``, or ``"unsessioned"``.

    ``None`` means latest session, falling back to all events when the log
    holds no session markers (for example CLI paper passes).
    """

    sessions, unsessioned = split_sessions(events)
    if session_id in (None, "latest"):
        if sessions:
            return sessions[-1]
        return SessionWindow(session_id=None, events=list(events))
    if session_id == "unsessioned":
        return unsessioned
    for window in sessions:
        if window.session_id == session_id:
            return window
    raise ValueError(f"Session {session_id} is not in this event log")
