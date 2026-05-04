"""Deterministic research scoring.

Scoring is intentionally simple and local; it does not call AI services or run
backtests.
"""

from decimal import Decimal

from tradeos.research.experiment import ExperimentResult


def score_result(result: ExperimentResult) -> Decimal:
    """Score an experiment result using deterministic quality heuristics."""

    score = Decimal("0")
    capped_pf = min(result.profit_factor, Decimal("3.0"))
    score += capped_pf * Decimal("25")

    if result.expectancy > Decimal("0"):
        score += min(result.expectancy, Decimal("5")) * Decimal("10")
    else:
        score += result.expectancy * Decimal("20")

    if result.profit_factor < Decimal("1.0"):
        score -= (Decimal("1.0") - result.profit_factor) * Decimal("50")

    if result.num_trades < 30:
        score -= Decimal(30 - result.num_trades)

    score -= result.max_drawdown * Decimal("2")
    return score.quantize(Decimal("0.01"))


def generate_warnings(result: ExperimentResult) -> list[str]:
    """Generate deterministic warnings for low-quality or suspicious results."""

    warnings: list[str] = []
    if result.num_trades < 30:
        warnings.append("low_trade_count")
    if result.profit_factor > Decimal("3") and result.num_trades < 50:
        warnings.append("high_profit_factor_low_sample")
    if result.expectancy <= Decimal("0"):
        warnings.append("non_positive_expectancy")
    if result.net_pnl <= Decimal("0") and result.max_drawdown > Decimal("0"):
        warnings.append("drawdown_high_relative_to_net_pnl")
    elif result.net_pnl > Decimal("0") and result.max_drawdown >= result.net_pnl:
        warnings.append("drawdown_high_relative_to_net_pnl")
    return warnings
