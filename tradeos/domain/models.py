"""Domain model definitions.

This module will contain typed entities such as bars, signals, orders, fills,
positions, and portfolio snapshots.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from tradeos.domain.enums import (
    DataQualityStatus,
    OrderSide,
    OrderStatus,
    RiskDecisionStatus,
    StrategyMode,
    TradeAction,
)


Metadata = dict[str, Any]


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_non_negative(value: Decimal, field_name: str) -> None:
    if value < Decimal("0"):
        raise ValueError(f"{field_name} cannot be negative")


@dataclass(frozen=True)
class Bar:
    """Validated market bar consumed by strategies and backtests."""

    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timeframe: str
    data_quality: DataQualityStatus

    def __post_init__(self) -> None:
        _require_timezone_aware(self.timestamp, "timestamp")
        _require_non_negative(self.open, "open")
        _require_non_negative(self.high, "high")
        _require_non_negative(self.low, "low")
        _require_non_negative(self.close, "close")
        _require_non_negative(self.volume, "volume")


@dataclass(frozen=True)
class Signal:
    """Strategy output describing an intended action without order submission."""

    symbol: str
    action: TradeAction
    strategy_mode: StrategyMode
    timestamp: datetime
    reason: str
    confidence: Decimal
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_timezone_aware(self.timestamp, "timestamp")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class RiskDecision:
    """Risk manager decision for a proposed strategy action."""

    status: RiskDecisionStatus
    symbol: str
    action: TradeAction
    approved_quantity: Decimal | None
    reason: str
    blocked_rules: list[str] = field(default_factory=list)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.approved_quantity is not None:
            _require_non_negative(self.approved_quantity, "approved_quantity")


@dataclass(frozen=True)
class OrderIntent:
    """Execution request that has passed strategy and risk boundaries."""

    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: str
    time_in_force: str
    limit_price: Decimal | None
    reason: str
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_negative(self.quantity, "quantity")
        if self.limit_price is not None:
            _require_non_negative(self.limit_price, "limit_price")


@dataclass(frozen=True)
class Fill:
    """Broker fill event applied idempotently by broker_order_id."""

    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    fill_price: Decimal
    filled_at: datetime
    raw_payload: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_timezone_aware(self.filled_at, "filled_at")
        _require_non_negative(self.quantity, "quantity")
        _require_non_negative(self.fill_price, "fill_price")


@dataclass(frozen=True)
class OrderSubmission:
    """Broker acknowledgement for an order, with optional fill details."""

    broker_order_id: str
    symbol: str
    side: OrderSide
    requested_quantity: Decimal
    status: OrderStatus
    submitted_at: datetime
    limit_price: Decimal | None = None
    fill: Fill | None = None
    raw_payload: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_timezone_aware(self.submitted_at, "submitted_at")
        _require_non_negative(self.requested_quantity, "requested_quantity")
        if self.limit_price is not None:
            _require_non_negative(self.limit_price, "limit_price")


@dataclass(frozen=True)
class Position:
    """Reconciled position view from a trusted source."""

    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal
    market_value: Decimal
    unrealized_pl: Decimal
    source: str

    def __post_init__(self) -> None:
        _require_non_negative(self.quantity, "quantity")
        _require_non_negative(self.avg_entry_price, "avg_entry_price")


@dataclass(frozen=True)
class PortfolioState:
    """Portfolio snapshot used by risk, strategy context, and reporting."""

    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    positions: list[Position]
    timestamp: datetime

    def __post_init__(self) -> None:
        _require_timezone_aware(self.timestamp, "timestamp")
        _require_non_negative(self.cash, "cash")
        _require_non_negative(self.equity, "equity")
        _require_non_negative(self.buying_power, "buying_power")
