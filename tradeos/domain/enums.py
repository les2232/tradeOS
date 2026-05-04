"""Domain enum definitions.

This module will contain shared enum values for sides, order types, order
statuses, timeframes, and runtime modes.
"""

from enum import StrEnum


class TradeAction(StrEnum):
    """Signal-level trading action emitted by strategies."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class OrderSide(StrEnum):
    """Broker-facing order side."""

    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    """Broker order lifecycle status."""

    NEW = "new"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class StrategyMode(StrEnum):
    """Supported strategy families."""

    MEAN_REVERSION = "mean_reversion"
    MOMENTUM_BREAKOUT = "momentum_breakout"


class RiskDecisionStatus(StrEnum):
    """Risk gate decision status."""

    APPROVED = "approved"
    BLOCKED = "blocked"


class ExecutionMode(StrEnum):
    """Runtime execution mode."""

    PAPER = "paper"
    LIVE = "live"
    DRY_RUN = "dry_run"


class DataQualityStatus(StrEnum):
    """Validation status for market data."""

    OK = "ok"
    STALE = "stale"
    MISSING = "missing"
    INVALID = "invalid"
