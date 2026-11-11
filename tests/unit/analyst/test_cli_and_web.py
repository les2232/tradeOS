import json
import socket

import pytest

from tradeos.analyst import __main__ as cli
from tradeos.analyst.claude import AnalystUnavailable
from tradeos.research.memory import ResearchMemory
from tradeos.web.app import create_app

from tests.unit.analyst.conftest import EVALUATION, cycle, experiment, run_session, session_end, session_start, write_log

HEADERS = {"X-TradeOS-Request": "analyst"}


@pytest.fixture
def log(tmp_path):
    return write_log(tmp_path / "paper.sqlite3", [
        session_start("s-cli"),
        *cycle("AAPL", "hold", "mean_reversion_no_signal"),
        EVALUATION,
        session_end(),
    ])


class FakeExplainer:
    name = "claude"

    def __init__(self, error=None):
        self.error = error

    def explain(self, evidence, question, comparison=None):
        if self.error:
            raise self.error
        return {"source": "claude", "model": "claude-opus-5-5", "fallback_used": False, "question": question,
                "explanation": {"summary": "fake", "findings": [], "hypotheses": [], "caveats": [], "next_steps": []}, "rejected": []}


def test_cli_lists_and_explains(log, capsys):
    assert cli.main(["sessions", "--db", str(log)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["session_id"] == "s-cli"

    assert cli.main(["explain", "--db", str(log), "--symbol", "AAPL"]) == 0
    out = capsys.readouterr().out
    assert "Why did AAPL buy, sell, or hold" in out and "mean_reversion_no_signal" in out and "[E" in out

    assert cli.main(["explain", "--db", str(log), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ai"] is None


def test_cli_compare_and_errors(log, tmp_path, capsys):
    memory = tmp_path / "experiments.jsonl"
    ResearchMemory(memory).save_result(experiment(dataset="sample_multisymbol_bars.csv"))
    assert cli.main(["explain", "--db", str(log), "--compare", "exp-1", "--research-memory", str(memory)]) == 0
    assert "NOT valid" in capsys.readouterr().out

    assert cli.main(["explain", "--db", str(tmp_path / "missing.sqlite3")]) == 2
    assert "No events recorded" in capsys.readouterr().err
    assert cli.main(["explain", "--db", str(log), "--compare", "nope", "--research-memory", str(memory)]) == 2


def test_cli_claude_flag(log, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ClaudeExplainer", FakeExplainer)
    assert cli.main(["explain", "--db", str(log), "--claude"]) == 0
    assert "== Claude (claude-opus-5-5) ==" in capsys.readouterr().out


@pytest.fixture
def web(supervised, tmp_path, monkeypatch):
    supervisor, broker = supervised
    run_session(supervisor, cycles=1)

    def forbidden(*args, **kwargs):
        pytest.fail("Analyst attempted network access")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    broker.reset_mock()
    memory = tmp_path / "experiments.jsonl"
    ResearchMemory(memory).save_result(experiment(symbols=("AAPL",)))
    # Matches config/paper.yaml, which the supervised session ran with.
    ResearchMemory(memory).save_result(experiment(symbols=("AAPL",), experiment_id="exp-paper", parameters={"entry_threshold_pct": "1.0", "exit_threshold_pct": "0.3"}))
    explainers = []

    def factory():
        explainers.append(FakeExplainer(**web_state))
        return explainers[-1]

    web_state = {}
    app = create_app(paper_supervisor=supervisor, research_memory=memory, analyst_explainer_factory=factory)
    app.testing = True
    yield app.test_client(), web_state, broker


def test_web_sessions_and_local_explanation(web):
    client, _, broker = web
    listing = client.get("/api/analyst/sessions").json
    assert len(listing["sessions"]) == 1
    assert {item["experiment_id"] for item in listing["research"]} == {"exp-1", "exp-paper"}
    session_id = listing["sessions"][0]["session_id"]

    response = client.post("/api/analyst/explain", json={"kind": "symbol", "symbol": "AAPL", "session_id": session_id}, headers=HEADERS)
    assert response.status_code == 200
    data = response.json
    assert data["ai"] is None and data["local"]["explanation"]["findings"]
    assert data["evidence"]["identity"]["session_id"] == session_id

    mismatched = client.post("/api/analyst/explain", json={"kind": "comparison", "experiment_id": "exp-1"}, headers=HEADERS).json["comparison"]
    assert mismatched["valid"] is False and mismatched["metrics"] is None
    assert [item["check"] for item in mismatched["checks"] if not item["ok"]] == ["parameters"]
    matched = client.post("/api/analyst/explain", json={"kind": "comparison", "experiment_id": "exp-paper"}, headers=HEADERS).json["comparison"]
    assert matched["valid"] is True and matched["metrics"]["closed_trades"]["session"] == 0
    # Reading the analyst never talks to the broker.
    assert broker.method_calls == []


def test_web_claude_and_request_guards(web):
    client, state, _ = web
    assert client.post("/api/analyst/explain", json={"use_claude": True}, headers=HEADERS).json["ai"]["explanation"]["summary"] == "fake"
    state["error"] = AnalystUnavailable("Claude is not configured on this server.")
    unavailable = client.post("/api/analyst/explain", json={"use_claude": True}, headers=HEADERS)
    assert unavailable.status_code == 503 and "not configured" in unavailable.json["error"]

    assert client.post("/api/analyst/explain", json={}).status_code == 400
    assert client.post("/api/analyst/explain", json={"db": "/etc/passwd"}, headers=HEADERS).status_code == 400
    assert client.post("/api/analyst/explain", json={"use_claude": "yes"}, headers=HEADERS).status_code == 400
    assert client.post("/api/analyst/explain", json={"kind": "symbol"}, headers=HEADERS).status_code == 400
    assert client.post("/api/analyst/explain", json={"session_id": "nope"}, headers=HEADERS).status_code == 400


def test_dashboard_serves_analyst_panel(web):
    client, _, _ = web
    page = client.get("/").get_data(as_text=True)
    assert 'id="analyst"' in page and "analyst.js" in page
    script = client.get("/static/analyst.js").get_data(as_text=True)
    assert "innerHTML" not in script
