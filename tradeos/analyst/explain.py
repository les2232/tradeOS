"""Explanations built from an evidence package.

Both explainers return the same shape. ``RuleExplainer`` is deterministic and
offline; ``ClaudeExplainer`` (tradeos.analyst.claude) asks a model to write
the explanation. Either way, ``validate_explanation`` removes any cited ref
that is not in the evidence package and rejects findings left without one.
"""

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from tradeos.analyst.evidence import collect_refs


QUESTIONS = {
    "session": "Explain this session: what the bot did, why it did it, and what stands out.",
    "symbol": "Why did {symbol} buy, sell, or hold during this session?",
    "comparison": "Can this session be validly compared with research result {experiment_id}, and what does the comparison show?",
}

EXPLANATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["statement", "refs"],
                "additionalProperties": False,
            },
        },
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                    "how_to_check": {"type": "string"},
                },
                "required": ["statement", "refs", "how_to_check"],
                "additionalProperties": False,
            },
        },
        "caveats": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "findings", "hypotheses", "caveats", "next_steps"],
    "additionalProperties": False,
}


def question_for(kind: str, symbol: str | None = None, experiment_id: str | None = None) -> str:
    if kind not in QUESTIONS:
        raise ValueError("Question must be session, symbol or comparison")
    if kind == "symbol" and not symbol:
        raise ValueError("Choose a symbol to explain")
    if kind == "comparison" and not experiment_id:
        raise ValueError("Choose a research result to compare with")
    return QUESTIONS[kind].format(symbol=symbol, experiment_id=experiment_id)


