from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradeos.domain.enums import DataQualityStatus
from tradeos.domain.models import Bar, PortfolioState, Position
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
        "tradeos.main.AlpacaLatestBarFeed.from_env",
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
        "tradeos.main.AlpacaLatestBarFeed.from_env",
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


def test_submit_paper_orders_blocks_unmanaged_positions(monkeypatch, capsys) -> None:
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
        "tradeos.main.AlpacaLatestBarFeed.from_env",
        staticmethod(lambda **kwargs: StaticMarketData()),
    )
    monkeypatch.setattr(
        "tradeos.main.SQLiteEventStore",
        lambda path: SQLiteEventStore(":memory:"),
    )

    exit_code = main(["--config", "config/paper.yaml", "--paper-live-once", "--submit-paper-orders"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "paper live loop blocked: paper order submission blocked by unmanaged positions" in output
    assert "COP(outside_config,short)" in output
    assert "paper order submission: no" in output


def test_submit_paper_orders_requires_paper_live_once() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--config", "config/paper.yaml", "--dry-run-once", "--submit-paper-orders"])

    assert exc.value.code == 2


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
        "tradeos.main.AlpacaLatestBarFeed.from_env",
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
