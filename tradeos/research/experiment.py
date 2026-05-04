"""Research experiment contracts.

This module defines immutable experiment specifications and results for offline
analysis.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import hashlib
import json
from typing import Any

from tradeos.domain.enums import StrategyMode


Parameters = dict[str, Any]


@dataclass(frozen=True)
class ExperimentSpec:
    """Immutable description of an offline research experiment."""

    strategy_mode: StrategyMode
    symbols: list[str]
    start_date: date
    end_date: date
    timeframe: str
    parameters: Parameters
    dataset_id: str


@dataclass(frozen=True)
class ExperimentResult:
    """Scored result from an offline research experiment."""

    experiment_id: str
    spec: ExperimentSpec
    profit_factor: Decimal
    max_drawdown: Decimal
    win_rate: Decimal
    num_trades: int
    expectancy: Decimal
    net_pnl: Decimal
    score: Decimal
    warnings: list[str] = field(default_factory=list)


def make_experiment_id(spec: ExperimentSpec) -> str:
    """Create a deterministic experiment ID from the stable spec fields."""

    payload = {
        "strategy_mode": spec.strategy_mode.value,
        "symbols": sorted(spec.symbols),
        "start_date": spec.start_date.isoformat(),
        "end_date": spec.end_date.isoformat(),
        "timeframe": spec.timeframe,
        "dataset_id": spec.dataset_id,
        "parameters": _stable_parameters(spec.parameters),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _stable_parameters(parameters: Parameters) -> Parameters:
    return json.loads(json.dumps(parameters, sort_keys=True, default=str))
