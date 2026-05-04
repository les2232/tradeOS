"""Walk-forward evaluation helpers.

This module keeps walk-forward selection deterministic and offline. It does not
connect to brokers or submit orders.
"""

from dataclasses import dataclass
from decimal import Decimal

from tradeos.backtest.runner import BacktestResult
from tradeos.config.schema import StrategyConfig
from tradeos.domain.models import Bar
from tradeos.research.experiment import ExperimentResult


@dataclass(frozen=True)
class WalkForwardCandidate:
    """One parameter combination evaluated on the selection window."""

    strategy_config: StrategyConfig
    backtest_result: BacktestResult
    experiment_result: ExperimentResult


@dataclass(frozen=True)
class WalkForwardResult:
    """Walk-forward selection and test-window outcome."""

    selection_bars: int
    test_bars: int
    test_warmup_bars: int
    best_candidate: WalkForwardCandidate | None
    test_result: BacktestResult | None
    verdict: str


def split_walk_forward_bars(bars: list[Bar]) -> tuple[list[Bar], list[Bar]]:
    """Split bars by timestamp order into 60% selection and 40% test windows."""

    ordered_bars = sorted(bars, key=lambda bar: bar.timestamp)
    split_index = int(len(ordered_bars) * Decimal("0.6"))
    return ordered_bars[:split_index], ordered_bars[split_index:]


def select_best_candidate(
    candidates: list[WalkForwardCandidate],
) -> WalkForwardCandidate | None:
    """Return the highest-scored selection candidate."""

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.experiment_result.score)


def walk_forward_verdict(test_result: BacktestResult | None) -> str:
    """Return PASS when out-of-sample performance meets the simple rule."""

    if test_result is None:
        return "FAIL"
    if test_result.total_pnl > Decimal("0") and test_result.closed_trades >= 2:
        return "PASS"
    return "FAIL"


def build_warmup_history(bars: list[Bar]) -> dict[str, list[Bar]]:
    """Group selection-window bars by symbol for test-window SMA warmup."""

    history: dict[str, list[Bar]] = {}
    for bar in bars:
        history.setdefault(bar.symbol, []).append(bar)
    return history
