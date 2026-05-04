from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from tradeos.backtest.multi_month import (
    MultiMonthDatasetResult,
    summarize_multi_month,
    write_multi_month_report,
)
from tradeos.backtest.walk_forward import WalkForwardCandidate, WalkForwardResult
from tradeos.config.schema import StrategyConfig
from tradeos.domain.enums import StrategyMode


def strategy_config(entry: Decimal, exit_threshold: Decimal) -> StrategyConfig:
    return StrategyConfig(
        strategy_mode=StrategyMode.MOMENTUM_BREAKOUT,
        sma_bars=20,
        entry_threshold_pct=Decimal("0.01"),
        exit_threshold_pct=Decimal("0.01"),
        max_atr_percentile=Decimal("80"),
        breakout_threshold_pct=entry,
        momentum_exit_threshold_pct=exit_threshold,
    )


def result(
    path: str,
    *,
    pnl: Decimal,
    profit_factor: Decimal,
    drawdown: Decimal,
    closed_trades: int,
    verdict: str,
) -> MultiMonthDatasetResult:
    test_result = SimpleNamespace(
        total_pnl=pnl,
        profit_factor=profit_factor,
        max_drawdown=drawdown,
        closed_trades=closed_trades,
    )
    candidate = WalkForwardCandidate(
        strategy_config=strategy_config(Decimal("0.001"), Decimal("0.002")),
        backtest_result=SimpleNamespace(),
        experiment_result=SimpleNamespace(score=Decimal("10")),
    )
    return MultiMonthDatasetResult(
        dataset_path=path,
        walk_forward_result=WalkForwardResult(
            selection_bars=60,
            test_bars=40,
            test_warmup_bars=60,
            best_candidate=candidate,
            test_result=test_result,
            verdict=verdict,
        ),
    )


def test_aggregate_pass_fail_counts_are_correct() -> None:
    summary = summarize_multi_month(
        [
            result("jan.csv", pnl=Decimal("10"), profit_factor=Decimal("2"), drawdown=Decimal("1"), closed_trades=12, verdict="PASS"),
            result("feb.csv", pnl=Decimal("-5"), profit_factor=Decimal("0.5"), drawdown=Decimal("8"), closed_trades=3, verdict="FAIL"),
        ]
    )

    assert summary.dataset_count == 2
    assert summary.pass_count == 1
    assert summary.fail_count == 1


def test_average_metrics_are_calculated() -> None:
    summary = summarize_multi_month(
        [
            result("jan.csv", pnl=Decimal("10"), profit_factor=Decimal("2"), drawdown=Decimal("4"), closed_trades=12, verdict="PASS"),
            result("feb.csv", pnl=Decimal("20"), profit_factor=Decimal("3"), drawdown=Decimal("6"), closed_trades=8, verdict="PASS"),
        ]
    )

    assert summary.average_test_pnl == Decimal("15")
    assert summary.average_profit_factor == Decimal("2.5")
    assert summary.average_max_drawdown == Decimal("5")
    assert summary.worst_test_pnl == Decimal("10")
    assert summary.worst_drawdown == Decimal("6")
    assert summary.average_closed_trades == Decimal("10")


def test_consistency_score_penalizes_failures() -> None:
    summary = summarize_multi_month(
        [
            result("jan.csv", pnl=Decimal("-5"), profit_factor=Decimal("0.5"), drawdown=Decimal("20"), closed_trades=2, verdict="FAIL"),
        ]
    )

    assert summary.consistency_score == 50


def test_report_includes_per_dataset_rows() -> None:
    report_path = Path("tests/fixtures") / f"multi_month_{uuid4().hex}.md"

    try:
        write_multi_month_report(
            results=[
                result("jan.csv", pnl=Decimal("10"), profit_factor=Decimal("2"), drawdown=Decimal("1"), closed_trades=12, verdict="PASS"),
                result("feb.csv", pnl=Decimal("-5"), profit_factor=Decimal("0.5"), drawdown=Decimal("8"), closed_trades=3, verdict="FAIL"),
            ],
            path=report_path,
        )

        content = report_path.read_text(encoding="utf-8")
        assert "jan.csv" in content
        assert "feb.csv" in content
        assert "PASS count: 1" in content
        assert "FAIL count: 1" in content
    finally:
        report_path.unlink(missing_ok=True)
