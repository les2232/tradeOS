"""Configuration schema definitions.

This module will define the validated shapes for broker, strategy, risk,
storage, and runtime settings.
"""

from dataclasses import dataclass
from decimal import Decimal
import re

from tradeos.domain.enums import ExecutionMode, StrategyMode


_HH_MM_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _require_positive(value: Decimal | int, field_name: str) -> None:
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _require_hh_mm(value: str, field_name: str) -> None:
    if not _HH_MM_PATTERN.match(value):
        raise ValueError(f"{field_name} must be in HH:MM format")


def _minutes_since_midnight(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


@dataclass(frozen=True)
class StrategyConfig:
    """Strategy configuration shared by live and backtest runtimes."""

    strategy_mode: StrategyMode
    sma_bars: int
    entry_threshold_pct: Decimal
    exit_threshold_pct: Decimal
    max_atr_percentile: Decimal
    long_sma_bars: int = 50
    trend_filter_enabled: bool = True
    max_below_long_sma_pct: Decimal = Decimal("0.02")
    stop_loss_enabled: bool = True
    stop_loss_pct: Decimal = Decimal("0.01")
    breakout_sma_bars: int = 20
    breakout_threshold_pct: Decimal = Decimal("0.002")
    momentum_exit_threshold_pct: Decimal = Decimal("0.001")
    momentum_stop_loss_pct: Decimal = Decimal("0.01")
    require_positive_sma_slope: bool = True

    def __post_init__(self) -> None:
        _require_positive(self.sma_bars, "strategy.sma_bars")
        _require_positive(self.long_sma_bars, "strategy.long_sma_bars")
        _require_positive(self.breakout_sma_bars, "strategy.breakout_sma_bars")
        if self.max_below_long_sma_pct < Decimal("0"):
            raise ValueError("strategy.max_below_long_sma_pct cannot be negative")
        if self.stop_loss_pct < Decimal("0"):
            raise ValueError("strategy.stop_loss_pct cannot be negative")
        if self.breakout_threshold_pct < Decimal("0"):
            raise ValueError("strategy.breakout_threshold_pct cannot be negative")
        if self.momentum_exit_threshold_pct < Decimal("0"):
            raise ValueError("strategy.momentum_exit_threshold_pct cannot be negative")
        if self.momentum_stop_loss_pct < Decimal("0"):
            raise ValueError("strategy.momentum_stop_loss_pct cannot be negative")
        if not Decimal("0") <= self.max_atr_percentile <= Decimal("100"):
            raise ValueError("strategy.max_atr_percentile must be between 0 and 100")


@dataclass(frozen=True)
class RiskConfig:
    """Risk limits applied before execution is allowed."""

    max_position_value: Decimal
    max_open_positions: int
    max_daily_loss: Decimal
    no_entry_after: str
    force_flatten_at: str
    allow_shorting: bool = False
    entry_start_time: str = "14:00"
    entry_end_time: str = "19:30"
    allowed_symbols: list[str] | None = None
    refined_entry_hours: list[str] | None = None

    def __post_init__(self) -> None:
        _require_positive(self.max_position_value, "risk.max_position_value")
        _require_positive(self.max_open_positions, "risk.max_open_positions")
        _require_positive(self.max_daily_loss, "risk.max_daily_loss")
        _require_hh_mm(self.entry_start_time, "risk.entry_start_time")
        _require_hh_mm(self.entry_end_time, "risk.entry_end_time")
        _require_hh_mm(self.no_entry_after, "risk.no_entry_after")
        _require_hh_mm(self.force_flatten_at, "risk.force_flatten_at")
        if _minutes_since_midnight(self.entry_end_time) < _minutes_since_midnight(
            self.entry_start_time
        ):
            raise ValueError("risk.entry_end_time must be after risk.entry_start_time")
        for hour in self.refined_entry_hours or []:
            if not re.match(r"^(?:[01]\d|2[0-3])$", hour):
                raise ValueError("risk.refined_entry_hours must contain HH hour strings")
        if _minutes_since_midnight(self.force_flatten_at) <= _minutes_since_midnight(
            self.no_entry_after
        ):
            raise ValueError("risk.force_flatten_at must be after risk.no_entry_after")


@dataclass(frozen=True)
class DataConfig:
    """Market data runtime requirements."""

    feed: str
    max_bar_age_seconds: int
    require_same_session: bool
    alignment_mode: str

    def __post_init__(self) -> None:
        _require_positive(self.max_bar_age_seconds, "data.max_bar_age_seconds")


@dataclass(frozen=True)
class BrokerConfig:
    """Broker adapter configuration."""

    name: str
    paper: bool


@dataclass(frozen=True)
class BacktestCostConfig:
    """Backtest-only transaction cost assumptions."""

    commission_per_trade: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.commission_per_trade < Decimal("0"):
            raise ValueError("backtest_costs.commission_per_trade cannot be negative")
        if self.slippage_bps < Decimal("0"):
            raise ValueError("backtest_costs.slippage_bps cannot be negative")


@dataclass(frozen=True)
class RuntimeConfig:
    """Top-level runtime configuration for TradeOS V3."""

    execution_mode: ExecutionMode
    symbols: list[str]
    timeframe: str
    strategy: StrategyConfig
    risk: RiskConfig
    data: DataConfig
    broker: BrokerConfig
    backtest_costs: BacktestCostConfig

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("symbols cannot be empty")
