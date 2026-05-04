from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradeos.domain.enums import OrderSide
from tradeos.domain.models import OrderIntent, PortfolioState, Position
from tradeos.execution.broker import FakeBroker, _alpaca_order_to_intent


def account(positions: list[Position] | None = None) -> PortfolioState:
    return PortfolioState(
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        buying_power=Decimal("10000"),
        positions=positions or [],
        timestamp=datetime(2026, 4, 25, 14, 30, tzinfo=UTC),
    )


def position(symbol: str = "AAPL", quantity: Decimal = Decimal("10")) -> Position:
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=Decimal("100"),
        market_value=quantity * Decimal("100"),
        unrealized_pl=Decimal("0"),
        source="fake_broker",
    )


def intent(
    side: OrderSide,
    *,
    symbol: str = "AAPL",
    quantity: Decimal = Decimal("1"),
    limit_price: Decimal | None = Decimal("100"),
) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type="limit" if limit_price is not None else "market",
        time_in_force="day",
        limit_price=limit_price,
        reason="unit-test",
        metadata={},
    )


def test_fake_broker_returns_account_state() -> None:
    initial = account()
    broker = FakeBroker(initial)

    assert broker.get_account() == initial
    assert broker.get_positions() == []


def test_buy_creates_position_and_reduces_cash() -> None:
    broker = FakeBroker(account())

    broker.submit_order(intent(OrderSide.BUY, quantity=Decimal("2"), limit_price=Decimal("50")))

    state = broker.get_account()
    assert state.cash == Decimal("9900")
    assert state.positions[0].symbol == "AAPL"
    assert state.positions[0].quantity == Decimal("2")


def test_buy_increases_existing_position() -> None:
    broker = FakeBroker(account([position(quantity=Decimal("2"))]))

    broker.submit_order(intent(OrderSide.BUY, quantity=Decimal("3"), limit_price=Decimal("110")))

    state = broker.get_account()
    assert state.positions[0].quantity == Decimal("5")
    assert state.positions[0].avg_entry_price == Decimal("106")
    assert state.cash == Decimal("9670")


def test_sell_reduces_position() -> None:
    broker = FakeBroker(account([position(quantity=Decimal("10"))]))

    broker.submit_order(intent(OrderSide.SELL, quantity=Decimal("4"), limit_price=Decimal("100")))

    state = broker.get_account()
    assert state.cash == Decimal("10400")
    assert state.positions[0].quantity == Decimal("6")


def test_sell_closes_position() -> None:
    broker = FakeBroker(account([position(quantity=Decimal("10"))]))

    broker.submit_order(intent(OrderSide.SELL, quantity=Decimal("10"), limit_price=Decimal("100")))

    assert broker.get_account().cash == Decimal("11000")
    assert broker.get_positions() == []


def test_sell_more_than_owned_raises_value_error() -> None:
    broker = FakeBroker(account([position(quantity=Decimal("10"))]))

    with pytest.raises(ValueError, match="cannot sell more than owned"):
        broker.submit_order(intent(OrderSide.SELL, quantity=Decimal("11")))


def test_submitted_order_returns_fill() -> None:
    broker = FakeBroker(account())

    submission = broker.submit_order(intent(OrderSide.BUY, quantity=Decimal("1")))
    fill = submission.fill

    assert submission.broker_order_id == "fake-order-000001"
    assert submission.status == "filled"
    assert fill is not None
    assert fill.broker_order_id == "fake-order-000001"
    assert fill.symbol == "AAPL"
    assert fill.side == OrderSide.BUY
    assert fill.quantity == Decimal("1")
    assert broker.fills == [fill]
    assert broker.submitted_orders == [intent(OrderSide.BUY, quantity=Decimal("1"))]


def test_broker_order_id_is_deterministic_and_unique_for_tests() -> None:
    broker = FakeBroker(account())

    first = broker.submit_order(intent(OrderSide.BUY, quantity=Decimal("1")))
    second = broker.submit_order(intent(OrderSide.BUY, quantity=Decimal("1")))

    assert first.broker_order_id == "fake-order-000001"
    assert second.broker_order_id == "fake-order-000002"
    assert first.broker_order_id != second.broker_order_id


def test_alpaca_open_order_mapper_accepts_enum_string_side() -> None:
    order = {
        "id": "paper-order-1",
        "symbol": "NVDA",
        "side": "OrderSide.SELL",
        "qty": "3",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "88.25",
    }

    mapped = _alpaca_order_to_intent(order)

    assert mapped.symbol == "NVDA"
    assert mapped.side == OrderSide.SELL
    assert mapped.quantity == Decimal("3")
    assert mapped.limit_price == Decimal("88.25")
    assert mapped.reason == "open_alpaca_paper_order"


def test_alpaca_open_order_mapper_accepts_enum_value_side() -> None:
    class AlpacaSide:
        value = "sell"

    order = {
        "id": "paper-order-2",
        "symbol": "NVDA",
        "side": AlpacaSide(),
        "qty": "1",
    }

    mapped = _alpaca_order_to_intent(order)

    assert mapped.side == OrderSide.SELL
