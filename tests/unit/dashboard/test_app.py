from pathlib import Path
from dataclasses import dataclass
from decimal import Decimal

from tradeos.dashboard.app import (
    auto_interval_seconds,
    command_args,
    credential_source,
    cycle_decisions,
    dashboard_summary,
    load_paper_credentials,
    missing_credentials,
    paper_account_snapshot,
    run_state,
)
from tradeos.domain.enums import OrderSide
from tradeos.domain.models import OrderIntent, Position


FIXTURE_CREDENTIALS = "tests/fixtures/paper_credentials.env"


@dataclass
class FakeBroker:
    positions: list[Position]
    open_orders: list[OrderIntent]

    def get_positions(self) -> list[Position]:
        return self.positions

    def get_open_orders(self) -> list[OrderIntent]:
        return self.open_orders


def test_command_args_runs_paper_live_without_submit_by_default() -> None:
    assert command_args("config/momentum_test.yaml") == [
        "--config",
        "config/momentum_test.yaml",
        "--paper-live-once",
    ]


def test_command_args_can_enable_paper_submit() -> None:
    assert command_args("config/momentum_test.yaml", submit_paper_orders=True) == [
        "--config",
        "config/momentum_test.yaml",
        "--paper-live-once",
        "--submit-paper-orders",
    ]


def test_auto_interval_seconds_accepts_whole_minutes() -> None:
    assert auto_interval_seconds("15") == 900


