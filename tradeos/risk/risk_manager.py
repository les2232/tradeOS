"""Risk manager boundary.

This module will evaluate proposed orders against account, portfolio, strategy,
and safety constraints before execution is allowed.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from tradeos.config.schema import RiskConfig
from tradeos.domain.enums import RiskDecisionStatus, TradeAction
from tradeos.domain.models import PortfolioState, RiskDecision, Signal
from tradeos.risk.safety_rules import (
    find_position,
    has_existing_position,
    has_long_position,
    has_sufficient_buying_power,
    would_exceed_max_positions,
)


@dataclass(frozen=True)
class RiskManager:
    """Deterministic safety gate between strategy signals and execution."""

    def approve(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        config: RiskConfig,
    ) -> RiskDecision:
        """Approve or block a signal before any order intent exists."""

        if signal.action == TradeAction.HOLD:
            return _blocked(
                signal=signal,
                approved_quantity=Decimal("0"),
                reason="hold_signal_no_order",
                blocked_rules=[],
            )

        if signal.action == TradeAction.BUY:
            return self._approve_buy(signal, portfolio, config)

        if signal.action == TradeAction.SELL:
            return self._approve_sell(signal, portfolio, config)

        return _blocked(
            signal=signal,
            approved_quantity=Decimal("0"),
            reason="unsupported_trade_action",
            blocked_rules=["unsupported_trade_action"],
        )

    def _approve_buy(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        config: RiskConfig,
    ) -> RiskDecision:
        blocked_rules: list[str] = []

        if has_existing_position(portfolio, signal.symbol):
            return _blocked(
                signal=signal,
                approved_quantity=Decimal("0"),
                reason="buy_blocked_existing_position",
                blocked_rules=["existing_position"],
            )

        if not _symbol_allowed(signal, config):
            return _blocked(
                signal=signal,
                approved_quantity=Decimal("0"),
                reason="buy_blocked_symbol_filter",
                blocked_rules=["symbol_filter"],
            )

        if not _within_refined_entry_hours(signal, config):
            return _blocked(
                signal=signal,
                approved_quantity=Decimal("0"),
                reason="buy_blocked_refined_time_filter",
                blocked_rules=["refined_time_filter"],
            )

        if not _within_entry_window(signal, config):
            return _blocked(
                signal=signal,
                approved_quantity=Decimal("0"),
                reason="buy_blocked_outside_entry_window",
                blocked_rules=["entry_time_window"],
            )
        if _past_no_entry_after(signal, config):
            return _blocked(
                signal=signal,
                approved_quantity=Decimal("0"),
                reason="buy_blocked_after_no_entry_time",
                blocked_rules=["no_entry_after"],
            )
        if _daily_loss_limit_breached(portfolio, config):
            return _blocked(
                signal=signal,
                approved_quantity=Decimal("0"),
                reason="buy_blocked_daily_loss_limit",
                blocked_rules=["max_daily_loss"],
            )

        if config.max_position_value <= Decimal("0"):
            blocked_rules.append("invalid_max_position_value")
        if would_exceed_max_positions(
            portfolio,
            signal.symbol,
            config.max_open_positions,
        ):
            blocked_rules.append("max_open_positions_reached")
        if not has_sufficient_buying_power(portfolio, config.max_position_value):
            blocked_rules.append("insufficient_buying_power")

        if blocked_rules:
            return _blocked(
                signal=signal,
                approved_quantity=Decimal("0"),
                reason="buy_signal_blocked_by_risk",
                blocked_rules=blocked_rules,
            )

        return RiskDecision(
            status=RiskDecisionStatus.APPROVED,
            symbol=signal.symbol,
            action=signal.action,
            approved_quantity=config.max_position_value,
            reason="buy_signal_approved",
            blocked_rules=[],
            metadata={"quantity_units": "budget_value"},
        )

    def _approve_sell(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        config: RiskConfig,
    ) -> RiskDecision:
        blocked_rules: list[str] = []
        position = find_position(portfolio, signal.symbol)

        has_long = has_long_position(portfolio, signal.symbol)
        if not has_long:
            blocked_rules.append("no_long_position")
        if position is not None and position.quantity == Decimal("0"):
            blocked_rules.append("zero_position_quantity")

        requested_quantity = _requested_quantity(signal)
        if position is not None and requested_quantity is not None:
            if requested_quantity > position.quantity:
                blocked_rules.append("sell_would_create_short")
        elif not has_long:
            blocked_rules.append("sell_would_create_short")

        if "sell_would_create_short" in blocked_rules and not config.allow_shorting:
            blocked_rules.append("shorting_disabled")

        if blocked_rules:
            return _blocked(
                signal=signal,
                approved_quantity=Decimal("0"),
                reason="sell_signal_blocked_by_risk",
                blocked_rules=blocked_rules,
            )

        approved_quantity = position.quantity if position is not None else Decimal("0")
        return RiskDecision(
            status=RiskDecisionStatus.APPROVED,
            symbol=signal.symbol,
            action=signal.action,
            approved_quantity=approved_quantity,
            reason="sell_signal_approved",
            blocked_rules=[],
            metadata={"quantity_units": "shares"},
        )


def _blocked(
    signal: Signal,
    approved_quantity: Decimal,
    reason: str,
    blocked_rules: list[str],
) -> RiskDecision:
    return RiskDecision(
        status=RiskDecisionStatus.BLOCKED,
        symbol=signal.symbol,
        action=signal.action,
        approved_quantity=approved_quantity,
        reason=reason,
        blocked_rules=blocked_rules,
        metadata={},
    )


def _requested_quantity(signal: Signal) -> Decimal | None:
    raw_quantity = signal.metadata.get("requested_quantity")
    if raw_quantity is None:
        return None
    try:
        return Decimal(str(raw_quantity))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("signal.metadata.requested_quantity must be decimal") from exc


def _within_entry_window(signal: Signal, config: RiskConfig) -> bool:
    signal_minutes = signal.timestamp.hour * 60 + signal.timestamp.minute
    start_minutes = _minutes(config.entry_start_time)
    end_minutes = _minutes(config.entry_end_time)
    return start_minutes <= signal_minutes <= end_minutes


def _past_no_entry_after(signal: Signal, config: RiskConfig) -> bool:
    signal_minutes = signal.timestamp.hour * 60 + signal.timestamp.minute
    return signal_minutes > _minutes(config.no_entry_after)


def _daily_loss_limit_breached(portfolio: PortfolioState, config: RiskConfig) -> bool:
    unrealized_pl = sum(
        (position.unrealized_pl for position in portfolio.positions),
        Decimal("0"),
    )
    return unrealized_pl <= -config.max_daily_loss


def _symbol_allowed(signal: Signal, config: RiskConfig) -> bool:
    if config.allowed_symbols is None:
        return True
    return signal.symbol in set(config.allowed_symbols)


def _within_refined_entry_hours(signal: Signal, config: RiskConfig) -> bool:
    refined_hours = config.refined_entry_hours or []
    if not refined_hours:
        return True
    return f"{signal.timestamp.hour:02d}" in set(refined_hours)


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)
