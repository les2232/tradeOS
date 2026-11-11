import json

import pytest

from tradeos.analyst.evidence import build_evidence, collect_refs
from tradeos.analyst.sessions import list_sessions
from tradeos.web.paper import fingerprint

from tests.unit.analyst.conftest import (
    EVALUATION, cycle, events_of, observe, order, run_session, session_end, session_start, snapshot, write_log,
)


def codes(evidence):
    return {item["code"] for item in evidence["limitations"]}


def test_round_trip_pnl_identity_and_redaction(tmp_path):
    path = write_log(tmp_path / "e.sqlite3", [
        session_start(),
        *cycle("AAPL", "buy", "mean_reversion_entry_threshold_crossed", order=True),
        *order("c-1", "AAPL", "buy", "10", "100"),
        EVALUATION,
        *cycle("AAPL", "sell", "mean_reversion_exit_threshold_crossed", bar="2026-11-02T15:15:00+00:00", order=True),
        *order("c-2", "AAPL", "sell", "10", "103"),
        EVALUATION,
        session_end(),
    ])
    evidence = build_evidence(events_of(path))

    assert evidence["identity"]["scope"] == "session"
    assert evidence["identity"]["config_fingerprint_verified"] is True
    assert evidence["identity"]["config"]["account_id"] == "redacted"
    assert "acct-secret-9" not in json.dumps(evidence)
    metrics = evidence["metrics"]
    assert metrics["decisions"] == 2
    assert metrics["signals"] == {"buy": 1, "sell": 1}
    assert metrics["fills"] == 2
    assert metrics["closed_trades"] == 1
    assert metrics["realized_pnl_from_matched_fills"] == "30.00"
    assert metrics["win_rate"] == "1.0000"
    assert evidence["trades"][0]["entry_refs"] and evidence["trades"][0]["exit_ref"]
    assert {"small_sample", "pnl_excludes_fees"} <= codes(evidence)
    assert "session_not_ended" not in codes(evidence)
    # Every ref in the package points at a real event.
    sequences = {f"E{seq}" for seq, _ in events_of(path)}
    assert collect_refs(evidence) <= sequences


def test_tampered_session_settings_are_flagged(tmp_path):
    original = snapshot()
    path = write_log(tmp_path / "e.sqlite3", [
        session_start(snap=snapshot(max_evaluations=99), confirmed=fingerprint(original)),
        session_end(),
    ])
    evidence = build_evidence(events_of(path))
    assert evidence["identity"]["config_fingerprint_verified"] is False
    assert "config_fingerprint_mismatch" in codes(evidence)


def test_sell_without_buy_in_scope_is_not_guessed(tmp_path):
    path = write_log(tmp_path / "e.sqlite3", [
        session_start(),
        *cycle("AAPL", "sell", "stop_loss_triggered", order=True),
        *order("c-9", "AAPL", "sell", "5", "98"),
        EVALUATION,
        session_end(),
    ])
    evidence = build_evidence(events_of(path))
    assert evidence["trades"] == []
    assert evidence["metrics"]["realized_pnl_from_matched_fills"] is None
    assert "unmatched_sells" in codes(evidence)
    assert any(item["type"] == "protective_signal" for item in evidence["notable_events"])


def test_open_session_and_selection(tmp_path):
    path = write_log(tmp_path / "e.sqlite3", [
        *cycle("AAPL", "hold", "mean_reversion_no_signal"),
        session_start("old"), session_end(),
        session_start("new"),
        *cycle("AAPL", "hold", "mean_reversion_no_signal"),
    ])
    events = events_of(path)
    assert [item["session_id"] for item in list_sessions(events)] == ["new", "old"]
    latest = build_evidence(events)
    assert latest["identity"]["session_id"] == "new"
    assert "session_not_ended" in codes(latest)
    assert build_evidence(events, session_id="old")["metrics"]["decisions"] == 0
    assert build_evidence(events, session_id="unsessioned")["metrics"]["decisions"] == 1
    with pytest.raises(ValueError, match="not in this event log"):
        build_evidence(events, session_id="missing")


def test_cli_log_without_markers_or_inputs(tmp_path):
    path = write_log(tmp_path / "e.sqlite3", [
        *cycle("AAPL", "hold", "mean_reversion_no_signal", with_inputs=False),
        *cycle("MSFT", "hold", "trend_filter_rejected_buy", with_inputs=False),
    ])
    evidence = build_evidence(events_of(path), symbol="aapl")
    assert evidence["identity"]["scope"] == "unsessioned"
    assert evidence["metrics"]["decisions"] == 2
    assert evidence["metrics"]["distinct_bars_evaluated"] is None
    assert {"no_session_markers", "missing_bar_timestamps", "missing_signal_inputs"} <= codes(evidence)
    assert evidence["symbol_detail"]["symbol"] == "AAPL"
    assert evidence["symbol_detail"]["in_watchlist"] is None


def test_pre_submission_recheck_counts_once(tmp_path):
    path = write_log(tmp_path / "e.sqlite3", [
        session_start(),
        *cycle("AAPL", "buy", "mean_reversion_entry_threshold_crossed", order=True),
        *cycle("AAPL", "buy", "mean_reversion_entry_threshold_crossed", order=True),
        EVALUATION,
        *cycle("AAPL", "hold", "mean_reversion_no_signal"),
        EVALUATION,
        session_end(),
    ])
    metrics = build_evidence(events_of(path))["metrics"]
    assert metrics["cycle_runs"] == 3
    assert metrics["decisions"] == 2
    assert metrics["distinct_bars_evaluated"] == 1


def test_fill_observed_after_session_and_uncertain_orders(tmp_path):
    attempt, response, _ = order("c-1", "AAPL", "buy")
    uncertain_attempt, _, _ = order("c-2", "AAPL", "buy")
    path = write_log(tmp_path / "e.sqlite3", [
        session_start(),
        attempt, response,
        observe("c-1", "AAPL", "buy", "0", "None", "new"),
        uncertain_attempt,
        observe("c-2", "AAPL", "buy", "0", "None", "new", submission="submission_uncertain"),
        session_end(),
        observe("c-1", "AAPL", "buy", "4", "100.5", "partially_filled"),
    ])
    evidence = build_evidence(events_of(path))
    orders = [item for item in evidence["notable_events"] if item["type"] == "paper_order"]
    assert orders[0]["detail"]["filled_qty"] == "4"
    assert evidence["metrics"]["fills"] == 1
    assert evidence["open_positions_from_session_fills"][0]["quantity"] == "4"
    assert "uncertain_submissions" in codes(evidence)


def test_real_supervised_session_log(supervised):
    supervisor, _ = supervised
    run_session(supervisor, cycles=2)
    events = events_of(supervisor.path)
    evidence = build_evidence(events, symbol="AAPL")

    assert evidence["identity"]["config_fingerprint_verified"] is True
    assert "paper-account-123" not in json.dumps(evidence)
    assert evidence["metrics"]["cycle_runs"] == 2
    assert evidence["metrics"]["decisions"] == 1
    assert evidence["metrics"]["paper_submission_attempts"] == 1
    assert evidence["metrics"]["fills"] == 1
    # The mocked broker never shows the filled position, so the next check is dirty and blocks.
    assert evidence["identity"]["end_state"] == "blocked"
    assert "dirty" in evidence["blocks"][0]["reason"]
    row = evidence["symbol_detail"]["recent_decisions"][0]
    assert row["bar_timestamp"]
    assert row["strategy_inputs"]["deviation_pct"] == "-0.012"
    assert collect_refs(evidence) <= {f"E{seq}" for seq, _ in events}
