"""Broker interface boundary.

This module will define broker-facing contracts and treat broker state as the
source of truth.
"""

import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from typing import Protocol

from tradeos.domain.enums import OrderSide, OrderStatus
from tradeos.domain.models import Fill, OrderIntent, OrderSubmission, PortfolioState, Position


class BrokerSafetyError(ValueError):
    """Raised when a broker adapter would violate a safety contract."""


class Broker(Protocol):
    """Broker boundary used by execution components."""

    def get_positions(self) -> list[Position]:
        """Return current broker positions."""

    def get_account(self) -> PortfolioState:
        """Return the current broker account snapshot."""

    def submit_order(self, intent: OrderIntent) -> OrderSubmission:
        """Submit an order intent and return broker acknowledgement."""

    def get_open_orders(self) -> list[OrderIntent]:
        """Return open broker orders."""


class FakeBroker:
    """Deterministic in-memory broker for tests.

    Orders fill immediately at the limit price when provided, otherwise at a
    fixed default price. The fake is long-only and never connects to Alpaca.
    """

    def __init__(
        self,
        initial_state: PortfolioState,
        *,
        default_fill_price: Decimal = Decimal("100"),
    ) -> None:
        self._account = initial_state
        self._default_fill_price = default_fill_price
        self._submitted_orders: list[OrderIntent] = []
        self._fills: list[Fill] = []
        self._next_order_number = 1

    @property
    def submitted_orders(self) -> list[OrderIntent]:
        """Return submitted order intents for test inspection."""

        return list(self._submitted_orders)

    @property
    def fills(self) -> list[Fill]:
        """Return generated fills for test inspection."""

        return list(self._fills)

    def get_positions(self) -> list[Position]:
        """Return current fake broker positions."""

        return list(self._account.positions)

    def get_account(self) -> PortfolioState:
        """Return the current fake broker account state."""

        return self._account

    def submit_order(self, intent: OrderIntent) -> OrderSubmission:
        """Fill an order intent immediately and update fake account state."""

        fill_price = intent.limit_price or self._default_fill_price
        fill = Fill(
            broker_order_id=self._next_broker_order_id(),
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            fill_price=fill_price,
            filled_at=self._account.timestamp + timedelta(seconds=len(self._fills) + 1),
            raw_payload={"fake": True, "order_type": intent.order_type},
        )

        if intent.side == OrderSide.BUY:
            self._apply_buy(fill)
        elif intent.side == OrderSide.SELL:
            self._apply_sell(fill)
        else:
            raise ValueError(f"unsupported order side: {intent.side}")

        self._submitted_orders.append(intent)
        self._fills.append(fill)
        return OrderSubmission(
            broker_order_id=fill.broker_order_id,
            symbol=fill.symbol,
            side=fill.side,
            requested_quantity=intent.quantity,
            status=OrderStatus.FILLED,
            submitted_at=fill.filled_at,
            limit_price=intent.limit_price,
            fill=fill,
            raw_payload=fill.raw_payload,
        )

    def get_open_orders(self) -> list[OrderIntent]:
        """Return open orders.

        The fake broker fills synchronously, so no orders remain open.
        """

        return []

    def _next_broker_order_id(self) -> str:
        order_id = f"fake-order-{self._next_order_number:06d}"
        self._next_order_number += 1
        return order_id

    def _apply_buy(self, fill: Fill) -> None:
        cost = fill.quantity * fill.fill_price
        if cost > self._account.cash:
            raise ValueError("insufficient cash for fake broker buy")

        positions = _upsert_buy_position(self._account.positions, fill)
        self._account = _account_with_positions(
            account=self._account,
            cash=self._account.cash - cost,
            positions=positions,
        )

    def _apply_sell(self, fill: Fill) -> None:
        position = _find_position(self._account.positions, fill.symbol)
        if position is None or position.quantity <= Decimal("0"):
            raise ValueError("cannot sell without an existing long position")
        if fill.quantity > position.quantity:
            raise ValueError("cannot sell more than owned in long-only fake broker")

        proceeds = fill.quantity * fill.fill_price
        positions = _reduce_sell_position(self._account.positions, fill)
        self._account = _account_with_positions(
            account=self._account,
            cash=self._account.cash + proceeds,
            positions=positions,
        )


