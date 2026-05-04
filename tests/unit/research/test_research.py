from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from tradeos.domain.enums import StrategyMode
from tradeos.research.experiment import (
    ExperimentResult,
    ExperimentSpec,
    make_experiment_id,
)
from tradeos.research.memory import ResearchMemory
from tradeos.research.report import write_markdown_report
from tradeos.research.scorer import generate_warnings, score_result


def spec(parameters: dict | None = None) -> ExperimentSpec:
    return ExperimentSpec(
        strategy_mode=StrategyMode.MEAN_REVERSION,
        symbols=["AAPL", "MSFT"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 2, 1),
        timeframe="15Min",
        parameters=parameters or {"sma_bars": 20, "entry_threshold_pct": "1.0"},
        dataset_id="dataset-1",
    )


def result(
    *,
    experiment_id: str = "experiment-1",
    profit_factor: Decimal = Decimal("1.5"),
    max_drawdown: Decimal = Decimal("10"),
    num_trades: int = 60,
    expectancy: Decimal = Decimal("1.0"),
    net_pnl: Decimal = Decimal("500"),
    score: Decimal | None = None,
    warnings: list[str] | None = None,
) -> ExperimentResult:
    base = ExperimentResult(
        experiment_id=experiment_id,
        spec=spec(),
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        win_rate=Decimal("0.55"),
        num_trades=num_trades,
        expectancy=expectancy,
        net_pnl=net_pnl,
        score=score or Decimal("0"),
        warnings=warnings or [],
    )
    computed_score = score_result(base) if score is None else score
    computed_warnings = generate_warnings(base) if warnings is None else warnings
    return ExperimentResult(
        experiment_id=base.experiment_id,
        spec=base.spec,
        profit_factor=base.profit_factor,
        max_drawdown=base.max_drawdown,
        win_rate=base.win_rate,
        num_trades=base.num_trades,
        expectancy=base.expectancy,
        net_pnl=base.net_pnl,
        score=computed_score,
        warnings=computed_warnings,
    )


def temp_path(name: str) -> Path:
    return Path("tests/fixtures") / f"{name}_{uuid4().hex}"


def test_deterministic_experiment_id() -> None:
    assert make_experiment_id(spec()) == make_experiment_id(
        spec({"entry_threshold_pct": "1.0", "sma_bars": 20})
    )


def test_scoring_rewards_good_results() -> None:
    good = result(profit_factor=Decimal("2.0"), expectancy=Decimal("2"), num_trades=80)
    weak = result(profit_factor=Decimal("1.1"), expectancy=Decimal("0.1"), num_trades=80)

    assert score_result(good) > score_result(weak)


def test_scoring_penalizes_low_trade_count() -> None:
    many_trades = result(num_trades=80)
    few_trades = result(num_trades=5)

    assert score_result(few_trades) < score_result(many_trades)


def test_scoring_penalizes_bad_profit_factor() -> None:
    good_pf = result(profit_factor=Decimal("1.5"))
    bad_pf = result(profit_factor=Decimal("0.8"))

    assert score_result(bad_pf) < score_result(good_pf)


def test_warning_generation() -> None:
    warnings = generate_warnings(
        result(
            profit_factor=Decimal("3.5"),
            num_trades=20,
            expectancy=Decimal("0"),
            max_drawdown=Decimal("100"),
            net_pnl=Decimal("50"),
        )
    )

    assert "low_trade_count" in warnings
    assert "high_profit_factor_low_sample" in warnings
    assert "non_positive_expectancy" in warnings
    assert "drawdown_high_relative_to_net_pnl" in warnings


def test_memory_saves_and_loads_result() -> None:
    path = temp_path("research_memory.jsonl")
    try:
        memory = ResearchMemory(path)
        saved = result()

        memory.save_result(saved)
        loaded = memory.load_results()

        assert loaded == [saved]
    finally:
        path.unlink(missing_ok=True)


def test_memory_does_not_duplicate_same_experiment() -> None:
    path = temp_path("research_memory.jsonl")
    try:
        memory = ResearchMemory(path)
        saved = result()

        memory.save_result(saved)
        memory.save_result(saved)

        assert len(memory.load_results()) == 1
    finally:
        path.unlink(missing_ok=True)


def test_ranking_sorts_by_score_descending() -> None:
    path = temp_path("research_memory.jsonl")
    try:
        memory = ResearchMemory(path)
        low = result(experiment_id="low", score=Decimal("1"))
        high = result(experiment_id="high", score=Decimal("10"))

        memory.save_result(low)
        memory.save_result(high)

        assert [item.experiment_id for item in memory.rank_results()] == ["high", "low"]
    finally:
        path.unlink(missing_ok=True)


def test_markdown_report_writes_expected_content() -> None:
    path = temp_path("research_report.md")
    try:
        write_markdown_report(
            [result(experiment_id="experiment-1", warnings=["low_trade_count"])],
            path,
        )

        content = path.read_text(encoding="utf-8")
        assert "TradeOS V3 Research Report" in content
        assert "Total experiments: 1" in content
        assert "Best score:" in content
        assert "experiment-1" in content
        assert "low_trade_count" in content
    finally:
        path.unlink(missing_ok=True)
