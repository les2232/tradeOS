from tradeos.analyst.comparison import compare_session_to_experiment
from tradeos.analyst.evidence import build_evidence

from tests.unit.analyst.conftest import (
    EVALUATION, cycle, events_of, experiment, order, session_end, session_start, snapshot, write_log,
)


def session_evidence(tmp_path, snap=None):
    path = write_log(tmp_path / "e.sqlite3", [
        session_start(snap=snap),
        *cycle("AAPL", "buy", "mean_reversion_entry_threshold_crossed", order=True),
        *order("c-1", "AAPL", "buy", "10", "100"),
        EVALUATION,
        *cycle("AAPL", "sell", "mean_reversion_exit_threshold_crossed", order=True),
        *order("c-2", "AAPL", "sell", "10", "101"),
        EVALUATION,
        session_end(),
    ])
    return build_evidence(events_of(path))


def failed(result):
    return {item["check"] for item in result["checks"] if not item["ok"]}


def test_matching_session_is_comparable_but_not_conclusive(tmp_path):
    result = compare_session_to_experiment(session_evidence(tmp_path), experiment())
    assert result["valid"] is True
    assert failed(result) == set()
    assert result["conclusive"] is False
    assert result["metrics"]["closed_trades"] == {"session": 1, "research": 40}
    assert result["metrics"]["net_pnl"]["session"] == "10.00"
    assert any("cost" in note for note in result["notes"])


def test_equal_values_in_different_spellings_still_match(tmp_path):
    params = {"entry_threshold_pct": "0.0100", "trend_filter_enabled": "true", "sma_bars": "20"}
    assert compare_session_to_experiment(session_evidence(tmp_path), experiment(parameters=params))["valid"] is True


def test_parameter_mismatch_withholds_all_metrics(tmp_path):
    result = compare_session_to_experiment(session_evidence(tmp_path), experiment(parameters={"entry_threshold_pct": "0.005"}))
    assert result["valid"] is False
    assert failed(result) == {"parameters"}
    assert result["metrics"] is None
    assert "withheld" in result["suppressed_reason"]


def test_synthetic_or_small_research_is_not_a_baseline(tmp_path):
    evidence = session_evidence(tmp_path)
    assert failed(compare_session_to_experiment(evidence, experiment(dataset="sample_multisymbol_bars.csv"))) == {"research_dataset"}
    assert failed(compare_session_to_experiment(evidence, experiment(trades=12))) == {"research_sample"}


def test_scope_and_universe_mismatches(tmp_path):
    evidence = session_evidence(tmp_path, snap=snapshot(watchlist=["AAPL"], timeframe="5Min"))
    assert failed(compare_session_to_experiment(evidence, experiment(mode="momentum_breakout"))) == {"strategy_mode", "symbols", "timeframe"}
    unsessioned = build_evidence(events_of(write_log(tmp_path / "u.sqlite3", cycle("AAPL", "hold", "mean_reversion_no_signal"))))
    result = compare_session_to_experiment(unsessioned, experiment())
    assert "session_configuration" in failed(result)
    assert result["metrics"] is None
