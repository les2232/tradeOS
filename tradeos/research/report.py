"""Markdown reporting for offline research results."""

from pathlib import Path
from collections.abc import Iterable

from tradeos.research.experiment import ExperimentResult


def write_markdown_report(results: Iterable[ExperimentResult], path: str | Path) -> None:
    """Write a compact markdown summary of research experiment results."""

    ranked = sorted(results, key=lambda result: result.score, reverse=True)
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# TradeOS V3 Research Report",
        "",
        f"Total experiments: {len(ranked)}",
        f"Best score: {ranked[0].score if ranked else 'n/a'}",
        "",
        "## Top Results",
        "",
        "| Rank | Experiment ID | Strategy | Symbols | Score | PF | Trades | Net PnL |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]

    for index, result in enumerate(ranked[:10], start=1):
        lines.append(
            "| "
            f"{index} | {result.experiment_id} | {result.spec.strategy_mode.value} | "
            f"{', '.join(result.spec.symbols)} | {result.score} | "
            f"{result.profit_factor} | {result.num_trades} | {result.net_pnl} |"
        )

    lines.extend(["", "## Rejected Or Low Quality Warnings", ""])
    warned = [result for result in ranked if result.warnings]
    if not warned:
        lines.append("No warnings.")
    else:
        for result in warned:
            lines.append(f"- {result.experiment_id}: {', '.join(result.warnings)}")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
