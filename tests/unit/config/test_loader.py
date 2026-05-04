from pathlib import Path
from textwrap import dedent
from contextlib import contextmanager
from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import pytest

from tradeos.config.loader import load_config
from tradeos.domain.enums import ExecutionMode, StrategyMode


@contextmanager
def written_config(content: str) -> Iterator[Path]:
    config_path = Path("tests/fixtures") / f"runtime_{uuid4().hex}.yaml"
    config_path.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    try:
        yield config_path
    finally:
        config_path.unlink(missing_ok=True)


def valid_config() -> str:
    return """
    execution_mode: paper
    symbols:
      - AAPL
      - MSFT
    timeframe: 15Min
    strategy:
      strategy_mode: mean_reversion
      sma_bars: 20
      long_sma_bars: 50
      entry_threshold_pct: 1.0
      exit_threshold_pct: 0.3
      trend_filter_enabled: true
      max_below_long_sma_pct: 0.02
      stop_loss_enabled: true
      stop_loss_pct: 0.01
      breakout_sma_bars: 20
      breakout_threshold_pct: 0.002
      momentum_exit_threshold_pct: 0.001
      momentum_stop_loss_pct: 0.01
      require_positive_sma_slope: true
      max_atr_percentile: 80
    risk:
      max_position_value: 1000
      max_open_positions: 3
      max_daily_loss: 250
      allow_shorting: false
      entry_start_time: "14:00"
      entry_end_time: "19:30"
      allowed_symbols:
      refined_entry_hours: []
      no_entry_after: "15:30"
      force_flatten_at: "15:55"
    data:
      feed: iex
      max_bar_age_seconds: 120
      require_same_session: true
      alignment_mode: strict
    broker:
      name: alpaca
      paper: true
    """


def test_valid_paper_config_loads() -> None:
    config = load_config(Path("config/paper.yaml"))

    assert config.execution_mode == ExecutionMode.PAPER
    assert config.symbols == ["AAPL", "MSFT", "NVDA"]
    assert config.strategy.strategy_mode == StrategyMode.MEAN_REVERSION
    assert config.strategy.long_sma_bars == 50
    assert config.strategy.trend_filter_enabled is True
    assert config.strategy.max_below_long_sma_pct == Decimal("0.02")
    assert config.strategy.stop_loss_enabled is True
    assert config.strategy.stop_loss_pct == Decimal("0.01")
    assert config.strategy.breakout_sma_bars == 20
    assert config.strategy.breakout_threshold_pct == Decimal("0.002")
    assert config.strategy.momentum_exit_threshold_pct == Decimal("0.001")
    assert config.strategy.momentum_stop_loss_pct == Decimal("0.01")
    assert config.strategy.require_positive_sma_slope is True
    assert config.risk.allow_shorting is False
    assert config.risk.entry_start_time == "14:00"
    assert config.risk.entry_end_time == "19:30"
    assert config.risk.allowed_symbols is None
    assert config.risk.refined_entry_hours == []
    assert config.data.feed == "iex"
    assert config.broker.name == "alpaca"
    assert config.broker.paper is True
    assert config.backtest_costs.commission_per_trade == 0
    assert config.backtest_costs.slippage_bps == 0


def test_missing_symbols_rejected() -> None:
    config_without_symbols = dedent(valid_config()).replace(
        "symbols:\n  - AAPL\n  - MSFT\n", ""
    )
    with written_config(
        config_without_symbols
    ) as path:
        with pytest.raises(ValueError, match="missing required config field: symbols"):
            load_config(path)


def test_empty_symbols_rejected() -> None:
    with written_config(
        valid_config().replace("symbols:\n      - AAPL\n      - MSFT", "symbols: []"),
    ) as path:
        with pytest.raises(ValueError, match="symbols cannot be empty"):
            load_config(path)


def test_allow_shorting_defaults_false_if_omitted() -> None:
    with written_config(
        valid_config().replace("      allow_shorting: false\n", ""),
    ) as path:
        config = load_config(path)

    assert config.risk.allow_shorting is False


