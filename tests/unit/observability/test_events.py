from tradeos.observability.events import (
    build_cycle_summaries,
    summarize_latest_cycle,
)
from tradeos.observability.reports import summarize_event_counts
from tradeos.storage.repositories import EventRecord


def event(event_type: str, payload: dict, symbol: str = "AAPL") -> EventRecord:
    return EventRecord(
        event_id=f"{event_type}-{len(str(payload))}",
        event_type=event_type,
        timestamp="2026-04-25T16:00:00+00:00",
        symbol=symbol,
        payload=payload,
    )


def hold_cycle_events(symbol: str = "AAPL") -> list[EventRecord]:
    return [
        event("cycle_started", {"execute_orders": False}, symbol),
        event("reconciliation_result", {"status": "clean", "reason": "positions_match"}, symbol),
        event(
            "signal_generated",
            {"action": "hold", "reason": "mean_reversion_placeholder_no_signal"},
            symbol,
        ),
        event(
            "risk_decision",
            {"status": "blocked", "reason": "hold_signal_no_order"},
            symbol,
        ),
        event(
            "cycle_completed",
            {
                "execution_allowed": False,
                "reason": "hold_signal_no_order",
                "order_created": False,
                "fill_received": False,
            },
            symbol,
        ),
    ]


def dry_run_cycle_events(symbol: str = "AAPL") -> list[EventRecord]:
    return [
        event("cycle_started", {"execute_orders": False}, symbol),
        event("reconciliation_result", {"status": "clean", "reason": "positions_match"}, symbol),
        event("signal_generated", {"action": "buy", "reason": "buy_test_signal"}, symbol),
        event("risk_decision", {"status": "approved", "reason": "buy_signal_approved"}, symbol),
        event("order_intent_created", {"side": "buy", "reason": "buy_signal_approved"}, symbol),
        event(
            "cycle_completed",
            {
                "execution_allowed": True,
                "reason": "dry_run_order_not_submitted",
                "order_created": True,
                "fill_received": False,
            },
            symbol,
        ),
    ]


def executed_cycle_events(symbol: str = "AAPL") -> list[EventRecord]:
    return [
        *dry_run_cycle_events(symbol)[:-1],
        event("fill_received", {"broker_order_id": "fake-order-000001"}, symbol),
        event(
            "cycle_completed",
            {
                "execution_allowed": True,
                "reason": "order_submitted",
                "order_created": True,
                "fill_received": True,
            },
            symbol,
        ),
    ]


def test_builds_summary_from_hold_cycle_events() -> None:
    summary = build_cycle_summaries(hold_cycle_events())[0]

    assert summary.symbol == "AAPL"
    assert summary.signal_action == "hold"
    assert summary.signal_reason == "mean_reversion_placeholder_no_signal"
    assert summary.risk_status == "blocked"
    assert summary.risk_reason == "hold_signal_no_order"
    assert summary.reconciliation_status == "clean"
    assert summary.execution_allowed is False
    assert summary.order_created is False
    assert summary.fill_received is False
    assert summary.cycle_reason == "hold_signal_no_order"


def test_builds_summary_from_approved_dry_run_cycle() -> None:
    summary = build_cycle_summaries(dry_run_cycle_events())[0]

    assert summary.signal_action == "buy"
    assert summary.risk_status == "approved"
    assert summary.execution_allowed is True
    assert summary.order_created is True
    assert summary.fill_received is False
    assert summary.cycle_reason == "dry_run_order_not_submitted"


def test_builds_summary_from_executed_cycle() -> None:
    summary = build_cycle_summaries(executed_cycle_events())[0]

    assert summary.signal_action == "buy"
    assert summary.execution_allowed is True
    assert summary.order_created is True
    assert summary.fill_received is True
    assert summary.cycle_reason == "order_submitted"


def test_latest_cycle_returns_most_recent_completed_cycle() -> None:
    latest = summarize_latest_cycle(
        [
            *hold_cycle_events("AAPL"),
            *executed_cycle_events("MSFT"),
        ]
    )

    assert latest is not None
    assert latest.symbol == "MSFT"
    assert latest.cycle_reason == "order_submitted"


def test_event_counts_are_correct() -> None:
    counts = summarize_event_counts(
        [
            *hold_cycle_events(),
            *dry_run_cycle_events(),
        ]
    )

    assert counts["cycle_started"] == 2
    assert counts["signal_generated"] == 2
    assert counts["risk_decision"] == 2
    assert counts["cycle_completed"] == 2
    assert counts["order_intent_created"] == 1
