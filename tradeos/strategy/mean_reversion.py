"""Simple mean reversion strategy.

This module emits signals only and remains safe for live/backtest parity
testing.
"""

from dataclasses import dataclass
from decimal import Decimal

from tradeos.config.schema import StrategyConfig
from tradeos.domain.enums import DataQualityStatus, StrategyMode, TradeAction
from tradeos.domain.models import Bar, Position, Signal


@dataclass(frozen=True)
class MeanReversionStrategy:
    """Mean reversion strategy using a rolling SMA reference price."""

    name: str = "mean_reversion"
    strategy_mode: StrategyMode = StrategyMode.MEAN_REVERSION

    def generate_signal(
        self,
        bar: Bar,
        config: StrategyConfig,
        history: list[Bar] | None = None,
        current_position: Position | None = None,
    ) -> Signal:
        """Generate a BUY, SELL, or HOLD signal from prior-bar SMA context."""

        if bar.data_quality is not DataQualityStatus.OK:
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.HOLD,
                reason=f"data_quality_{bar.data_quality.value}_hold",
                confidence=Decimal("0"),
                metadata={"strategy_name": self.name},
            )

        position_return = _position_return(bar, current_position)
        prior_bars = history or []
        if len(prior_bars) < config.sma_bars:
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.HOLD,
                reason="insufficient_history_for_sma",
                confidence=Decimal("0"),
                metadata={
                    "strategy_name": self.name,
                    "sma_bars": str(config.sma_bars),
                    "history_count": str(len(prior_bars)),
                    "position_return": (
                        str(position_return) if position_return is not None else ""
                    ),
                    "stop_loss_pct": str(config.stop_loss_pct),
                    "stop_loss_enabled": str(config.stop_loss_enabled).lower(),
                },
            )

        recent_closes = [item.close for item in prior_bars[-config.sma_bars :]]
        sma = sum(recent_closes, Decimal("0")) / Decimal(config.sma_bars)
        if sma <= Decimal("0"):
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.HOLD,
                reason="invalid_sma_reference",
                confidence=Decimal("0"),
                metadata={
                    "strategy_name": self.name,
                    "reference_price": str(sma),
                    "sma": str(sma),
                    "sma_bars": str(config.sma_bars),
                    "history_count": str(len(prior_bars)),
                    "position_return": (
                        str(position_return) if position_return is not None else ""
                    ),
                    "stop_loss_pct": str(config.stop_loss_pct),
                    "stop_loss_enabled": str(config.stop_loss_enabled).lower(),
                },
            )

        long_sma = None
        if config.trend_filter_enabled and len(prior_bars) >= config.long_sma_bars:
            long_closes = [item.close for item in prior_bars[-config.long_sma_bars :]]
            long_sma = sum(long_closes, Decimal("0")) / Decimal(config.long_sma_bars)

        deviation_pct = (bar.close - sma) / sma
        metadata = {
            "strategy_name": self.name,
            "reference_price": str(sma),
            "sma": str(sma),
            "long_sma": str(long_sma) if long_sma is not None else "",
            "deviation_pct": str(deviation_pct),
            "entry_threshold_pct": str(config.entry_threshold_pct),
            "exit_threshold_pct": str(config.exit_threshold_pct),
            "sma_bars": str(config.sma_bars),
            "long_sma_bars": str(config.long_sma_bars),
            "history_count": str(len(prior_bars)),
            "trend_filter_enabled": str(config.trend_filter_enabled).lower(),
            "max_below_long_sma_pct": str(config.max_below_long_sma_pct),
            "position_return": str(position_return) if position_return is not None else "",
            "stop_loss_pct": str(config.stop_loss_pct),
            "stop_loss_enabled": str(config.stop_loss_enabled).lower(),
        }

        if (
            config.stop_loss_enabled
            and position_return is not None
            and position_return <= -config.stop_loss_pct
        ):
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.SELL,
                reason="stop_loss_triggered",
                confidence=Decimal("1"),
                metadata=metadata,
            )

        if deviation_pct <= -config.entry_threshold_pct:
            if config.trend_filter_enabled:
                if long_sma is None or long_sma <= Decimal("0"):
                    return _signal(
                        bar=bar,
                        config=config,
                        action=TradeAction.HOLD,
                        reason="trend_filter_rejected_buy",
                        confidence=Decimal("0"),
                        metadata=metadata,
                    )
                minimum_allowed_price = long_sma * (
                    Decimal("1") - config.max_below_long_sma_pct
                )
                if bar.close < minimum_allowed_price:
                    return _signal(
                        bar=bar,
                        config=config,
                        action=TradeAction.HOLD,
                        reason="trend_filter_rejected_buy",
                        confidence=Decimal("0"),
                        metadata=metadata,
                    )
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.BUY,
                reason="mean_reversion_entry_threshold_crossed",
                confidence=Decimal("1"),
                metadata=metadata,
            )

        if deviation_pct >= config.exit_threshold_pct:
            return _signal(
                bar=bar,
                config=config,
                action=TradeAction.SELL,
                reason="mean_reversion_exit_threshold_crossed",
                confidence=Decimal("1"),
                metadata=metadata,
            )

        return _signal(
            bar=bar,
            config=config,
            action=TradeAction.HOLD,
            reason="mean_reversion_no_signal",
            confidence=Decimal("0"),
            metadata=metadata,
        )


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


def _position_return(bar: Bar, current_position: Position | None) -> Decimal | None:
    if current_position is None or current_position.quantity <= Decimal("0"):
        return None
    if current_position.avg_entry_price <= Decimal("0"):
        return None
    return (bar.close - current_position.avg_entry_price) / current_position.avg_entry_price
