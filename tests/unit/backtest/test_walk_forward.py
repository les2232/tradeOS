from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradeos.backtest.walk_forward import (
    WalkForwardCandidate,
    build_warmup_history,
    select_best_candidate,
    split_walk_forward_bars,
    walk_forward_verdict,
)
from tradeos.config.schema import StrategyConfig
from tradeos.domain.enums import DataQualityStatus, StrategyMode
from tradeos.domain.models import Bar
from tradeos.research.experiment import ExperimentResult, ExperimentSpec


def bars(count: int) -> list[Bar]:
    start = datetime(2026, 4, 25, 14, 30, tzinfo=UTC)
    return [
        Bar(
            symbol="AAPL",
            timestamp=start + timedelta(minutes=15 * index),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1000"),
            timeframe="15Min",
            data_quality=DataQualityStatus.OK,
        )
        for index in range(count)
    ]


def strategy_config(entry: Decimal) -> StrategyConfig:
    return StrategyConfig(
        strategy_mode=StrategyMode.MEAN_REVERSION,
        sma_bars=2,
        entry_threshold_pct=entry,
        exit_threshold_pct=Decimal("0.01"),
        trend_filter_enabled=True,
        max_atr_percentile=Decimal("80"),
    )


def experiment_result(experiment_id: str, score: Decimal) -> ExperimentResult:
    spec = ExperimentSpec(
        strategy_mode=StrategyMode.MEAN_REVERSION,
        symbols=["AAPL"],
        start_date=datetime(2026, 4, 1, tzinfo=UTC).date(),
        end_date=datetime(2026, 4, 30, tzinfo=UTC).date(),
        timeframe="15Min",
        parameters={},
        dataset_id="unit.csv",
    )
    return ExperimentResult(
        experiment_id=experiment_id,
        spec=spec,
        profit_factor=Decimal("1"),
        max_drawdown=Decimal("0"),
        win_rate=Decimal("0"),
        num_trades=0,
        expectancy=Decimal("0"),
        net_pnl=Decimal("0"),
        score=score,
        warnings=[],
    )


def test_walk_forward_splits_dataset_into_selection_test_windows() -> None:
    selection, test = split_walk_forward_bars(list(reversed(bars(10))))

    assert len(selection) == 6
    assert len(test) == 4
    assert selection[0].timestamp < test[0].timestamp


def test_selects_best_parameter_combo_from_selection() -> None:
    low = WalkForwardCandidate(
        strategy_config=strategy_config(Decimal("0.001")),
        backtest_result=None,
        experiment_result=experiment_result("low", Decimal("1")),
    )
    high = WalkForwardCandidate(
        strategy_config=strategy_config(Decimal("0.002")),
        backtest_result=None,
        experiment_result=experiment_result("high", Decimal("5")),
    )

    best = select_best_candidate([low, high])

    assert best == high


def test_empty_candidate_selection_returns_none() -> None:
    assert select_best_candidate([]) is None


def test_walk_forward_verdict_handles_missing_result() -> None:
    assert walk_forward_verdict(None) == "FAIL"


def test_build_warmup_history_groups_selection_bars_by_symbol() -> None:
    input_bars = bars(2) + [
        Bar(
            symbol="MSFT",
            timestamp=datetime(2026, 4, 25, 15, 0, tzinfo=UTC),
            open=Decimal("200"),
            high=Decimal("201"),
            low=Decimal("199"),
            close=Decimal("200"),
            volume=Decimal("1000"),
            timeframe="15Min",
            data_quality=DataQualityStatus.OK,
        )
    ]

    history = build_warmup_history(input_bars)

    assert len(history["AAPL"]) == 2
    assert len(history["MSFT"]) == 1
