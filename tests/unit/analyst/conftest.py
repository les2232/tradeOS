"""Analyst fixtures: a real PaperSupervisor on mocked broker data, and hand-built event logs."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from tradeos.config.loader import load_config
from tradeos.domain.enums import DataQualityStatus, StrategyMode, TradeAction
from tradeos.domain.models import Bar, OrderSubmission, PortfolioState, Signal
from tradeos.research.experiment import ExperimentResult, ExperimentSpec
from tradeos.storage.sqlite import SQLiteEventStore, read_events_readonly
from tradeos.web.paper import PaperSupervisor, fingerprint


@pytest.fixture
def supervised(tmp_path, monkeypatch):
    config = load_config("config/paper.yaml")
    config = replace(config, symbols=["AAPL"], risk=replace(config.risk, entry_start_time="00:00", entry_end_time="23:59", no_entry_after="23:58", force_flatten_at="23:59"))
    monkeypatch.setattr("tradeos.web.paper.load_config", lambda _: config)
    broker, feed = MagicMock(), MagicMock()
    broker.client.get_account.return_value = NS(id="paper-account-123", last_equity="10000", status="ACTIVE", trading_blocked=False, account_blocked=False)
    broker.get_account.return_value = PortfolioState(D(10000), D(10000), D(10000), [], datetime.now(UTC))
    broker.get_open_orders.return_value = []
    timestamp = datetime.now(UTC) - timedelta(minutes=15, seconds=2)
    feed.recent_bars.return_value = [Bar("AAPL", timestamp - timedelta(minutes=15 * i), D(100), D(101), D(99), D(100), D(100), "15Min", DataQualityStatus.OK) for i in range(55)][::-1]
    monkeypatch.setattr(
        "tradeos.web.paper.MeanReversionStrategy.generate_signal",
        lambda self, bar, config, **kw: Signal(bar.symbol, TradeAction.BUY, StrategyMode.MEAN_REVERSION, bar.timestamp, "mean_reversion_entry_threshold_crossed", D(1), {"deviation_pct": "-0.012", "entry_threshold_pct": "0.01", "exit_threshold_pct": "0.003", "sma": "101.2"}),
    )
    broker.client.get_clock.return_value = NS(is_open=True, next_close=datetime.now(UTC) + timedelta(hours=6))
    broker.client.get_order_by_client_id.return_value = NS(id="broker-1", filled_qty="10", filled_avg_price="100", status="filled")
    broker.submit_order.return_value = OrderSubmission("broker-1", "AAPL", "buy", D(10), "submitted", datetime.now(UTC))
    supervisor = PaperSupervisor("unused", tmp_path / "paper.sqlite3", lambda _: (broker, feed))
    supervisor._pause = lambda stop, seconds: stop.is_set()
    yield supervisor, broker
    if supervisor.process_lock is not None:
        supervisor.process_lock.close()


def run_session(supervisor, cycles=2):
    supervisor.check()
    preview = supervisor.session_preview(5, cycles, 60)
    supervisor.start(5, cycles, 60, preview["fingerprint"])
    supervisor.thread.join(timeout=10)
    assert not supervisor.active


# --- Hand-built event logs -------------------------------------------------


STRATEGY = {
    "strategy_mode": "mean_reversion", "sma_bars": 20, "entry_threshold_pct": "0.01", "exit_threshold_pct": "0.003",
    "trend_filter_enabled": True, "max_atr_percentile": "80", "breakout_sma_bars": 20, "breakout_threshold_pct": "0.002",
    "momentum_exit_threshold_pct": "0.001", "momentum_stop_loss_pct": "0.01", "require_positive_sma_slope": True,
}


def snapshot(**overrides):
    value = {"label": "Automatic PAPER session", "account_id": "acct-secret-9", "watchlist": ["AAPL", "MSFT"],
             "timeframe": "15Min", "strategy": dict(STRATEGY), "interval_seconds": 60, "max_evaluations": 5}
    value.update(overrides)
    return value


def session_start(session_id="s-1", snap=None, confirmed=None):
    snap = snap or snapshot()
    return ("paper_session_started", "", {"session_id": session_id, "fingerprint": confirmed or fingerprint(snap), "session": snap,
                                          "deadline": "2026-11-02T20:00:00+00:00", "deadline_reason": "market close"})


def session_end(state="stopped", reason="Session reached its time limit"):
    return ("paper_session_ended", "", {"state": state, "ended_reason": reason, "completed": 2, "limit": 5, "session_config": snapshot()})


def cycle(symbol, action, reason, *, bar="2026-11-02T15:00:00+00:00", risk="approved", risk_reason=None, rules=(),
          recon="clean", order=False, metadata=None, with_inputs=True):
    started = {"timeframe": "15Min", "execute_orders": False}
    signal = {"action": action, "reason": reason, "strategy_mode": "mean_reversion", "confidence": "1"}
    if with_inputs:
        started["bar_timestamp"] = bar
        signal["metadata"] = metadata if metadata is not None else {"deviation_pct": "-0.004", "entry_threshold_pct": "0.01", "exit_threshold_pct": "0.003"}
    events = [
        ("cycle_started", symbol, started),
        ("reconciliation_result", symbol, {"status": recon, "execution_allowed": recon == "clean", "reason": recon, "issue_count": 0}),
        ("signal_generated", symbol, signal),
        ("risk_decision", symbol, {"status": risk, "action": action, "reason": risk_reason or f"{action}_signal_{risk}", "blocked_rules": list(rules)}),
    ]
    if order:
        events.append(("order_intent_created", symbol, {"side": action, "quantity": "10", "order_type": "limit", "reason": "approved"}))
    events.append(("cycle_completed", symbol, {"execution_allowed": order, "reason": "dry_run_order_not_submitted" if order else "no_order_for_signal",
                                               "order_created": order, "fill_received": False}))
    return events


def order(client_id, symbol, side, filled_qty="10", price="100", status="filled", submission="accepted"):
    attempt = ("paper_submission_attempt", symbol, {"symbol": symbol, "side": side, "submission": "claimed", "status": "not_submitted",
                                                    "filled_qty": "0", "client_order_id": client_id})
    response = ("paper_submission_response", symbol, {"broker_order_id": f"b-{client_id}", "symbol": symbol, "side": side,
                                                      "requested_quantity": filled_qty, "status": "submitted"})
    observed = observe(client_id, symbol, side, filled_qty, price, status, submission)
    return attempt, response, observed


def observe(client_id, symbol, side, filled_qty="10", price="100", status="filled", submission="accepted"):
    return ("paper_order_observed", symbol, {"symbol": symbol, "side": side, "submission": submission, "status": status,
                                             "filled_qty": filled_qty, "filled_avg_price": price, "client_order_id": client_id,
                                             "broker_order_id": f"b-{client_id}"})


EVALUATION = ("paper_evaluation", "", {"state": "running"})


def write_log(path, entries):
    store = SQLiteEventStore(path)
    for event_type, symbol, payload in entries:
        store.append_event(event_type, symbol, payload)
    return path


def events_of(path):
    return read_events_readonly(path)


def experiment(*, parameters=None, dataset="aapl_msft_2026_iex.csv", trades=40, symbols=("AAPL", "MSFT"), mode="mean_reversion", experiment_id="exp-1"):
    params = {key: value for key, value in STRATEGY.items() if key != "strategy_mode"}
    params.update(parameters or {})
    spec = ExperimentSpec(StrategyMode(mode), list(symbols), date(2026, 1, 2), date(2026, 6, 30), "15Min", params, dataset)
    return ExperimentResult(experiment_id, spec, D("1.4"), D("120"), D("0.55"), trades, D("2.10"), D("84.00"), D("40.00"), ["low_trade_count"] if trades < 30 else [])
