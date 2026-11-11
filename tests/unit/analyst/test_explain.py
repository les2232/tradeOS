import json
from types import SimpleNamespace as NS

import pytest

from tradeos.analyst.claude import DEFAULT_MODEL, FALLBACK_BETA, AnalystUnavailable, ClaudeExplainer
from tradeos.analyst.comparison import compare_session_to_experiment
from tradeos.analyst.evidence import build_evidence, collect_refs
from tradeos.analyst.explain import RuleExplainer, question_for, validate_explanation

from tests.unit.analyst.conftest import (
    EVALUATION, cycle, events_of, experiment, order, session_end, session_start, write_log,
)


@pytest.fixture
def evidence(tmp_path):
    path = write_log(tmp_path / "e.sqlite3", [
        session_start(),
        *cycle("AAPL", "hold", "mean_reversion_no_signal"),
        *cycle("MSFT", "buy", "mean_reversion_entry_threshold_crossed", risk="blocked", risk_reason="max_open_positions_reached", rules=["max_open_positions"]),
        EVALUATION,
        *cycle("AAPL", "buy", "mean_reversion_entry_threshold_crossed", bar="2026-11-02T15:15:00+00:00", order=True,
               metadata={"deviation_pct": "-0.0125", "entry_threshold_pct": "0.01", "exit_threshold_pct": "0.003"}),
        *order("c-1", "AAPL", "buy", "10", "100"),
        EVALUATION,
        ("paper_blocked", "", {"reason": "Stale or future market data blocks all orders"}),
        session_end(state="blocked", reason=None),
    ])
    return build_evidence(events_of(path), symbol="AAPL")


def test_rule_explanation_cites_only_real_events(evidence):
    answer = RuleExplainer().explain(evidence, question_for("symbol", symbol="AAPL"))
    explanation = answer["explanation"]
    assert answer["rejected"] == []
    assert answer["source"] == "rules"
    known = collect_refs(evidence)
    assert all(item["refs"] and set(item["refs"]) <= known for item in explanation["findings"])
    text = " ".join(item["statement"] for item in explanation["findings"])
    assert "risk manager blocked 1" in text and "max_open_positions_reached" in text
    assert "Stale or future market data" in text
    assert "price was -1.25% from its SMA, versus a buy trigger at -1.00%" in text
    assert explanation["hypotheses"] == []
    assert any("closed trade" in caveat for caveat in explanation["caveats"])


def test_invalid_comparison_is_explained_not_quoted(evidence):
    comparison = compare_session_to_experiment(evidence, experiment(dataset="sample_multisymbol_bars.csv"))
    answer = RuleExplainer().explain(evidence, question_for("comparison", experiment_id="exp-1"), comparison)
    assert "not valid" in answer["explanation"]["summary"]
    assert any("research_dataset" not in c and "synthetic" in c for c in answer["explanation"]["caveats"])


def test_validation_drops_invented_refs(evidence):
    real = sorted(collect_refs(evidence))[0]
    cleaned, rejected = validate_explanation({
        "summary": "s",
        "findings": [
            {"statement": "supported", "refs": [real, "E9999"]},
            {"statement": "made up", "refs": ["E9998"]},
        ],
        "hypotheses": [{"statement": "maybe", "refs": ["E9997"], "how_to_check": "look"}],
        "caveats": [], "next_steps": [],
    }, evidence)
    assert cleaned["findings"] == [{"statement": "supported", "refs": [real]}]
    assert cleaned["hypotheses"][0]["refs"] == []
    assert {item["statement"] for item in rejected} == {"supported", "made up"}


def test_questions_require_their_subject():
    with pytest.raises(ValueError):
        question_for("symbol")
    with pytest.raises(ValueError):
        question_for("comparison")
    with pytest.raises(ValueError):
        question_for("forecast")


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(response=None, error=None):
    messages = FakeMessages(response, error)
    return NS(beta=NS(messages=messages)), messages


def reply(payload, stop_reason="end_turn", model=DEFAULT_MODEL, iterations=None):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return NS(stop_reason=stop_reason, model=model, content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
              usage=NS(iterations=iterations or []))


def test_claude_request_shape_and_verified_answer(evidence):
    real = sorted(collect_refs(evidence))[0]
    client, messages = fake_client(reply({
        "summary": "AAPL bought once.",
        "findings": [{"statement": "One buy.", "refs": [real]}, {"statement": "Invented.", "refs": ["E9999"]}],
        "hypotheses": [{"statement": "Thresholds may be tight.", "refs": [real], "how_to_check": "Compare deviation_pct with entry_threshold_pct across sessions."}],
        "caveats": ["Small sample."], "next_steps": ["Collect more trades."],
    }))
    answer = ClaudeExplainer(client=client).explain(evidence, "Why did AAPL buy?")

    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "high"
    assert "temperature" not in call and "thinking" not in call
    content = call["messages"][0]["content"]
    assert content.startswith("Question: Why did AAPL buy?") and "<evidence>" in content
    assert "acct-secret-9" not in content
    assert answer["source"] == "claude" and answer["fallback_used"] is False
    assert [item["statement"] for item in answer["explanation"]["findings"]] == ["One buy."]
    assert answer["rejected"][0]["statement"] == "Invented."


def test_claude_fallback_is_reported(evidence):
    client, _ = fake_client(reply({"summary": "s", "findings": [], "hypotheses": [], "caveats": [], "next_steps": []},
                                  model="claude-opus-5", iterations=[NS(type="message"), NS(type="fallback_message")]))
    answer = ClaudeExplainer(client=client).explain(evidence, "q")
    assert answer["model"] == "claude-opus-5" and answer["fallback_used"] is True


@pytest.mark.parametrize("response, message", [
    (reply("{}", stop_reason="refusal"), "declined"),
    (reply("{", stop_reason="max_tokens"), "cut off"),
    (reply("not json"), "not valid JSON"),
    (NS(stop_reason="end_turn", model=DEFAULT_MODEL, content=[], usage=NS()), "no explanation"),
])
def test_claude_unusable_answers_raise(evidence, response, message):
    client, _ = fake_client(response)
    with pytest.raises(AnalystUnavailable, match=message):
        ClaudeExplainer(client=client).explain(evidence, "q")


def test_claude_errors_never_echo_details(evidence):
    client, _ = fake_client(error=TimeoutError("Bearer sk-ant-secret"))
    with pytest.raises(AnalystUnavailable) as caught:
        ClaudeExplainer(client=client).explain(evidence, "q")
    assert "sk-ant-secret" not in str(caught.value)
    assert "TimeoutError" in str(caught.value)


def test_claude_sdk_errors_map_to_guidance(evidence):
    anthropic = pytest.importorskip("anthropic")
    client, _ = fake_client(error=anthropic.AnthropicError("missing credentials"))
    with pytest.raises(AnalystUnavailable, match="ANTHROPIC_API_KEY"):
        ClaudeExplainer(client=client).explain(evidence, "q")
