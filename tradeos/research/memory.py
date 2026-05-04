"""JSONL research memory store.

This module persists offline experiment results as one JSON object per line.
"""

from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from tradeos.domain.enums import StrategyMode
from tradeos.research.experiment import ExperimentResult, ExperimentSpec


class ResearchMemory:
    """Simple JSONL store for deduplicated research experiment results."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def has_experiment(self, experiment_id: str) -> bool:
        """Return true when an experiment ID already exists in memory."""

        return any(result.experiment_id == experiment_id for result in self.load_results())

    def save_result(self, result: ExperimentResult) -> None:
        """Append a result unless its experiment ID already exists."""

        if self.has_experiment(result.experiment_id):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as memory_file:
            memory_file.write(json.dumps(_result_to_json(result), sort_keys=True) + "\n")

    def load_results(self) -> list[ExperimentResult]:
        """Load all persisted experiment results."""

        if not self.path.exists():
            return []

        results: list[ExperimentResult] = []
        with self.path.open("r", encoding="utf-8") as memory_file:
            for line in memory_file:
                if line.strip():
                    results.append(_result_from_json(json.loads(line)))
        return results

    def rank_results(self) -> list[ExperimentResult]:
        """Return results sorted by score descending."""

        return sorted(self.load_results(), key=lambda result: result.score, reverse=True)


def _result_to_json(result: ExperimentResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["spec"]["strategy_mode"] = result.spec.strategy_mode.value
    payload["spec"]["start_date"] = result.spec.start_date.isoformat()
    payload["spec"]["end_date"] = result.spec.end_date.isoformat()
    for field_name in (
        "profit_factor",
        "max_drawdown",
        "win_rate",
        "expectancy",
        "net_pnl",
        "score",
    ):
        payload[field_name] = str(getattr(result, field_name))
    return payload


def _result_from_json(payload: dict[str, Any]) -> ExperimentResult:
    spec_payload = payload["spec"]
    spec = ExperimentSpec(
        strategy_mode=StrategyMode(spec_payload["strategy_mode"]),
        symbols=list(spec_payload["symbols"]),
        start_date=date.fromisoformat(spec_payload["start_date"]),
        end_date=date.fromisoformat(spec_payload["end_date"]),
        timeframe=spec_payload["timeframe"],
        parameters=dict(spec_payload["parameters"]),
        dataset_id=spec_payload["dataset_id"],
    )
    return ExperimentResult(
        experiment_id=payload["experiment_id"],
        spec=spec,
        profit_factor=Decimal(payload["profit_factor"]),
        max_drawdown=Decimal(payload["max_drawdown"]),
        win_rate=Decimal(payload["win_rate"]),
        num_trades=int(payload["num_trades"]),
        expectancy=Decimal(payload["expectancy"]),
        net_pnl=Decimal(payload["net_pnl"]),
        score=Decimal(payload["score"]),
        warnings=list(payload.get("warnings", [])),
    )
