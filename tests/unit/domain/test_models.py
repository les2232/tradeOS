from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradeos.domain.enums import (
    DataQualityStatus,
    OrderSide,
    StrategyMode,
    TradeAction,
)
from tradeos.domain.models import Bar, Fill, PortfolioState, Position, Signal


def aware_now() -> datetime:
    return datetime(2026, 4, 24, 14, 30, tzinfo=UTC)


def test_valid_bar_creation() -> None:
    bar = Bar(
        symbol="AAPL",
        timestamp=aware_now(),
        open=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.50"),
        close=Decimal("100.50"),
        volume=Decimal("1000"),
        timeframe="1Min",
        data_quality=DataQualityStatus.OK,
    )

    assert bar.symbol == "AAPL"
    assert bar.close == Decimal("100.50")
    assert bar.data_quality == DataQualityStatus.OK


def test_invalid_negative_price_rejected() -> None:
    with pytest.raises(ValueError, match="close cannot be negative"):
        Bar(
            symbol="AAPL",
            timestamp=aware_now(),
            open=Decimal("100.00"),
            high=Decimal("101.00"),
            low=Decimal("99.50"),
            close=Decimal("-1.00"),
            volume=Decimal("1000"),
            timeframe="1Min",
            data_quality=DataQualityStatus.INVALID,
        )


def test_invalid_signal_confidence_rejected() -> None:
    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        Signal(
            symbol="AAPL",
            action=TradeAction.BUY,
            strategy_mode=StrategyMode.MEAN_REVERSION,
            timestamp=aware_now(),
            reason="test signal",
            confidence=Decimal("1.01"),
            metadata={},
        )


def test_valid_hold_signal() -> None:
    signal = Signal(
        symbol="AAPL",
        action=TradeAction.HOLD,
        strategy_mode=StrategyMode.MEAN_REVERSION,
        timestamp=aware_now(),
        reason="no edge",
        confidence=Decimal("0.50"),
        metadata={"source": "unit-test"},
    )

    assert signal.action == TradeAction.HOLD
    assert signal.metadata["source"] == "unit-test"


def test_valid_fill() -> None:
    fill = Fill(
        broker_order_id="order-123",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        fill_price=Decimal("100.25"),
        filled_at=aware_now(),
        raw_payload={"id": "order-123"},
    )

    assert fill.broker_order_id == "order-123"
    assert fill.quantity == Decimal("2")


def test_invalid_negative_quantity_rejected() -> None:
    with pytest.raises(ValueError, match="quantity cannot be negative"):
        Fill(
            broker_order_id="order-123",
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal("-1"),
            fill_price=Decimal("100.25"),
            filled_at=aware_now(),
            raw_payload={},
        )


def test_valid_portfolio_state_with_positions() -> None:
    position = Position(
        symbol="AAPL",
        quantity=Decimal("2"),
        avg_entry_price=Decimal("100.00"),
        market_value=Decimal("205.00"),
        unrealized_pl=Decimal("5.00"),
        source="broker",
    )
    state = PortfolioState(
        cash=Decimal("1000.00"),
        equity=Decimal("1205.00"),
        buying_power=Decimal("2000.00"),
        positions=[position],
        timestamp=aware_now(),
    )

    assert state.positions == [position]
    assert state.equity == Decimal("1205.00")
