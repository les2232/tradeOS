"""One entry point used by the CLI and the browser dashboard."""

from pathlib import Path
from typing import Any

from tradeos.analyst.comparison import compare_session_to_experiment
from tradeos.analyst.evidence import build_evidence
from tradeos.analyst.explain import RuleExplainer, question_for
from tradeos.analyst.sessions import list_sessions
from tradeos.research.memory import ResearchMemory
from tradeos.storage.sqlite import read_events_readonly


def sessions(db_path: str | Path) -> list[dict[str, Any]]:
    return list_sessions(read_events_readonly(db_path))


def research_results(memory_path: str | Path) -> list[dict[str, Any]]:
    return [
        {
            "experiment_id": result.experiment_id,
            "strategy_mode": result.spec.strategy_mode.value,
            "symbols": result.spec.symbols,
            "dataset_id": result.spec.dataset_id,
            "num_trades": result.num_trades,
            "score": str(result.score),
        }
        for result in ResearchMemory(memory_path).rank_results()
    ]


def analyze(
    db_path: str | Path,
    *,
    kind: str = "session",
    session_id: str | None = None,
    symbol: str | None = None,
    experiment_id: str | None = None,
    research_memory: str | Path | None = None,
    explainer: Any = None,
) -> dict[str, Any]:
    """Build evidence, an optional comparison, and an explanation.

    The rule-based explanation is always included. When ``explainer`` is
    given (for example ClaudeExplainer), its answer is added alongside.
    """

    question = question_for(kind, symbol=symbol, experiment_id=experiment_id)
    events = read_events_readonly(db_path)
    if not events:
        raise ValueError("No events recorded yet. Run a paper check or session first.")
    evidence = build_evidence(
        events,
        session_id=session_id,
        symbol=symbol if kind == "symbol" else None,
        source_label=Path(db_path).name,
    )

    comparison = None
    if kind == "comparison":
        if research_memory is None:
            raise ValueError("No research memory file is configured")
        matches = [item for item in ResearchMemory(research_memory).load_results() if item.experiment_id == experiment_id]
        if not matches:
            raise ValueError(f"Research result {experiment_id} is not in {Path(research_memory).name}")
        comparison = compare_session_to_experiment(evidence, matches[0])

    result = {
        "question": question,
        "evidence": evidence,
        "comparison": comparison,
        "local": RuleExplainer().explain(evidence, question, comparison),
        "ai": None,
    }
    if explainer is not None:
        result["ai"] = explainer.explain(evidence, question, comparison)
    return result
