"""Multi-dataset walk-forward reporting.

This module summarizes already-computed walk-forward results across multiple
CSV datasets. It is reporting-only and does not alter strategy or risk logic.
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from tradeos.backtest.walk_forward import WalkForwardResult
from tradeos.config.schema import StrategyConfig
from tradeos.domain.enums import StrategyMode


@dataclass(frozen=True)
class MultiMonthDatasetResult:
    """One dataset's walk-forward outcome."""

    dataset_path: str
    walk_forward_result: WalkForwardResult


@dataclass(frozen=True)
class MultiMonthSummary:
    """Aggregate robustness summary across datasets."""

    dataset_count: int
    pass_count: int
    fail_count: int
    average_test_pnl: Decimal
    average_profit_factor: Decimal
    average_max_drawdown: Decimal
    worst_test_pnl: Decimal
    worst_drawdown: Decimal
    average_closed_trades: Decimal
    consistency_score: int


def summarize_multi_month(
    results: list[MultiMonthDatasetResult],
) -> MultiMonthSummary:
    """Aggregate walk-forward test-window metrics across datasets."""

    dataset_count = len(results)
    pass_count = sum(1 for result in results if result.walk_forward_result.verdict == "PASS")
    fail_count = dataset_count - pass_count
    test_results = [
        result.walk_forward_result.test_result
        for result in results
        if result.walk_forward_result.test_result is not None
    ]
    if not test_results:
        return MultiMonthSummary(
            dataset_count=dataset_count,
            pass_count=pass_count,
            fail_count=fail_count,
            average_test_pnl=Decimal("0"),
            average_profit_factor=Decimal("0"),
            average_max_drawdown=Decimal("0"),
            worst_test_pnl=Decimal("0"),
            worst_drawdown=Decimal("0"),
            average_closed_trades=Decimal("0"),
            consistency_score=max(0, 100 - 20 * fail_count - 10),
        )

    average_test_pnl = _average([result.total_pnl for result in test_results])
    average_profit_factor = _average([result.profit_factor for result in test_results])
    average_max_drawdown = _average([result.max_drawdown for result in test_results])
    worst_test_pnl = min(result.total_pnl for result in test_results)
    worst_drawdown = max(result.max_drawdown for result in test_results)
    average_closed_trades = _average(
        [Decimal(result.closed_trades) for result in test_results]
    )
    score = _consistency_score(
        fail_count=fail_count,
        average_profit_factor=average_profit_factor,
        average_closed_trades=average_closed_trades,
        worst_drawdown=worst_drawdown,
        average_test_pnl=average_test_pnl,
    )
    return MultiMonthSummary(
        dataset_count=dataset_count,
        pass_count=pass_count,
        fail_count=fail_count,
        average_test_pnl=average_test_pnl,
        average_profit_factor=average_profit_factor,
        average_max_drawdown=average_max_drawdown,
        worst_test_pnl=worst_test_pnl,
        worst_drawdown=worst_drawdown,
        average_closed_trades=average_closed_trades,
        consistency_score=score,
    )


def write_multi_month_report(
    *,
    results: list[MultiMonthDatasetResult],
    path: str | Path,
) -> MultiMonthSummary:
    """Write a markdown report and return its aggregate summary."""

    summary = summarize_multi_month(results)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Multi-Month Evaluation Report",
        "",
        "## Aggregate Summary",
        "",
        f"- Number of datasets: {summary.dataset_count}",
        f"- PASS count: {summary.pass_count}",
        f"- FAIL count: {summary.fail_count}",
        f"- Average test PnL: {summary.average_test_pnl}",
        f"- Average profit factor: {summary.average_profit_factor}",
        f"- Average max drawdown: {summary.average_max_drawdown}",
        f"- Worst test PnL: {summary.worst_test_pnl}",
        f"- Worst drawdown: {summary.worst_drawdown}",
        f"- Average closed trades: {summary.average_closed_trades}",
        f"- Consistency score: {summary.consistency_score}",
        "",
        "## Dataset Results",
        "",
        "| Dataset | Test Total PnL | Test Profit Factor | Max Drawdown | Closed Trades | Verdict | Selected Entry Threshold | Selected Exit Threshold |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        walk_forward = result.walk_forward_result
        test_result = walk_forward.test_result
        candidate = walk_forward.best_candidate
        lines.append(
            "| "
            + " | ".join(
                [
                    result.dataset_path,
                    str(test_result.total_pnl if test_result else Decimal("0")),
                    str(test_result.profit_factor if test_result else Decimal("0")),
                    str(test_result.max_drawdown if test_result else Decimal("0")),
                    str(test_result.closed_trades if test_result else 0),
                    walk_forward.verdict,
                    str(_selected_entry_threshold(candidate.strategy_config) if candidate else ""),
                    str(_selected_exit_threshold(candidate.strategy_config) if candidate else ""),
                ]
            )
            + " |"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _selected_entry_threshold(strategy_config: StrategyConfig) -> Decimal:
    if strategy_config.strategy_mode == StrategyMode.MOMENTUM_BREAKOUT:
        return strategy_config.breakout_threshold_pct
    return strategy_config.entry_threshold_pct


def _selected_exit_threshold(strategy_config: StrategyConfig) -> Decimal:
    if strategy_config.strategy_mode == StrategyMode.MOMENTUM_BREAKOUT:
        return strategy_config.momentum_exit_threshold_pct
    return strategy_config.exit_threshold_pct


def _consistency_score(
    *,
    fail_count: int,
    average_profit_factor: Decimal,
    average_closed_trades: Decimal,
    worst_drawdown: Decimal,
    average_test_pnl: Decimal,
) -> int:
    score = 100 - 20 * fail_count
    if average_profit_factor < Decimal("1.2"):
        score -= 10
    if average_closed_trades < Decimal("10"):
        score -= 10
    if worst_drawdown > average_test_pnl * Decimal("3"):
        score -= 10
    return max(0, score)
