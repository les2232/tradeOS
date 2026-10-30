from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradeos.domain.enums import DataQualityStatus, StrategyMode, TradeAction
from tradeos.domain.models import Bar, OrderIntent, OrderSubmission, PortfolioState, Position, Signal
from tradeos.execution.broker import FakeBroker
from tradeos.main import main
from tradeos.storage.sqlite import SQLiteEventStore


class StaticMarketData:
    def latest_bars(self, symbols: list[str], timeframe: str) -> list[Bar]:
        return [
            Bar(
                symbol=symbol,
                timestamp=datetime(2026, 4, 27, 14, 30, tzinfo=UTC),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1000"),
                timeframe=timeframe,
                data_quality=DataQualityStatus.OK,
            )
            for symbol in symbols
        ]

    def recent_bars(self, symbols: list[str], timeframe: str, limit: int) -> list[Bar]:
        return [
            Bar(
                symbol=symbol,
                timestamp=datetime(2026, 4, 27, 14, 15, tzinfo=UTC),
                open=Decimal("99"),
                high=Decimal("100"),
                low=Decimal("98"),
                close=Decimal("99"),
                volume=Decimal("1000"),
                timeframe=timeframe,
                data_quality=DataQualityStatus.OK,
            )
            for symbol in symbols
        ]


class AlwaysBuyStrategy:
    name = "always_buy"
    strategy_mode = StrategyMode.MEAN_REVERSION

    def generate_signal(self, bar, config, history=None, current_position=None) -> Signal:
        return Signal(
            symbol=bar.symbol,
            action=TradeAction.BUY,
            strategy_mode=self.strategy_mode,
            timestamp=bar.timestamp,
            reason="buy_test_signal",
            confidence=Decimal("0.5"),
            metadata={},
        )


class SubmitForbiddenBroker(FakeBroker):
    submit_attempts = 0

    def submit_order(self, intent: OrderIntent) -> OrderSubmission:
        self.submit_attempts += 1
        raise AssertionError("--paper-live-once must never submit orders")


def account() -> PortfolioState:
    return PortfolioState(
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        buying_power=Decimal("10000"),
        positions=[],
        timestamp=datetime(2026, 4, 27, 14, 30, tzinfo=UTC),
    )


def account_with_position(position: Position) -> PortfolioState:
    return PortfolioState(
        cash=Decimal("10000"),
        equity=Decimal("10000") + position.market_value,
        buying_power=Decimal("10000"),
        positions=[position],
        timestamp=datetime(2026, 4, 27, 14, 30, tzinfo=UTC),
    )


def test_paper_live_once_runs_with_injected_adapters(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "tradeos.main.AlpacaPaperBroker.from_env",
        staticmethod(lambda **kwargs: FakeBroker(account())),
    )
    monkeypatch.setattr(
        "tradeos.main._paper_market_data_from_env",
        staticmethod(lambda **kwargs: StaticMarketData()),
    )
    monkeypatch.setattr(
        "tradeos.main.SQLiteEventStore",
        lambda path: SQLiteEventStore(":memory:"),
    )

    exit_code = main(["--config", "config/paper.yaml", "--paper-live-once"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "paper live loop complete" in output
    assert "bars received: 3" in output
    assert "warmup bars loaded: 3" in output
    assert "cycles completed: 3" in output
    assert "paper order submission: no" in output


def test_paper_live_once_reports_unmanaged_positions(monkeypatch, capsys) -> None:
    position = Position(
        symbol="COP",
        quantity=Decimal("8"),
        avg_entry_price=Decimal("119.13"),
        market_value=Decimal("953.04"),
        unrealized_pl=Decimal("-20.16"),
        source="alpaca_paper_short",
    )
    monkeypatch.setattr(
        "tradeos.main.AlpacaPaperBroker.from_env",
        staticmethod(lambda **kwargs: FakeBroker(account_with_position(position))),
    )
    monkeypatch.setattr(
        "tradeos.main._paper_market_data_from_env",
        staticmethod(lambda **kwargs: StaticMarketData()),
    )
    monkeypatch.setattr(
        "tradeos.main.SQLiteEventStore",
        lambda path: SQLiteEventStore(":memory:"),
    )

    exit_code = main(["--config", "config/paper.yaml", "--paper-live-once"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "unmanaged positions: COP(outside_config,short)" in output
    assert "paper order submission: no" in output


def test_paper_live_once_never_submits_orders_for_approved_buys(monkeypatch, capsys) -> None:
    broker = SubmitForbiddenBroker(account())
    monkeypatch.setattr(
        "tradeos.main.AlpacaPaperBroker.from_env",
        staticmethod(lambda **kwargs: broker),
    )
    monkeypatch.setattr(
        "tradeos.main._paper_market_data_from_env",
        staticmethod(lambda **kwargs: StaticMarketData()),
    )
    monkeypatch.setattr(
        "tradeos.main.SQLiteEventStore",
        lambda path: SQLiteEventStore(":memory:"),
    )
    monkeypatch.setattr("tradeos.main._strategy_from_config", lambda config: AlwaysBuyStrategy())

    exit_code = main(["--config", "config/paper.yaml", "--paper-live-once"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "paper live loop complete" in output
    assert output.count("risk approved") == 3
    assert output.count("dry_run_order_not_submitted") == 3
    assert "paper order submission: no" in output
    assert broker.submit_attempts == 0
    assert broker.submitted_orders == []


def test_submit_paper_orders_flag_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--config", "config/paper.yaml", "--paper-live-once", "--submit-paper-orders"])

    assert exc.value.code == 2
    assert "unrecognized arguments: --submit-paper-orders" in capsys.readouterr().err


def test_paper_live_once_prints_safety_block(monkeypatch, capsys) -> None:
    class BlockingMarketData:
        def latest_bars(self, symbols: list[str], timeframe: str) -> list[Bar]:
            return []

        def recent_bars(self, symbols: list[str], timeframe: str, limit: int) -> list[Bar]:
            return []

    monkeypatch.setattr(
        "tradeos.main.AlpacaPaperBroker.from_env",
        staticmethod(lambda **kwargs: FakeBroker(account())),
    )
    monkeypatch.setattr(
        "tradeos.main._paper_market_data_from_env",
        staticmethod(lambda **kwargs: BlockingMarketData()),
    )
    monkeypatch.setattr(
        "tradeos.main.SQLiteEventStore",
        lambda path: SQLiteEventStore(":memory:"),
    )

    exit_code = main(["--config", "config/paper.yaml", "--paper-live-once"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "paper live loop blocked:" in output
    assert "paper order submission: no" in output