class AlpacaPaperBroker:
    """Paper-only Alpaca broker adapter.

    The adapter refuses to construct unless paper mode is explicitly requested.
    It maps accepted order responses into the existing Fill contract so the
    current TradingCycle can remain deterministic from the caller's view.
    """

    def __init__(self, client: Any, *, paper: bool) -> None:
        if not paper:
            raise BrokerSafetyError("AlpacaPaperBroker requires paper=True")
        self.client = client

    @classmethod
    def from_env(
        cls,
        *,
        paper: bool,
        env: Mapping[str, str] | None = None,
    ):
        source = env or os.environ
        api_key = source.get("ALPACA_API_KEY", "")
        secret_key = source.get("ALPACA_SECRET_KEY", "")
        missing = [
            name
            for name, value in [
                ("ALPACA_API_KEY", api_key),
                ("ALPACA_SECRET_KEY", secret_key),
            ]
            if not value
        ]
        if missing:
            raise ValueError(
                "missing required Alpaca environment variables: " + ", ".join(missing)
            )
        dependencies = _import_alpaca_trading_dependencies()
        client = dependencies["TradingClient"](api_key, secret_key, paper=paper)
        return cls(client, paper=paper)

    def get_positions(self) -> list[Position]:
        return [_alpaca_position(position) for position in self.client.get_all_positions()]

    def get_account(self) -> PortfolioState:
        account = self.client.get_account()
        positions = self.get_positions()
        return PortfolioState(
            cash=Decimal(str(_attr(account, "cash", "0"))),
            equity=Decimal(str(_attr(account, "equity", "0"))),
            buying_power=Decimal(str(_attr(account, "buying_power", "0"))),
            positions=positions,
            timestamp=datetime.now(UTC),
        )

    def submit_order(self, intent: OrderIntent) -> OrderSubmission:
        dependencies = _import_alpaca_trading_dependencies()
        request = dependencies["LimitOrderRequest"](
            symbol=intent.symbol,
            qty=str(_alpaca_order_quantity(intent)),
            side=_alpaca_order_side(intent.side, dependencies),
            time_in_force=dependencies["TimeInForce"].DAY,
            limit_price=float(intent.limit_price) if intent.limit_price is not None else None,
        )
        response = self.client.submit_order(order_data=request)
        return _alpaca_order_response_to_submission(response, intent)

    def get_open_orders(self) -> list[OrderIntent]:
        dependencies = _import_alpaca_trading_dependencies()
        request = dependencies["GetOrdersRequest"](
            status=dependencies["QueryOrderStatus"].OPEN
        )
        orders = self.client.get_orders(filter=request)
        return [_alpaca_order_to_intent(order) for order in orders]


def _find_position(positions: list[Position], symbol: str) -> Position | None:
    for position in positions:
        if position.symbol == symbol:
            return position
    return None


def _alpaca_position(position: Any) -> Position:
    quantity = Decimal(str(_attr(position, "qty", "0")))
    avg_entry_price = Decimal(str(_attr(position, "avg_entry_price", "0")))
    market_value = Decimal(str(_attr(position, "market_value", "0")))
    unrealized_pl = Decimal(str(_attr(position, "unrealized_pl", "0")))
    source = "alpaca_paper_short" if quantity < Decimal("0") else "alpaca_paper"
    return Position(
        symbol=str(_attr(position, "symbol", "")),
        quantity=abs(quantity),
        avg_entry_price=avg_entry_price,
        market_value=abs(market_value),
        unrealized_pl=unrealized_pl,
        source=source,
    )


def _alpaca_order_quantity(intent: OrderIntent) -> Decimal:
    if intent.side == OrderSide.BUY and intent.limit_price is not None:
        if intent.limit_price <= Decimal("0"):
            raise BrokerSafetyError("buy order limit_price must be positive")
        return intent.quantity / intent.limit_price
    return intent.quantity


def _alpaca_order_side(side: OrderSide, dependencies: dict[str, Any]) -> Any:
    order_side = dependencies["AlpacaOrderSide"]
    if side == OrderSide.BUY:
        return order_side.BUY
    if side == OrderSide.SELL:
        return order_side.SELL
    raise BrokerSafetyError(f"unsupported order side: {side}")


def _alpaca_order_response_to_submission(response: Any, intent: OrderIntent) -> OrderSubmission:
    filled_qty = Decimal(str(_attr(response, "filled_qty", "0") or "0"))
    fill_price = Decimal(
        str(
            _attr(
                response,
                "filled_avg_price",
                intent.limit_price if intent.limit_price is not None else "0",
            )
            or intent.limit_price
            or "0"
        )
    )
    submitted_at = _attr(response, "submitted_at", None) or datetime.now(UTC)
    if isinstance(submitted_at, str):
        submitted_at = datetime.fromisoformat(submitted_at)
    if submitted_at.tzinfo is None or submitted_at.utcoffset() is None:
        submitted_at = submitted_at.replace(tzinfo=UTC)
    broker_order_id = str(_attr(response, "id", ""))
    raw_status = str(_attr(response, "status", "") or "")
    requested_quantity = _alpaca_order_quantity(intent)
    fill = None
    status = _order_status(raw_status, filled_qty)
    if filled_qty > Decimal("0"):
        fill = Fill(
            broker_order_id=broker_order_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=filled_qty,
            fill_price=fill_price,
            filled_at=submitted_at,
            raw_payload={
                "alpaca_paper": True,
                "status": raw_status,
                "accepted_quantity": str(requested_quantity),
            },
        )
    return OrderSubmission(
        broker_order_id=broker_order_id,
        symbol=intent.symbol,
        side=intent.side,
        requested_quantity=requested_quantity,
        status=status,
        submitted_at=submitted_at,
        limit_price=intent.limit_price,
        fill=fill,
        raw_payload={
            "alpaca_paper": True,
            "status": raw_status,
            "accepted_quantity": str(requested_quantity),
            "filled_quantity": str(filled_qty),
        },
    )


