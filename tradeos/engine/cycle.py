"""Single-cycle orchestration.

This module will define one deterministic engine cycle from data ingestion
through signal generation, risk gating, execution, and reporting.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from tradeos.config.schema import RiskConfig, StrategyConfig
from tradeos.domain.enums import OrderSide, OrderStatus, RiskDecisionStatus, TradeAction
from tradeos.domain.models import (
    Bar,
    Fill,
    OrderIntent,
    OrderSubmission,
    Position,
    RiskDecision,
    Signal,
)
from tradeos.execution.broker import Broker
from tradeos.execution.reconciliation import ReconciliationResult, ReconciliationService
from tradeos.risk.risk_manager import RiskManager
from tradeos.storage.repositories import EventStore
from tradeos.strategy.base import Strategy


@dataclass(frozen=True)
class TradingCycleResult:
    """Outcome of one deterministic single-bar trading cycle."""

    signal: Signal
    risk_decision: RiskDecision
    reconciliation_result: ReconciliationResult
    order_intent: OrderIntent | None
    order_submission: OrderSubmission | None
    fill: Fill | None
    execution_allowed: bool
    reason: str


class TradingCycle:
    """Wire strategy, reconciliation, risk, and optional execution once."""

    def __init__(
        self,
        strategy: Strategy,
        risk_manager: RiskManager,
        broker: Broker,
        reconciliation_service: ReconciliationService,
        execute_orders: bool = False,
        event_store: EventStore | None = None,
    ) -> None:
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.broker = broker
        self.reconciliation_service = reconciliation_service
        self.execute_orders = execute_orders
        self.event_store = event_store

    def run(
        self,
        bar: Bar,
        strategy_config: StrategyConfig,
        risk_config: RiskConfig,
        local_positions: list[Position],
        history: list[Bar] | None = None,
    ) -> TradingCycleResult:
        """Run one deterministic cycle for a single market bar."""

        self._append_event(
            "cycle_started",
            bar.symbol,
            {
                "timeframe": bar.timeframe,
                "execute_orders": self.execute_orders,
            },
        )
        broker_account = self.broker.get_account()
        broker_positions = self.broker.get_positions()
        reconciliation_result = self.reconciliation_service.reconcile(
            broker_positions=broker_positions,
            local_positions=local_positions,
        )
        self._append_event(
            "reconciliation_result",
            bar.symbol,
            {
                "status": reconciliation_result.status,
                "execution_allowed": reconciliation_result.execution_allowed,
                "reason": reconciliation_result.reason,
                "issue_count": len(reconciliation_result.issues),
            },
        )
        current_position = _position_for_symbol(broker_account.positions, bar.symbol)
        strategy_signal = self.strategy.generate_signal(
            bar,
            strategy_config,
            history=history,
            current_position=current_position,
        )
        signal = _protective_signal_override(
            strategy_signal,
            current_position=current_position,
            portfolio=broker_account,
            risk_config=risk_config,
        )
        self._append_event(
            "signal_generated",
            signal.symbol,
            {
                "action": signal.action,
                "strategy_mode": signal.strategy_mode,
                "reason": signal.reason,
                "confidence": signal.confidence,
            },
        )
        risk_decision = self.risk_manager.approve(signal, broker_account, risk_config)
        self._append_event(
            "risk_decision",
            signal.symbol,
            {
                "status": risk_decision.status,
                "action": risk_decision.action,
                "approved_quantity": risk_decision.approved_quantity,
                "reason": risk_decision.reason,
                "blocked_rules": risk_decision.blocked_rules,
            },
        )

        if not reconciliation_result.execution_allowed:
            return self._complete(
                symbol=signal.symbol,
                result=TradingCycleResult(
                signal=signal,
                risk_decision=risk_decision,
                reconciliation_result=reconciliation_result,
                order_intent=None,
                order_submission=None,
                fill=None,
                execution_allowed=False,
                reason="reconciliation_blocked_execution",
                ),
            )

        if risk_decision.status == RiskDecisionStatus.BLOCKED:
            return self._complete(
                symbol=signal.symbol,
                result=TradingCycleResult(
                signal=signal,
                risk_decision=risk_decision,
                reconciliation_result=reconciliation_result,
                order_intent=None,
                order_submission=None,
                fill=None,
                execution_allowed=False,
                reason=risk_decision.reason,
                ),
            )

        order_intent = _order_intent_from_signal(bar, signal, risk_decision)
        if order_intent is None:
            return self._complete(
                symbol=signal.symbol,
                result=TradingCycleResult(
                signal=signal,
                risk_decision=risk_decision,
                reconciliation_result=reconciliation_result,
                order_intent=None,
                order_submission=None,
                fill=None,
                execution_allowed=False,
                reason="no_order_for_signal",
                ),
            )

        self._append_event(
            "order_intent_created",
            order_intent.symbol,
            {
                "side": order_intent.side,
                "quantity": order_intent.quantity,
                "order_type": order_intent.order_type,
                "time_in_force": order_intent.time_in_force,
                "limit_price": order_intent.limit_price,
                "reason": order_intent.reason,
            },
        )
        if not self.execute_orders:
            return self._complete(
                symbol=signal.symbol,
                result=TradingCycleResult(
                signal=signal,
                risk_decision=risk_decision,
                reconciliation_result=reconciliation_result,
                order_intent=order_intent,
                order_submission=None,
                fill=None,
                execution_allowed=True,
                reason="dry_run_order_not_submitted",
                ),
            )

        submission = self.broker.submit_order(order_intent)
        if submission.fill is not None:
            self._append_event(
                "fill_received",
                submission.symbol,
                {
                    "broker_order_id": submission.fill.broker_order_id,
                    "side": submission.fill.side,
                    "quantity": submission.fill.quantity,
                    "fill_price": submission.fill.fill_price,
                    "order_status": submission.status,
                },
            )
            result_fill = submission.fill
            reason = "order_filled"
        else:
            self._append_event(
                "order_submitted",
                submission.symbol,
                {
                    "broker_order_id": submission.broker_order_id,
                    "side": submission.side,
                    "requested_quantity": submission.requested_quantity,
                    "status": submission.status,
                    "limit_price": submission.limit_price,
                },
            )
            result_fill = None
            reason = _reason_for_submission(submission.status)
        return self._complete(
            symbol=signal.symbol,
            result=TradingCycleResult(
            signal=signal,
            risk_decision=risk_decision,
            reconciliation_result=reconciliation_result,
            order_intent=order_intent,
            order_submission=submission,
            fill=result_fill,
            execution_allowed=True,
            reason=reason,
            ),
        )

    def _append_event(
        self,
        event_type: str,
        symbol: str,
        payload: dict[str, Any],
    ) -> None:
        if self.event_store is None:
            return
        self.event_store.append_event(
            event_type=event_type,
            symbol=symbol,
            payload=_json_payload(payload),
        )

    def _complete(self, symbol: str, result: TradingCycleResult) -> TradingCycleResult:
        self._append_event(
            "cycle_completed",
            symbol,
            {
                "execution_allowed": result.execution_allowed,
                "reason": result.reason,
                "risk_status": result.risk_decision.status,
                "reconciliation_status": result.reconciliation_result.status,
                "order_created": result.order_intent is not None,
                "order_submitted": result.order_submission is not None,
                "order_status": result.order_submission.status if result.order_submission is not None else None,
                "fill_received": result.fill is not None,
            },
        )
        return result


def _order_intent_from_signal(
    bar: Bar,
    signal: Signal,
    risk_decision: RiskDecision,
) -> OrderIntent | None:
    if signal.action == TradeAction.HOLD or risk_decision.approved_quantity is None:
        return None

    if signal.action == TradeAction.BUY:
        side = OrderSide.BUY
    elif signal.action == TradeAction.SELL:
        side = OrderSide.SELL
    else:
        return None

    return OrderIntent(
        symbol=signal.symbol,
        side=side,
        quantity=risk_decision.approved_quantity,
        order_type="limit",
        time_in_force="day",
        limit_price=bar.close,
        reason=risk_decision.reason,
        metadata={"signal_reason": signal.reason},
    )


def _protective_signal_override(
    signal: Signal,
    *,
    current_position: Position | None,
    portfolio,
    risk_config: RiskConfig,
) -> Signal:
    if signal.action == TradeAction.SELL:
        return signal
    if current_position is None or current_position.quantity <= Decimal("0"):
        return signal
    reason = None
    if _past_force_flatten_at(signal, risk_config):
        reason = "force_flatten_at_triggered"
    elif _daily_loss_limit_breached(portfolio, risk_config):
        reason = "max_daily_loss_triggered"
    if reason is None:
        return signal
    return Signal(
        symbol=signal.symbol,
        action=TradeAction.SELL,
        strategy_mode=signal.strategy_mode,
        timestamp=signal.timestamp,
        reason=reason,
        confidence=Decimal("1"),
        metadata={"requested_quantity": str(current_position.quantity)},
    )


def _past_force_flatten_at(signal: Signal, risk_config: RiskConfig) -> bool:
    signal_minutes = signal.timestamp.hour * 60 + signal.timestamp.minute
    return signal_minutes >= _minutes(risk_config.force_flatten_at)


def _daily_loss_limit_breached(portfolio, risk_config: RiskConfig) -> bool:
    unrealized_pl = sum(
        (position.unrealized_pl for position in portfolio.positions),
        Decimal("0"),
    )
    return unrealized_pl <= -risk_config.max_daily_loss


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _reason_for_submission(status: OrderStatus) -> str:
    if status == OrderStatus.REJECTED:
        return "order_rejected"
    if status == OrderStatus.CANCELED:
        return "order_canceled"
    if status == OrderStatus.EXPIRED:
        return "order_expired"
    if status == OrderStatus.PARTIALLY_FILLED:
        return "order_partially_filled"
    return "order_submitted"


def _position_for_symbol(positions: list[Position], symbol: str) -> Position | None:
    for position in positions:
        if position.symbol == symbol:
            return position
    return None


def _json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in payload.items()}


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value
