"""Configuration loading boundary.

This module will read environment variables and YAML files, then produce typed
configuration objects without triggering trading side effects.
"""

from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import Any

import yaml

from tradeos.config.schema import (
    BacktestCostConfig,
    BrokerConfig,
    DataConfig,
    RiskConfig,
    RuntimeConfig,
    StrategyConfig,
)
from tradeos.domain.enums import ExecutionMode, StrategyMode


def load_config(path: str | Path) -> RuntimeConfig:
    """Load and validate a TradeOS V3 YAML runtime config."""

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as config_file:
        raw = yaml.safe_load(config_file)

    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")

    for section in ("strategy", "risk", "data", "broker"):
        _required_mapping(raw, section)

    return RuntimeConfig(
        execution_mode=_enum_value(
            ExecutionMode, _required(raw, "execution_mode"), "execution_mode"
        ),
        symbols=_symbols(raw),
        timeframe=str(_required(raw, "timeframe")),
        strategy=_strategy_config(raw["strategy"]),
        risk=_risk_config(raw["risk"]),
        data=_data_config(raw["data"]),
        broker=_broker_config(raw["broker"]),
        backtest_costs=_backtest_cost_config(raw.get("backtest_costs", {})),
    )


def _required(mapping: dict[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"missing required config field: {key}")
    return mapping[key]


def _required_mapping(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = _required(mapping, key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def _symbols(raw: dict[str, Any]) -> list[str]:
    value = _required(raw, "symbols")
    if not isinstance(value, list):
        raise ValueError("symbols must be a list")
    symbols = [str(symbol).strip() for symbol in value if str(symbol).strip()]
    if not symbols:
        raise ValueError("symbols cannot be empty")
    return symbols


def _enum_value(enum_type: type, value: Any, field_name: str) -> Any:
    try:
        return enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {allowed}") from exc


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a decimal value") from exc


def _int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    return parsed


def _bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be true or false")


def _strategy_config(raw: dict[str, Any]) -> StrategyConfig:
    return StrategyConfig(
        strategy_mode=_enum_value(
            StrategyMode, _required(raw, "strategy_mode"), "strategy.strategy_mode"
        ),
        sma_bars=_int(_required(raw, "sma_bars"), "strategy.sma_bars"),
        long_sma_bars=_int(raw.get("long_sma_bars", 50), "strategy.long_sma_bars"),
        entry_threshold_pct=_decimal(
            _required(raw, "entry_threshold_pct"), "strategy.entry_threshold_pct"
        ),
        exit_threshold_pct=_decimal(
            _required(raw, "exit_threshold_pct"), "strategy.exit_threshold_pct"
        ),
        trend_filter_enabled=_bool(
            raw.get("trend_filter_enabled", True), "strategy.trend_filter_enabled"
        ),
        max_below_long_sma_pct=_decimal(
            raw.get("max_below_long_sma_pct", "0.02"),
            "strategy.max_below_long_sma_pct",
        ),
        stop_loss_enabled=_bool(
            raw.get("stop_loss_enabled", True),
            "strategy.stop_loss_enabled",
        ),
        stop_loss_pct=_decimal(
            raw.get("stop_loss_pct", "0.01"),
            "strategy.stop_loss_pct",
        ),
        breakout_sma_bars=_int(
            raw.get("breakout_sma_bars", 20),
            "strategy.breakout_sma_bars",
        ),
        breakout_threshold_pct=_decimal(
            raw.get("breakout_threshold_pct", "0.002"),
            "strategy.breakout_threshold_pct",
        ),
        momentum_exit_threshold_pct=_decimal(
            raw.get("momentum_exit_threshold_pct", "0.001"),
            "strategy.momentum_exit_threshold_pct",
        ),
        momentum_stop_loss_pct=_decimal(
            raw.get("momentum_stop_loss_pct", "0.01"),
            "strategy.momentum_stop_loss_pct",
        ),
        require_positive_sma_slope=_bool(
            raw.get("require_positive_sma_slope", True),
            "strategy.require_positive_sma_slope",
        ),
        max_atr_percentile=_decimal(
            _required(raw, "max_atr_percentile"), "strategy.max_atr_percentile"
        ),
    )


def _risk_config(raw: dict[str, Any]) -> RiskConfig:
    return RiskConfig(
        max_position_value=_decimal(
            _required(raw, "max_position_value"), "risk.max_position_value"
        ),
        max_open_positions=_int(
            _required(raw, "max_open_positions"), "risk.max_open_positions"
        ),
        max_daily_loss=_decimal(_required(raw, "max_daily_loss"), "risk.max_daily_loss"),
        allow_shorting=_bool(raw.get("allow_shorting", False), "risk.allow_shorting"),
        entry_start_time=str(raw.get("entry_start_time", "14:00")),
        entry_end_time=str(raw.get("entry_end_time", "19:30")),
        allowed_symbols=_optional_string_list(
            raw.get("allowed_symbols"),
            "risk.allowed_symbols",
        ),
        refined_entry_hours=_string_list(
            raw.get("refined_entry_hours", []),
            "risk.refined_entry_hours",
        ),
        no_entry_after=str(_required(raw, "no_entry_after")),
        force_flatten_at=str(_required(raw, "force_flatten_at")),
    )


def _data_config(raw: dict[str, Any]) -> DataConfig:
    return DataConfig(
        feed=str(_required(raw, "feed")),
        max_bar_age_seconds=_int(
            _required(raw, "max_bar_age_seconds"), "data.max_bar_age_seconds"
        ),
        require_same_session=_bool(
            _required(raw, "require_same_session"), "data.require_same_session"
        ),
        alignment_mode=str(_required(raw, "alignment_mode")),
    )


def _broker_config(raw: dict[str, Any]) -> BrokerConfig:
    return BrokerConfig(
        name=str(_required(raw, "name")),
        paper=_bool(_required(raw, "paper"), "broker.paper"),
    )


def _backtest_cost_config(raw: dict[str, Any]) -> BacktestCostConfig:
    if not isinstance(raw, dict):
        raise ValueError("backtest_costs must be a mapping")
    return BacktestCostConfig(
        commission_per_trade=_decimal(
            raw.get("commission_per_trade", "0"),
            "backtest_costs.commission_per_trade",
        ),
        slippage_bps=_decimal(
            raw.get("slippage_bps", "0"),
            "backtest_costs.slippage_bps",
        ),
    )


def _optional_string_list(value: Any, field_name: str) -> list[str] | None:
    if value is None:
        return None
    return _string_list(value, field_name)


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]