def _order_status(raw_status: str, filled_qty: Decimal) -> OrderStatus:
    normalized = raw_status.lower()
    if filled_qty > Decimal("0"):
        if normalized == "partially_filled":
            return OrderStatus.PARTIALLY_FILLED
        return OrderStatus.FILLED
    if normalized in {"accepted", "new", "pending_new", "accepted_for_bidding"}:
        return OrderStatus.SUBMITTED
    if normalized == "canceled":
        return OrderStatus.CANCELED
    if normalized == "rejected":
        return OrderStatus.REJECTED
    if normalized == "expired":
        return OrderStatus.EXPIRED
    if normalized == "partially_filled":
        return OrderStatus.PARTIALLY_FILLED
    if normalized == "filled":
        return OrderStatus.FILLED
    return OrderStatus.SUBMITTED


def _alpaca_order_to_intent(order: Any) -> OrderIntent:
    side = _order_side_from_alpaca(_attr(order, "side", "buy"))
    limit_price = _attr(order, "limit_price", None)
    return OrderIntent(
        symbol=str(_attr(order, "symbol", "")),
        side=side,
        quantity=Decimal(str(_attr(order, "qty", "0") or "0")),
        order_type=str(_attr(order, "type", "limit")),
        time_in_force=str(_attr(order, "time_in_force", "day")),
        limit_price=Decimal(str(limit_price)) if limit_price is not None else None,
        reason="open_alpaca_paper_order",
        metadata={"broker_order_id": str(_attr(order, "id", ""))},
    )


def _order_side_from_alpaca(value: Any) -> OrderSide:
    raw_value = getattr(value, "value", value)
    text = str(raw_value).strip().lower()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return OrderSide(text)


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _import_alpaca_trading_dependencies() -> dict[str, Any]:
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.enums import OrderSide as AlpacaOrderSide
        from alpaca.trading.enums import QueryOrderStatus, TimeInForce
        from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest
    except ImportError as exc:
        raise ImportError(
            "alpaca-py is required for paper trading. "
            "Install it with: python -m pip install alpaca-py"
        ) from exc

    return {
        "TradingClient": TradingClient,
        "AlpacaOrderSide": AlpacaOrderSide,
        "QueryOrderStatus": QueryOrderStatus,
        "TimeInForce": TimeInForce,
        "GetOrdersRequest": GetOrdersRequest,
        "LimitOrderRequest": LimitOrderRequest,
    }


def _upsert_buy_position(positions: list[Position], fill: Fill) -> list[Position]:
    updated: list[Position] = []
    found = False

    for position in positions:
        if position.symbol != fill.symbol:
            updated.append(position)
            continue

        found = True
        new_quantity = position.quantity + fill.quantity
        total_cost = (
            position.avg_entry_price * position.quantity
            + fill.fill_price * fill.quantity
        )
        avg_entry_price = total_cost / new_quantity
        updated.append(
            Position(
                symbol=position.symbol,
                quantity=new_quantity,
                avg_entry_price=avg_entry_price,
                market_value=new_quantity * fill.fill_price,
                unrealized_pl=Decimal("0"),
                source="fake_broker",
            )
        )

    if not found:
        updated.append(
            Position(
                symbol=fill.symbol,
                quantity=fill.quantity,
                avg_entry_price=fill.fill_price,
                market_value=fill.quantity * fill.fill_price,
                unrealized_pl=Decimal("0"),
                source="fake_broker",
            )
        )

    return updated


def _reduce_sell_position(positions: list[Position], fill: Fill) -> list[Position]:
    updated: list[Position] = []

    for position in positions:
        if position.symbol != fill.symbol:
            updated.append(position)
            continue

        new_quantity = position.quantity - fill.quantity
        if new_quantity > Decimal("0"):
            updated.append(
                Position(
                    symbol=position.symbol,
                    quantity=new_quantity,
                    avg_entry_price=position.avg_entry_price,
                    market_value=new_quantity * fill.fill_price,
                    unrealized_pl=Decimal("0"),
                    source="fake_broker",
                )
            )

    return updated


def _account_with_positions(
    *,
    account: PortfolioState,
    cash: Decimal,
    positions: list[Position],
) -> PortfolioState:
    position_value = sum(
        (position.market_value for position in positions),
        Decimal("0"),
    )
    return PortfolioState(
        cash=cash,
        equity=cash + position_value,
        buying_power=cash,
        positions=positions,
        timestamp=account.timestamp,
    )
