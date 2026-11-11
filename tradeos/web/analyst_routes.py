"""Read-only analyst endpoints. They read the paper event log; nothing here touches the broker."""

import importlib.util
from pathlib import Path
from threading import Lock

from flask import jsonify, request

from tradeos.analyst import service
from tradeos.analyst.claude import AnalystUnavailable, ClaudeExplainer


ALLOWED_FIELDS = {"kind", "session_id", "symbol", "experiment_id", "use_claude"}


def register_analyst_routes(app, events_path, research_memory, explainer_factory=None) -> None:
    events_path = Path(events_path)
    research_memory = Path(research_memory)
    explainer_factory = explainer_factory or ClaudeExplainer
    claude_lock = Lock()

    @app.get("/api/analyst/sessions")
    def analyst_sessions():
        return jsonify(
            sessions=service.sessions(events_path),
            research=service.research_results(research_memory) if research_memory.exists() else [],
            claude_installed=importlib.util.find_spec("anthropic") is not None,
        )

    @app.post("/api/analyst/explain")
    def analyst_explain():
        if request.headers.get("X-TradeOS-Request") != "analyst" or not request.is_json:
            return jsonify(error="Use the analyst panel to ask a question"), 400
        payload = request.get_json()
        if not isinstance(payload, dict) or payload.keys() - ALLOWED_FIELDS:
            return jsonify(error="Unsupported analyst request"), 400
        try:
            fields = {name: _text(payload.get(name), name) for name in ("kind", "session_id", "symbol", "experiment_id")}
            use_claude = payload.get("use_claude", False)
            if not isinstance(use_claude, bool):
                raise ValueError("use_claude must be true or false")
            if not use_claude:
                return jsonify(_analyze(fields, None))
            # One model request at a time per server keeps spend predictable.
            if not claude_lock.acquire(blocking=False):
                return jsonify(error="Claude is already answering a question. Try again shortly."), 429
            try:
                return jsonify(_analyze(fields, explainer_factory()))
            finally:
                claude_lock.release()
        except AnalystUnavailable as exc:
            return jsonify(error=str(exc)), 503
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    def _analyze(fields, explainer):
        return service.analyze(
            events_path,
            kind=fields["kind"] or "session",
            session_id=fields["session_id"],
            symbol=fields["symbol"],
            experiment_id=fields["experiment_id"],
            research_memory=research_memory,
            explainer=explainer,
        )


def _text(value, name):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError(f"{name} must be a short text value")
    return value.strip()