def validate_explanation(explanation: dict[str, Any], evidence: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Keep only refs present in the evidence; reject findings with none left."""

    known = collect_refs(evidence)
    rejected: list[dict[str, Any]] = []
    cleaned = {
        "summary": str(explanation.get("summary", "")),
        "findings": [],
        "hypotheses": [],
        "caveats": [str(item) for item in explanation.get("caveats", [])],
        "next_steps": [str(item) for item in explanation.get("next_steps", [])],
    }
    for finding in explanation.get("findings", []):
        refs = [item for item in finding.get("refs", []) if item in known]
        unknown = [item for item in finding.get("refs", []) if item not in known]
        if not refs:
            rejected.append({"statement": finding.get("statement"), "reason": "cites no event ref from the evidence", "unknown_refs": unknown})
            continue
        cleaned["findings"].append({"statement": str(finding.get("statement", "")), "refs": refs})
        if unknown:
            rejected.append({"statement": finding.get("statement"), "reason": "some cited refs are not in the evidence and were removed", "unknown_refs": unknown})
    for hypothesis in explanation.get("hypotheses", []):
        refs = [item for item in hypothesis.get("refs", []) if item in known]
        cleaned["hypotheses"].append({
            "statement": str(hypothesis.get("statement", "")),
            "refs": refs,
            "how_to_check": str(hypothesis.get("how_to_check", "")),
        })
    return cleaned, rejected


class RuleExplainer:
    """Plain-English explanation from the evidence alone. Never speculates."""

    name = "rules"

    def explain(self, evidence: dict[str, Any], question: str, comparison: dict[str, Any] | None = None) -> dict[str, Any]:
        identity = evidence["identity"]
        metrics = evidence["metrics"]
        anchor = [item for item in (identity.get("started_ref"), identity.get("ended_ref")) if item] or [
            item for item in (evidence["source"]["window_first_ref"], evidence["source"]["window_last_ref"]) if item
        ]
        findings: list[dict[str, Any]] = []
        decisions = metrics["decisions"]

        signals = metrics["signals"]
        if decisions:
            findings.append({
                "statement": (
                    f"The bot made {decisions} decision(s): {signals.get('buy', 0)} buy, "
                    f"{signals.get('sell', 0)} sell and {signals.get('hold', 0)} hold."
                ),
                "refs": anchor,
            })
            reasons = Counter(metrics["signal_reasons"]).most_common(1)
            if reasons:
                reason, count = reasons[0]
                refs = _decision_refs(evidence, lambda item: item["detail"].get("signal_reason") == reason) or anchor
                findings.append({"statement": f"The most common strategy reason was {reason} ({count} of {decisions}).", "refs": refs})
        if metrics["risk"].get("blocked"):
            reasons = ", ".join(f"{key} ({value})" for key, value in metrics["risk_reasons"].items())
            refs = _decision_refs(evidence, lambda item: item["detail"].get("risk_status") == "blocked") or anchor
            findings.append({"statement": f"The risk manager blocked {metrics['risk']['blocked']} decision(s): {reasons}.", "refs": refs})
        for block in evidence["blocks"][:5]:
            findings.append({"statement": f"A check was blocked: {block['reason']}", "refs": [block["ref"]]})
        if metrics["paper_submission_attempts"]:
            findings.append({
                "statement": (
                    f"{metrics['paper_submission_attempts']} paper order(s) were attempted and "
                    f"{metrics['fills']} have recorded fills."
                ),
                "refs": [item["ref"] for item in evidence["notable_events"] if item["type"] == "paper_order"][:10],
            })
        for trade in evidence["trades"][:10]:
            findings.append({
                "statement": (
                    f"{trade['symbol']}: {trade['quantity']} shares bought at {trade['entry_avg_price']} "
                    f"and sold at {trade['exit_price']}, P&L {trade['pnl']} before fees."
                ),
                "refs": [*trade["entry_refs"], trade["exit_ref"]],
            })
        detail = evidence.get("symbol_detail")
        if detail:
            findings.extend(_symbol_findings(detail, anchor))
        if identity.get("end_state"):
            reason = f" ({identity['ended_reason']})" if identity.get("ended_reason") else ""
            findings.append({"statement": f"The session ended in state {identity['end_state']}{reason}.", "refs": [identity["ended_ref"]]})

        summary = _summary(evidence)
        caveats = [item["message"] for item in evidence["limitations"]]
        next_steps: list[str] = []
        if comparison is not None:
            if comparison["valid"]:
                summary += " The research comparison passed its checks" + ("." if comparison.get("conclusive") else ", but the sample is too small to be conclusive.")
            else:
                failed = [item["detail"] for item in comparison["checks"] if not item["ok"]]
                summary += " The research comparison is not valid, so no performance numbers are compared."
                caveats.extend(f"Comparison check failed: {item}" for item in failed)
            caveats.extend(comparison["notes"])
        if any(item["code"] == "small_sample" for item in evidence["limitations"]):
            next_steps.append("Collect more closed trades before judging the strategy's edge.")
        explanation = {"summary": summary, "findings": findings, "hypotheses": [], "caveats": caveats, "next_steps": next_steps}
        cleaned, rejected = validate_explanation(explanation, evidence)
        return {"source": self.name, "model": None, "question": question, "explanation": cleaned, "rejected": rejected}


def _summary(evidence: dict[str, Any]) -> str:
    identity, metrics = evidence["identity"], evidence["metrics"]
    if identity["scope"] == "session":
        config = identity.get("config") or {}
        strategy = (config.get("strategy") or {}).get("strategy_mode", "unknown strategy")
        watchlist = ", ".join(config.get("watchlist") or []) or "no watchlist"
        head = f"Session {identity['session_id'][:8]} ran {strategy} on {watchlist}."
    else:
        head = "Activity outside supervised paper sessions."
    if not metrics["decisions"]:
        return head + " No trading decisions were recorded."
    pnl = metrics["realized_pnl_from_matched_fills"]
    pnl_text = f" Realized P&L from matched fills: {pnl}." if pnl is not None else " No round trip closed."
    return (
        head
        + f" {metrics['decisions']} decisions, {metrics['paper_submission_attempts']} paper order attempt(s), "
        + f"{metrics['fills']} fill(s), {metrics['closed_trades']} closed trade(s)."
        + pnl_text
    )


def _symbol_findings(detail: dict[str, Any], anchor: list[str]) -> list[dict[str, Any]]:
    symbol = detail["symbol"]
    if detail["in_watchlist"] is False:
        return [{"statement": f"{symbol} is not in this session's watchlist, so the bot never evaluated it.", "refs": anchor}]
    rows = detail["recent_decisions"]
    if not rows:
        return [{"statement": f"No decisions were recorded for {symbol}.", "refs": anchor}]
    findings = []
    counts = Counter(row["action"] for row in rows)
    findings.append({
        "statement": f"Across the last {len(rows)} {symbol} decision(s): " + ", ".join(f"{count} {action}" for action, count in sorted(counts.items())) + ".",
        "refs": [row["ref"] for row in rows][:10],
    })
    latest = rows[-1]
    text = f"Latest {symbol} decision: {latest['action']} because {latest['signal_reason']}"
    inputs = latest.get("strategy_inputs") or {}
    deviation, entry, exit_ = (_pct(inputs.get(key)) for key in ("deviation_pct", "entry_threshold_pct", "exit_threshold_pct"))
    if deviation is not None and entry is not None and exit_ is not None:
        text += (
            f"; price was {deviation} from its SMA, versus a buy trigger at -{entry} and a sell trigger at +{exit_}"
        )
    if latest["risk_status"] == "blocked":
        text += f"; the risk manager blocked it ({latest['risk_reason']})"
    findings.append({"statement": text + ".", "refs": [latest["ref"]]})
    return findings


def _decision_refs(evidence: dict[str, Any], predicate) -> list[str]:
    return [item["ref"] for item in evidence["notable_events"] if item["type"] != "paper_order" and "detail" in item and predicate(item)][:10]


def _pct(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return f"{(Decimal(str(value)) * 100).quantize(Decimal('0.01'))}%"
    except InvalidOperation:
        return None