def test_auto_interval_seconds_rejects_invalid_values() -> None:
    for value in ["", "0", "391", "1.5"]:
        try:
            auto_interval_seconds(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid auto interval: {value}")


def test_run_state_identifies_blocked_data_gate() -> None:
    assert run_state(0, "paper live loop blocked: stale bars") == "Blocked"


def test_run_state_identifies_review_signal_approval_without_submit() -> None:
    assert (
        run_state(
            0,
            "paper order submission: no\n"
            "  NVDA: buy | bar 2026-04-27T19:59:00+00:00 | risk approved | "
            "signal momentum_breakout_entry | rules none | buy_signal_approved\n",
        )
        == "Signal Approved"
    )


def test_run_state_identifies_executable_paper_when_submit_enabled() -> None:
    assert (
        run_state(
            0,
            "paper order submission: yes\n"
            "  NVDA: buy | bar 2026-04-27T19:59:00+00:00 | risk approved | "
            "signal momentum_breakout_entry | rules none | "
            "reconciliation clean | execution filled | order_filled\n",
        )
        == "Executable Paper"
    )


def test_dashboard_summary_identifies_unaligned_data_block() -> None:
    summary = dashboard_summary(
        0,
        "paper live loop blocked: paper live loop blocked by unaligned latest bars: AAPL age=1200s\n"
        "paper order submission: no\n",
    )

    assert summary.run_state == "Blocked"
    assert summary.data_gate == "Blocked"
    assert summary.reconciliation == "Not evaluated"
    assert summary.signal == "Not evaluated"
    assert summary.risk == "Not evaluated"
    assert summary.execution == "Blocked"
    assert summary.order_mode == "No submit"
    assert summary.latest_title == "Data gate blocked evaluation"
    assert summary.latest_detail.startswith("BLOCKED:")


def test_dashboard_summary_prioritizes_blocked_buy_signal() -> None:
    summary = dashboard_summary(
        0,
        "paper live loop complete\n"
        "paper order submission: no\n"
        "cycle results:\n"
        "  AAPL: hold | bar 2026-04-27T19:59:00+00:00 | risk blocked | "
        "signal momentum_no_signal | rules none | hold_signal_no_order\n"
        "  NVDA: buy | bar 2026-04-27T19:59:00+00:00 | risk blocked | "
        "signal momentum_breakout_entry | rules entry_time_window | "
        "buy_blocked_outside_entry_window\n",
    )

    assert summary.run_state == "Complete"
    assert summary.data_gate == "Aligned"
    assert summary.reconciliation == "Unknown"
    assert summary.signal == "Buy signal"
    assert summary.risk == "Blocked"
    assert summary.execution == "Unknown"
    assert summary.latest_title == "NVDA buy signal blocked"


def test_dashboard_summary_identifies_approved_buy_signal_without_submit_as_review() -> None:
    summary = dashboard_summary(
        0,
        "paper live loop complete\n"
        "paper order submission: no\n"
        "  NVDA: buy | bar 2026-04-27T19:59:00+00:00 | risk approved | "
        "signal momentum_breakout_entry | rules none | buy_signal_approved\n",
    )

    assert summary.run_state == "Signal Approved"
    assert summary.signal == "Buy signal"
    assert summary.risk == "Approved"
    assert summary.order_mode == "No submit"
    assert summary.latest_title == "NVDA buy signal approved for review"


def test_dashboard_summary_identifies_filled_execution_distinctly() -> None:
    summary = dashboard_summary(
        0,
        "paper live loop complete\n"
        "paper order submission: yes\n"
        "  NVDA: buy | bar 2026-04-27T19:59:00+00:00 | risk approved | "
        "signal momentum_breakout_entry | rules none | "
        "reconciliation clean | execution filled | order_filled\n",
    )

    assert summary.run_state == "Executable Paper"
    assert summary.reconciliation == "Clean"
    assert summary.execution == "Filled"
    assert summary.order_mode == "Paper submit enabled"
    assert summary.latest_title == "NVDA buy signal approved for paper submit"


def test_dashboard_summary_identifies_submitted_not_filled_execution() -> None:
    summary = dashboard_summary(
        0,
        "paper live loop complete\n"
        "paper order submission: yes\n"
        "  NVDA: buy | bar 2026-04-27T19:59:00+00:00 | risk approved | "
        "signal momentum_breakout_entry | rules none | "
        "reconciliation clean | execution submitted | order_submitted\n",
    )

    assert summary.execution == "Submitted"


def test_missing_credentials_reports_each_missing_value() -> None:
    assert missing_credentials("", "") == ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"]
    assert missing_credentials("key", "") == ["ALPACA_SECRET_KEY"]
    assert missing_credentials("key", "secret") == []


def test_load_paper_credentials_uses_environment_first() -> None:
    credentials = load_paper_credentials(
        env_file=Path(FIXTURE_CREDENTIALS),
        environment={
            "ALPACA_API_KEY": "env-key",
            "ALPACA_SECRET_KEY": "env-secret",
        },
    )

    assert credentials == {
        "ALPACA_API_KEY": "env-key",
        "ALPACA_SECRET_KEY": "env-secret",
    }


def test_load_paper_credentials_falls_back_to_dotenv() -> None:
    credentials = load_paper_credentials(
        env_file=Path(FIXTURE_CREDENTIALS),
        environment={},
    )

    assert credentials == {
        "ALPACA_API_KEY": "file-key",
        "ALPACA_SECRET_KEY": "file-secret",
    }


def test_credential_source_reports_dotenv() -> None:
    assert (
        credential_source(env_file=Path(FIXTURE_CREDENTIALS), environment={})
        == "Loaded from paper_credentials.env"
    )


def test_paper_account_snapshot_formats_positions_and_orders() -> None:
    broker = FakeBroker(
        positions=[
            Position(
                symbol="NVDA",
                quantity=Decimal("4.741358873"),
                avg_entry_price=Decimal("210.902655"),
                market_value=Decimal("990.707411"),
                unrealized_pl=Decimal("-9.257764"),
                source="alpaca_paper",
            )
        ],
        open_orders=[
            OrderIntent(
                symbol="AAPL",
                side=OrderSide.BUY,
                quantity=Decimal("2.5"),
                order_type="limit",
                time_in_force="day",
                limit_price=Decimal("190.12"),
                reason="buy_signal_approved",
            )
        ],
    )

    snapshot = paper_account_snapshot(
        {"ALPACA_API_KEY": "key", "ALPACA_SECRET_KEY": "secret"},
        broker_factory=lambda: broker,
    )

    assert snapshot.status == "Account refreshed"
    assert snapshot.positions == [("NVDA", "4.7414", "$210.90", "$990.71", "-$9.26")]
    assert snapshot.open_orders == [("AAPL", "BUY", "2.5", "$190.12", "Buy approved")]


def test_cycle_decisions_parses_symbol_rows() -> None:
    decisions = cycle_decisions(
        "  AAPL: hold | bar 2026-04-27T19:59:00+00:00 | risk blocked | "
        "signal momentum_no_signal | rules none | hold_signal_no_order\n"
        "  NVDA: buy | bar 2026-04-27T19:59:00+00:00 | risk blocked | "
        "signal momentum_breakout_entry | rules entry_time_window | "
        "buy_blocked_outside_entry_window\n"
    )

    assert [decision.symbol for decision in decisions] == ["AAPL", "NVDA"]
    assert decisions[1].action == "buy"
    assert decisions[1].risk == "Blocked"
    assert decisions[1].signal == "momentum_breakout_entry"
    assert decisions[1].rules == "entry_time_window"
    assert decisions[1].reconciliation == "Unknown"
    assert decisions[1].execution == "Unknown"


def test_cycle_decisions_parses_reconciliation_and_execution_fields() -> None:
    decisions = cycle_decisions(
        "  AAPL: hold | bar 2026-04-27T19:59:00+00:00 | risk blocked | "
        "signal momentum_no_signal | rules none | "
        "reconciliation dirty | execution blocked | position_reconciliation_dirty\n"
    )

    assert decisions[0].reconciliation == "Dirty / Blocked"
    assert decisions[0].execution == "No order"
    assert decisions[0].reason == "position_reconciliation_dirty"


def test_dashboard_summary_surfaces_reconciliation_clean() -> None:
    summary = dashboard_summary(
        0,
        "paper live loop complete\n"
        "paper order submission: no\n"
        "  AAPL: hold | bar 2026-04-27T19:59:00+00:00 | risk blocked | "
        "signal momentum_no_signal | rules none | "
        "reconciliation clean | execution blocked | hold_signal_no_order\n",
    )

    assert summary.reconciliation == "Clean"
    assert summary.execution == "No order"


def test_dashboard_copy_is_bounded_paper_and_no_real_money() -> None:
    import tradeos.dashboard.app as app

    source = app.Path(app.__file__).read_text(encoding="utf-8")
    assert "Evaluate bounded paper-data signals" in source
    assert "No real-money live trading command exists" in source
    assert "Auto Paper Submit" in source
    assert "API key" not in source
    assert "Evaluate live signals" not in source
