"""Momentum breakout strategy.

This strategy emits signals only. It does not create orders, check risk, or
access broker execution APIs.
"""

from dataclasses import dataclass
from decimal import Decimal

from tradeos.config.schema import StrategyConfig
from tradeos.domain.enums import DataQualityStatus, StrategyMode, TradeAction
from tradeos.domain.models import Bar, Position, Signal


@dataclass(frozen=True)
class MomentumBreakoutStrategy:
    """Buy upward breakouts when price and SMA slope confirm momentum."""

    name: str = "momentum_breakout"
    strategy_mode: StrategyMode = StrategyMode.MOMENTUM_BREAKOUT

    def generate_signal(
        self,
        bar: Bar,
        config: StrategyConfig,
        history: list[Bar] | None = None,
        current_position: Position | None = None,
    ) -> Signal:
        """Generate a momentum signal from prior-bar SMA context."""

        if bar.data_quality is not DataQualityStatus.OK:
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.HOLD,
                reason=f"data_quality_{bar.data_quality.value}_hold",
                confidence=Decimal("0"),
                metadata={"strategy_name": self.name},
            )

        prior_bars = history or []
        required_history = config.breakout_sma_bars + 1
        if len(prior_bars) < required_history:
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.HOLD,
                reason="insufficient_history_for_breakout_sma",
                confidence=Decimal("0"),
                metadata={
                    "strategy_name": self.name,
                    "breakout_sma_bars": str(config.breakout_sma_bars),
                    "history_count": str(len(prior_bars)),
                    "has_position": str(_has_position(current_position)).lower(),
                },
            )

        sma = _average_close(prior_bars[-config.breakout_sma_bars :])
        previous_sma = _average_close(
            prior_bars[-(config.breakout_sma_bars + 1) : -1]
        )
        if sma <= Decimal("0"):
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.HOLD,
                reason="invalid_breakout_sma_reference",
                confidence=Decimal("0"),
                metadata={
                    "strategy_name": self.name,
                    "sma": str(sma),
                    "previous_sma": str(previous_sma),
                },
            )

        sma_slope = sma - previous_sma
        deviation_pct = (bar.close - sma) / sma
        has_position = _has_position(current_position)
        position_return = _position_return(bar, current_position)
        metadata = {
            "strategy_name": self.name,
            "sma": str(sma),
            "previous_sma": str(previous_sma),
            "sma_slope": str(sma_slope),
            "deviation_pct": str(deviation_pct),
            "breakout_threshold_pct": str(config.breakout_threshold_pct),
            "momentum_exit_threshold_pct": str(config.momentum_exit_threshold_pct),
            "momentum_stop_loss_pct": str(config.momentum_stop_loss_pct),
            "require_positive_sma_slope": str(config.require_positive_sma_slope).lower(),
            "has_position": str(has_position).lower(),
            "position_return": str(position_return) if position_return is not None else "",
        }

        if has_position:
            if (
                position_return is not None
                and position_return <= -config.momentum_stop_loss_pct
            ):
                return _signal(
                    bar=bar,
                    config=config,
                    action=TradeAction.SELL,
                    reason="momentum_stop_loss_triggered",
                    confidence=Decimal("1"),
                    metadata=metadata,
                )
            if deviation_pct <= config.momentum_exit_threshold_pct:
                return _signal(
                    bar=bar,
                    config=config,
                    action=TradeAction.SELL,
                    reason="momentum_exit_threshold_crossed",
                    confidence=Decimal("1"),
                    metadata=metadata,
                )

        if (
            not has_position
            and deviation_pct >= config.breakout_threshold_pct
            and (
                not config.require_positive_sma_slope
                or sma_slope > Decimal("0")
            )
        ):
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.BUY,
                reason="momentum_breakout_entry",
                confidence=Decimal("1"),
                metadata=metadata,
            )

        return _signal(
            bar=bar,
            config=config,
            action=TradeAction.HOLD,
            reason="momentum_no_signal",
            confidence=Decimal("0"),
            metadata=metadata,
        )


def _average_close(bars: list[Bar]) -> Decimal:
    return sum((bar.close for bar in bars), Decimal("0")) / Decimal(len(bars))


def _has_position(current_position: Position | None) -> bool:
    return current_position is not None and current_position.quantity > Decimal("0")


def _position_return(bar: Bar, current_position: Position | None) -> Decimal | None:
    if not _has_position(current_position):
        return None
    if current_position.avg_entry_price <= Decimal("0"):
        return None
    return (bar.close - current_position.avg_entry_price) / current_position.avg_entry_price


def _signal(
    *,
    bar: Bar,
    config: StrategyConfig,
    action: TradeAction,
    reason: str,
    confidence: Decimal,
    metadata: dict[str, str],
) -> Signal:
    return Signal(
        symbol=bar.symbol,
        action=action,
        strategy_mode=config.strategy_mode,
        timestamp=bar.timestamp,
        reason=reason,
        confidence=confidence,
        metadata=metadata,
    )