def test_entry_window_defaults_if_omitted() -> None:
    with written_config(
        valid_config()
        .replace('      entry_start_time: "14:00"\n', "")
        .replace('      entry_end_time: "19:30"\n', ""),
    ) as path:
        config = load_config(path)

    assert config.risk.entry_start_time == "14:00"
    assert config.risk.entry_end_time == "19:30"


def test_risk_symbol_and_refined_hour_filters_load() -> None:
    with written_config(
        valid_config()
        .replace("      allowed_symbols:\n", "      allowed_symbols:\n        - AAPL\n")
        .replace("      refined_entry_hours: []", '      refined_entry_hours: ["14", "18"]')
    ) as path:
        config = load_config(path)

    assert config.risk.allowed_symbols == ["AAPL"]
    assert config.risk.refined_entry_hours == ["14", "18"]


def test_config_loads_default_backtest_costs_if_omitted() -> None:
    with written_config(valid_config()) as path:
        config = load_config(path)

    assert config.backtest_costs.commission_per_trade == 0
    assert config.backtest_costs.slippage_bps == 0


def test_strategy_trend_filter_defaults_if_omitted() -> None:
    with written_config(
        valid_config()
        .replace("      long_sma_bars: 50\n", "")
        .replace("      trend_filter_enabled: true\n", "")
        .replace("      max_below_long_sma_pct: 0.02\n", "")
        .replace("      stop_loss_enabled: true\n", "")
        .replace("      stop_loss_pct: 0.01\n", "")
        .replace("      breakout_sma_bars: 20\n", "")
        .replace("      breakout_threshold_pct: 0.002\n", "")
        .replace("      momentum_exit_threshold_pct: 0.001\n", "")
        .replace("      momentum_stop_loss_pct: 0.01\n", "")
        .replace("      require_positive_sma_slope: true\n", "")
    ) as path:
        config = load_config(path)

    assert config.strategy.long_sma_bars == 50
    assert config.strategy.trend_filter_enabled is True
    assert config.strategy.max_below_long_sma_pct == Decimal("0.02")
    assert config.strategy.stop_loss_enabled is True
    assert config.strategy.stop_loss_pct == Decimal("0.01")
    assert config.strategy.breakout_sma_bars == 20
    assert config.strategy.breakout_threshold_pct == Decimal("0.002")
    assert config.strategy.momentum_exit_threshold_pct == Decimal("0.001")
    assert config.strategy.momentum_stop_loss_pct == Decimal("0.01")
    assert config.strategy.require_positive_sma_slope is True


def test_invalid_execution_mode_rejected() -> None:
    with written_config(
        valid_config().replace("execution_mode: paper", "execution_mode: sim")
    ) as path:
        with pytest.raises(ValueError, match="execution_mode must be one of"):
            load_config(path)


def test_invalid_negative_risk_value_rejected() -> None:
    with written_config(
        valid_config().replace("max_position_value: 1000", "max_position_value: -1"),
    ) as path:
        with pytest.raises(ValueError, match="risk.max_position_value must be positive"):
            load_config(path)


def test_force_flatten_at_before_no_entry_after_rejected() -> None:
    with written_config(
        valid_config().replace('force_flatten_at: "15:55"', 'force_flatten_at: "15:00"'),
    ) as path:
        with pytest.raises(
            ValueError,
            match="risk.force_flatten_at must be after risk.no_entry_after",
        ):
            load_config(path)


def test_invalid_entry_window_time_format_rejected() -> None:
    with written_config(
        valid_config().replace('entry_start_time: "14:00"', 'entry_start_time: "bad"'),
    ) as path:
        with pytest.raises(
            ValueError,
            match="risk.entry_start_time must be in HH:MM format",
        ):
            load_config(path)


def test_max_atr_percentile_outside_range_rejected() -> None:
    with written_config(
        valid_config().replace("max_atr_percentile: 80", "max_atr_percentile: 101"),
    ) as path:
        with pytest.raises(
            ValueError,
            match="strategy.max_atr_percentile must be between 0 and 100",
        ):
            load_config(path)
