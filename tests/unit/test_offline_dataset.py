import csv
import importlib
from datetime import UTC

import pytest

from tradeos.data.historical_dataset import load_bars_from_csv, summarize_bars
from tradeos.main import main, _market_data_adapter
from scripts.generate_sample_dataset import write_sample_dataset


def write_rows(tmp_path, rows):
    path = tmp_path / "bars.csv"
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(valid_row()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def valid_row(**changes):
    return dict(symbol="AAPL", timestamp="2026-04-20T09:30:00-04:00",
                open="100", high="102", low="99", close="101", volume="0",
                timeframe="15Min", data_quality="ok") | changes


@pytest.mark.parametrize("changes", [
    {"close": "NaN"}, {"volume": "Infinity"}, {"open": "0"},
    {"volume": "-1"}, {"high": "98"}, {"low": "103"},
    {"timestamp": "2026-04-20T09:30:00"}, {"symbol": " "},
    {"timeframe": ""}, {"close": "abc"}, {"data_quality": "unknown"},
])
def test_rejects_bad_rows_with_location(tmp_path, changes):
    path = write_rows(tmp_path, [valid_row(**changes)])
    with pytest.raises(ValueError, match="row 2"):
        load_bars_from_csv(path)


def test_rejects_duplicate_instants_with_different_offsets(tmp_path):
    path = write_rows(tmp_path, [valid_row(), valid_row(timestamp="2026-04-20T13:30:00Z")])
    with pytest.raises(ValueError, match="row 3: duplicate"):
        load_bars_from_csv(path)


def test_sorts_and_summarizes_multisymbol_bars(tmp_path):
    path = write_rows(tmp_path, [valid_row(timestamp="2026-04-20T14:00:00Z"),
                                 valid_row(symbol="MSFT"), valid_row()])
    bars = load_bars_from_csv(path)
    assert [bar.symbol for bar in bars] == ["AAPL", "MSFT", "AAPL"]
    assert all(bar.timestamp.tzinfo == UTC for bar in bars)
    summary = summarize_bars(bars)
    assert summary.total_rows == 3
    assert summary.symbols == ["AAPL", "MSFT"]
    assert summary.timeframe_counts == {"15Min": 3}
    assert summary.start_timestamp < summary.end_timestamp


def test_empty_and_missing_columns(tmp_path):
    with pytest.raises(ValueError, match="no bars"):
        load_bars_from_csv(write_rows(tmp_path, []))
    path = tmp_path / "missing.csv"
    path.write_text("symbol\nAAPL\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        load_bars_from_csv(path)
    assert summarize_bars([]).start_timestamp is None


def test_offline_cli_never_loads_online_adapter(tmp_path, monkeypatch, capsys):
    def forbidden(**kwargs):
        pytest.fail("Offline command requested online market data")
    monkeypatch.setattr("tradeos.main._market_data_adapter", forbidden)
    path = tmp_path / "synthetic.csv"
    write_sample_dataset(path, bars_per_symbol=30)
    assert main(["--config", "config/paper.yaml", "--dry-run-once"], env={}) == 0
    assert main(["--config", "config/paper.yaml", "--backtest-csv", str(path)], env={}) == 0
    assert "total bars: 90" in capsys.readouterr().out


def test_missing_online_adapter_has_actionable_error(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name=name)
    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(RuntimeError, match="use --dry-run-once or --backtest-csv"):
        _market_data_adapter()
