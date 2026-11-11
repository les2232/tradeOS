"""Decide whether a paper session can be compared with a research result.

Comparison metrics are only produced when every comparability check passes.
An invalid comparison returns the failed checks and no metrics at all, so no
downstream explanation can quote numbers against an unverified baseline.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from tradeos.analyst.evidence import MIN_CLOSED_TRADES
from tradeos.research.experiment import ExperimentResult


SYNTHETIC_MARKERS = ("sample", "synthetic")


def compare_session_to_experiment(evidence: dict[str, Any], experiment: ExperimentResult) -> dict[str, Any]:
    identity = evidence.get("identity") or {}
    config = identity.get("config")
    spec = experiment.spec
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    if not config:
        check("session_configuration", False, "The scope has no recorded session configuration to compare.")
    else:
        strategy = config.get("strategy") or {}
        session_mode = strategy.get("strategy_mode")
        check("strategy_mode", session_mode == spec.strategy_mode.value,
              f"session {session_mode} vs research {spec.strategy_mode.value}")
        check("timeframe", config.get("timeframe") == spec.timeframe,
              f"session {config.get('timeframe')} vs research {spec.timeframe}")
        session_symbols = sorted(config.get("watchlist") or [])
        check("symbols", session_symbols == sorted(spec.symbols),
              f"session {', '.join(session_symbols) or 'none'} vs research {', '.join(sorted(spec.symbols))}")
        mismatched = [
            f"{key}: session {strategy.get(key)} vs research {value}"
            for key, value in sorted(spec.parameters.items())
            if key in strategy and _normal(strategy.get(key)) != _normal(value)
        ]
        missing = sorted(key for key in spec.parameters if key not in strategy)
        check("parameters", not mismatched and not missing,
              "; ".join(mismatched + [f"{key}: not in session configuration" for key in missing]) or "all research parameters match")
    if identity.get("config_fingerprint_verified") is False:
        check("session_integrity", False, "Session settings do not match their confirmed fingerprint.")

    dataset = spec.dataset_id.lower()
    check("research_dataset", not any(marker in dataset for marker in SYNTHETIC_MARKERS),
          f"dataset {spec.dataset_id}" + (" looks synthetic; it shows software behavior, not market behavior" if any(m in dataset for m in SYNTHETIC_MARKERS) else ""))
    check("research_sample", experiment.num_trades >= MIN_CLOSED_TRADES,
          f"{experiment.num_trades} research trades (minimum {MIN_CLOSED_TRADES})")

    valid = all(item["ok"] for item in checks)
    metrics = evidence.get("metrics") or {}
    session_trades = int(metrics.get("closed_trades") or 0)
    result: dict[str, Any] = {
        "experiment_id": experiment.experiment_id,
        "valid": valid,
        "checks": checks,
        "research_warnings": list(experiment.warnings),
        "notes": [
            "Research results do not record their commission and slippage assumptions; cost parity is unverified.",
        ],
    }
    started = (identity.get("started_at") or "")[:10]
    if started and spec.start_date.isoformat() <= started <= spec.end_date.isoformat():
        result["notes"].append("The session date falls inside the research period, so this is not an out-of-sample check.")

    if not valid:
        result["metrics"] = None
        result["suppressed_reason"] = "Comparison metrics are withheld because at least one check failed."
        return result

    result["conclusive"] = session_trades >= MIN_CLOSED_TRADES
    if not result["conclusive"]:
        result["notes"].append(
            f"The session has {session_trades} closed trade(s); differences are not conclusive below {MIN_CLOSED_TRADES}."
        )
    result["metrics"] = {
        "closed_trades": {"session": session_trades, "research": experiment.num_trades},
        "win_rate": {"session": metrics.get("win_rate"), "research": str(experiment.win_rate)},
        "expectancy_per_trade": {"session": metrics.get("expectancy_per_closed_trade"), "research": str(experiment.expectancy)},
        "net_pnl": {"session": metrics.get("realized_pnl_from_matched_fills"), "research": str(experiment.net_pnl)},
    }
    return result


def _normal(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    text = str(value).strip().lower()
    if text in {"true", "false"}:
        return text
    try:
        return str(Decimal(text).normalize())
    except InvalidOperation:
        return text
