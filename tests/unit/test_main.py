import csv
from pathlib import Path
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from tradeos.domain.enums import StrategyMode
from tradeos.main import main
from tradeos.research.experiment import ExperimentResult, ExperimentSpec
from tradeos.research.memory import ResearchMemory


def write_dataset_csv(content: str) -> Path:
    path = Path("tests/fixtures") / f"cli_dataset_{uuid4().hex}.csv"
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def research_memory_path() -> Path:
    return Path("tests/fixtures") / f"experiments_{uuid4().hex}.jsonl"


def research_report_path() -> Path:
    return Path("tests/fixtures") / f"research_report_{uuid4().hex}.md"


def sweep_report_path() -> Path:
    return Path("tests/fixtures") / f"sweep_report_{uuid4().hex}.md"


def multi_month_report_path() -> Path:
    return Path("tests/fixtures") / f"multi_month_{uuid4().hex}.md"


def backtest_export_path() -> Path:
    return Path("tests/fixtures") / f"backtest_results_{uuid4().hex}.csv"


def experiment_result(experiment_id: str, score: Decimal) -> ExperimentResult:
    spec = ExperimentSpec(
        strategy_mode=StrategyMode.MEAN_REVERSION,
        symbols=["AAPL"],
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
        timeframe="15Min",
        parameters={"entry_threshold_pct": "1.0"},
        dataset_id=f"{experiment_id}.csv",
    )
    return ExperimentResult(
        experiment_id=experiment_id,
        spec=spec,
        profit_factor=Decimal("1.5"),
        max_drawdown=Decimal("0"),
        win_rate=Decimal("0.5"),
        num_trades=10,
        expectancy=Decimal("1"),
        net_pnl=Decimal("100"),
        score=score,
        warnings=["low_trade_count"],
    )


DATASET_CSV = """
symbol,timestamp,open,high,low,close,volume,timeframe
AAPL,2026-04-25T14:30:00+00:00,100,101,99,100,1000,15Min
TSLA,2026-04-25T14:30:00+00:00,200,201,199,200,1000,15Min
AAPL,2026-04-25T14:45:00+00:00,101,102,100,101,1000,15Min
"""

MULTI_SYMBOL_DATASET_CSV = """
symbol,timestamp,open,high,low,close,volume,timeframe
AAPL,2026-04-25T14:30:00+00:00,100,101,99,100,1000,15Min
MSFT,2026-04-25T14:45:00+00:00,200,201,199,200,1000,15Min
TSLA,2026-04-25T15:00:00+00:00,300,301,299,300,1000,15Min
AAPL,2026-04-25T15:15:00+00:00,101,102,100,101,1000,15Min
MSFT,2026-04-25T15:30:00+00:00,201,202,200,201,1000,15Min
"""

SWEEP_DATASET_CSV = """
symbol,timestamp,open,high,low,close,volume,timeframe
AAPL,2026-04-25T14:30:00+00:00,100,101,99,99.5,1000,15Min
AAPL,2026-04-25T14:45:00+00:00,100,103,99,101.5,1000,15Min
AAPL,2026-04-25T15:00:00+00:00,100,101,99,99.75,1000,15Min
AAPL,2026-04-25T15:15:00+00:00,100,102,99,101.25,1000,15Min
"""

WALK_FORWARD_DATASET_CSV = """
symbol,timestamp,open,high,low,close,volume,timeframe
AAPL,2026-04-25T14:30:00+00:00,100,101,99,100,1000,15Min
AAPL,2026-04-25T14:45:00+00:00,100,101,99,100,1000,15Min
AAPL,2026-04-25T15:00:00+00:00,100,101,99,99,1000,15Min
AAPL,2026-04-25T15:15:00+00:00,100,102,99,101,1000,15Min
AAPL,2026-04-25T15:30:00+00:00,100,101,99,99,1000,15Min
AAPL,2026-04-25T15:45:00+00:00,100,102,99,101,1000,15Min
AAPL,2026-04-25T16:00:00+00:00,100,101,99,99,1000,15Min
AAPL,2026-04-25T16:15:00+00:00,100,102,99,101,1000,15Min
AAPL,2026-04-25T16:30:00+00:00,100,101,99,99,1000,15Min
AAPL,2026-04-25T16:45:00+00:00,100,102,99,101,1000,15Min
"""


def test_cli_dry_run_exits_successfully(capsys) -> None:
    exit_code = main(["--config", "config/paper.yaml", "--dry-run-once"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "symbol: AAPL" in output
    assert "signal action: hold" in output
    assert "reconciliation status: clean" in output
    assert "execution allowed: false" in output


def test_cli_output_contains_hold(capsys) -> None:
    main(["--config", "config/paper.yaml", "--dry-run-once"])

    output = capsys.readouterr().out
    assert "hold" in output
    assert "hold_signal_no_order" in output


def test_download_history_rejects_incomplete_args() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--download-history", "--symbols", "AAPL"])

    assert exc.value.code == 2


def test_download_history_command_does_not_require_config(monkeypatch, capsys) -> None:
    calls = {}

    def fake_download_historical_bars_to_csv(**kwargs):
        calls.update(kwargs)
        return 3

    monkeypatch.setattr(
        "tradeos.main.download_historical_bars_to_csv",
        fake_download_historical_bars_to_csv,
    )

    exit_code = main(
        [
            "--download-history",
            "--symbols",
            "AAPL,MSFT,NVDA",
            "--start",
            "2026-01-01",
            "--end",
            "2026-02-01",
            "--timeframe",
            "15Min",
            "--feed",
            "iex",
            "--output",
            "data/real_bars.csv",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert calls["symbols"] == ["AAPL", "MSFT", "NVDA"]
    assert calls["output"] == "data/real_bars.csv"
    assert "historical data download complete" in output
    assert "rows: 3" in output


def test_preview_mode_exits_successfully(capsys) -> None:
    exit_code = main(["--config", "config/paper.yaml", "--preview-cycles", "5"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "latest cycle summary:" in output


def test_preview_mode_prints_total_cycles(capsys) -> None:
    main(["--config", "config/paper.yaml", "--preview-cycles", "5"])

    output = capsys.readouterr().out
    assert "total cycles: 5" in output


def test_invalid_preview_cycle_count_is_rejected() -> None:
    try:
        main(["--config", "config/paper.yaml", "--preview-cycles", "0"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected invalid preview cycle count to exit")


def test_preview_event_counts_include_cycle_completed(capsys) -> None:
    main(["--config", "config/paper.yaml", "--preview-cycles", "3"])

    output = capsys.readouterr().out
    assert "cycle_completed: 3" in output


def test_still_supports_dry_run_once(capsys) -> None:
    exit_code = main(["--config", "config/paper.yaml", "--dry-run-once"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "signal action: hold" in output


def test_preview_with_dataset_csv_exits_successfully(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        exit_code = main(
            [
                "--config",
                "config/paper.yaml",
                "--preview-cycles",
                "5",
                "--dataset-csv",
                str(path),
            ]
        )
    finally:
        path.unlink(missing_ok=True)

    assert exit_code == 0
    assert "latest cycle summary:" in capsys.readouterr().out


def test_preview_with_dataset_outputs_actual_total_cycles(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--preview-cycles",
                "5",
                "--dataset-csv",
                str(path),
            ]
        )
    finally:
        path.unlink(missing_ok=True)

    assert "total cycles: 2" in capsys.readouterr().out


def test_preview_with_dataset_filters_to_first_configured_symbol(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--preview-cycles",
                "5",
                "--dataset-csv",
                str(path),
            ]
        )
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "cycle_completed: 2" in output
    assert "symbol: AAPL" in output


def test_rejects_dataset_csv_without_preview_mode() -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        with pytest.raises(SystemExit) as exc:
            main(["--config", "config/paper.yaml", "--dry-run-once", "--dataset-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert exc.value.code == 2


def test_rejects_missing_dataset_file_clearly() -> None:
    with pytest.raises(ValueError, match="dataset CSV does not exist"):
        main(
            [
                "--config",
                "config/paper.yaml",
                "--preview-cycles",
                "5",
                "--dataset-csv",
                "tests/fixtures/missing_dataset.csv",
            ]
        )


def test_existing_fake_preview_still_works(capsys) -> None:
    main(["--config", "config/paper.yaml", "--preview-cycles", "2"])

    output = capsys.readouterr().out
    assert "total cycles: 2" in output
    assert "cycle_completed: 2" in output


def test_backtest_csv_exits_successfully(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        exit_code = main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert exit_code == 0
    assert "latest cycle summary:" in capsys.readouterr().out


def test_backtest_output_includes_total_bars_and_cycles(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "total bars: 2" in output
    assert "total cycles: 2" in output


def test_cli_can_run_backtest_with_momentum_breakout_strategy(capsys) -> None:
    path = write_dataset_csv(MULTI_SYMBOL_DATASET_CSV)
    try:
        exit_code = main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(path),
                "--strategy-mode",
                "momentum_breakout",
                "--breakout-threshold-pct",
                "0.002",
                "--momentum-exit-threshold-pct",
                "0.001",
                "--momentum-stop-loss-pct",
                "0.01",
            ]
        )
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "strategy mode: momentum_breakout" in output
    assert "breakout threshold pct: 0.002" in output
    assert "momentum exit threshold pct: 0.001" in output
    assert "momentum stop loss pct: 0.01" in output


def test_backtest_output_includes_event_counts(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "event counts:" in output
    assert "cycle_completed: 2" in output


def test_backtest_output_includes_signal_reason_counts(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "signal reason counts:" in output
    assert "insufficient_history_for_sma:" in output


def test_backtest_output_includes_risk_reason_counts(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "risk reason counts:" in output
    assert "hold_signal_no_order:" in output


def test_backtest_output_includes_trade_diagnostics(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "trade diagnostics:" in output
    assert "worst trade PnL:" in output
    assert "best trade PnL:" in output
    assert "average bars held:" in output
    assert "worst max adverse excursion:" in output


def test_backtest_output_includes_forced_flatten_trades(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "forced flatten trades:" in output


def test_backtest_output_includes_edge_attribution(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "closed-trade edge attribution:" in output
    assert "Closed trade PnL total:" in output
    assert "Attribution PnL total:" in output
    assert "Unrealized PnL:" in output
    assert "PnL by symbol:" in output
    assert "Closed trades by symbol:" in output
    assert "PnL by entry hour UTC:" in output
    assert "Closed trades by entry hour UTC:" in output
    assert "PnL by exit reason:" in output
    assert "Closed trades by exit reason:" in output


def test_backtest_cli_prints_dataset_summary(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "dataset rows: 2" in output
    assert "dataset symbols: AAPL" in output
    assert "symbol count: 1" in output
    assert "tested symbols: AAPL" in output
    assert "dataset start: 2026-04-25T14:30:00+00:00" in output
    assert "dataset end: 2026-04-25T14:45:00+00:00" in output
    assert "timeframe counts:" in output
    assert "15Min: 2" in output


def test_multi_symbol_csv_includes_configured_symbols(capsys) -> None:
    path = write_dataset_csv(MULTI_SYMBOL_DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "dataset rows: 4" in output
    assert "symbol count: 2" in output
    assert "tested symbols: AAPL, MSFT" in output
    assert "total bars: 4" in output
    assert "total cycles: 4" in output


def test_research_memory_save_includes_all_tested_symbols(capsys) -> None:
    dataset = write_dataset_csv(MULTI_SYMBOL_DATASET_CSV)
    memory = research_memory_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--save-research-result",
                str(memory),
            ]
        )

        result = ResearchMemory(memory).load_results()[0]
        assert result.spec.symbols == ["AAPL", "MSFT"]
    finally:
        dataset.unlink(missing_ok=True)
        memory.unlink(missing_ok=True)


def test_export_backtest_csv_creates_file(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    export = backtest_export_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--export-backtest-csv",
                str(export),
            ]
        )

        assert export.exists()
        assert "backtest CSV export:" in capsys.readouterr().out
    finally:
        dataset.unlink(missing_ok=True)
        export.unlink(missing_ok=True)


def test_export_backtest_csv_contains_expected_headers(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    export = backtest_export_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--export-backtest-csv",
                str(export),
            ]
        )

        with export.open(newline="", encoding="utf-8") as file:
            headers = next(csv.reader(file))
        assert headers == [
            "dataset rows",
            "dataset symbols",
            "dataset start",
            "dataset end",
            "sma bars",
            "entry threshold pct",
            "exit threshold pct",
            "max atr percentile",
            "total cycles",
            "buy signals",
            "sell signals",
            "hold signals",
            "approved risk decisions",
            "blocked risk decisions",
            "order intents created",
            "realized PnL",
            "unrealized PnL",
            "total PnL",
            "total costs",
            "max drawdown",
            "closed trades",
            "win rate",
            "profit factor",
            "expectancy",
        ]
    finally:
        dataset.unlink(missing_ok=True)
        export.unlink(missing_ok=True)


def test_export_backtest_csv_contains_expected_metrics(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    export = backtest_export_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--export-backtest-csv",
                str(export),
            ]
        )

        with export.open(newline="", encoding="utf-8") as file:
            row = next(csv.DictReader(file))
        assert row["dataset rows"] == "2"
        assert row["dataset symbols"] == "AAPL"
        assert row["dataset start"] == "2026-04-25T14:30:00+00:00"
        assert row["dataset end"] == "2026-04-25T14:45:00+00:00"
        assert row["sma bars"]
        assert row["entry threshold pct"]
        assert row["exit threshold pct"]
        assert row["max atr percentile"]
        assert row["total cycles"] == "2"
        assert row["buy signals"] == "0"
        assert row["sell signals"] == "0"
        assert row["hold signals"] == "2"
        assert row["total costs"] == "0"
        assert row["closed trades"] == "0"
    finally:
        dataset.unlink(missing_ok=True)
        export.unlink(missing_ok=True)


def test_export_backtest_csv_without_backtest_csv_is_rejected() -> None:
    export = backtest_export_path()
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--dry-run-once",
                "--export-backtest-csv",
                str(export),
            ]
        )

    assert exc.value.code == 2
    export.unlink(missing_ok=True)


def test_override_entry_threshold_affects_output_and_spec(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    memory = research_memory_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--entry-threshold-pct",
                "0.025",
                "--save-research-result",
                str(memory),
            ]
        )

        output = capsys.readouterr().out
        result = ResearchMemory(memory).load_results()[0]
        assert "entry threshold pct: 0.025" in output
        assert result.spec.parameters["entry_threshold_pct"] == "0.025"
    finally:
        dataset.unlink(missing_ok=True)
        memory.unlink(missing_ok=True)


def test_override_exit_threshold_affects_output_and_spec(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    memory = research_memory_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--exit-threshold-pct",
                "0.03",
                "--save-research-result",
                str(memory),
            ]
        )

        output = capsys.readouterr().out
        result = ResearchMemory(memory).load_results()[0]
        assert "exit threshold pct: 0.03" in output
        assert result.spec.parameters["exit_threshold_pct"] == "0.03"
    finally:
        dataset.unlink(missing_ok=True)
        memory.unlink(missing_ok=True)


def test_override_sma_bars_affects_output_and_spec(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    memory = research_memory_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sma-bars",
                "42",
                "--save-research-result",
                str(memory),
            ]
        )

        output = capsys.readouterr().out
        result = ResearchMemory(memory).load_results()[0]
        assert "sma bars: 42" in output
        assert result.spec.parameters["sma_bars"] == 42
    finally:
        dataset.unlink(missing_ok=True)
        memory.unlink(missing_ok=True)


def test_invalid_strategy_override_is_rejected_clearly() -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    try:
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--config",
                    "config/paper.yaml",
                    "--backtest-csv",
                    str(dataset),
                    "--entry-threshold-pct",
                    "-0.01",
                ]
            )
    finally:
        dataset.unlink(missing_ok=True)

    assert exc.value.code == 2


def test_export_csv_includes_overridden_parameter_columns(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    export = backtest_export_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--entry-threshold-pct",
                "0.025",
                "--exit-threshold-pct",
                "0.035",
                "--sma-bars",
                "42",
                "--max-atr-percentile",
                "88",
                "--export-backtest-csv",
                str(export),
            ]
        )

        with export.open(newline="", encoding="utf-8") as file:
            row = next(csv.DictReader(file))
        assert row["entry threshold pct"] == "0.025"
        assert row["exit threshold pct"] == "0.035"
        assert row["sma bars"] == "42"
        assert row["max atr percentile"] == "88"
    finally:
        dataset.unlink(missing_ok=True)
        export.unlink(missing_ok=True)


def test_sweep_runs_all_parameter_combinations(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001,0.002",
                "--sweep-exit-thresholds",
                "0.003,0.004",
            ]
        )

        output = capsys.readouterr().out
        result_lines = [
            line for line in output.splitlines() if line.startswith(("0.001 |", "0.002 |"))
        ]
        assert len(result_lines) == 4
        assert "parameter sweep results:" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_sweep_output_includes_each_entry_exit_pair(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001,0.002",
                "--sweep-exit-thresholds",
                "0.003,0.004",
            ]
        )

        output = capsys.readouterr().out
        assert "0.001 | 0.003 |" in output
        assert "0.001 | 0.004 |" in output
        assert "0.002 | 0.003 |" in output
        assert "0.002 | 0.004 |" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_sweep_append_csv_writes_one_row_per_combo(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    export = backtest_export_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001,0.002",
                "--sweep-exit-thresholds",
                "0.003,0.004",
                "--append-backtest-csv",
                str(export),
            ]
        )

        with export.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        assert len(rows) == 4
        assert {row["entry threshold pct"] for row in rows} == {"0.001", "0.002"}
        assert {row["exit threshold pct"] for row in rows} == {"0.003", "0.004"}
    finally:
        dataset.unlink(missing_ok=True)
        export.unlink(missing_ok=True)


def test_sweep_save_research_result_writes_one_result_per_combo(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    memory = research_memory_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001,0.002",
                "--sweep-exit-thresholds",
                "0.003,0.004",
                "--save-research-result",
                str(memory),
            ]
        )

        results = ResearchMemory(memory).load_results()
        assert len(results) == 4
        assert {
            result.spec.parameters["entry_threshold_pct"] for result in results
        } == {"0.001", "0.002"}
        assert {
            result.spec.parameters["exit_threshold_pct"] for result in results
        } == {"0.003", "0.004"}
    finally:
        dataset.unlink(missing_ok=True)
        memory.unlink(missing_ok=True)


def test_invalid_sweep_threshold_list_is_rejected_clearly() -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    try:
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--config",
                    "config/paper.yaml",
                    "--backtest-csv",
                    str(dataset),
                    "--sweep-entry-thresholds",
                    "0.001,bad",
                    "--sweep-exit-thresholds",
                    "0.003",
                ]
            )
    finally:
        dataset.unlink(missing_ok=True)

    assert exc.value.code == 2


def test_sweep_summary_prints_best_result(capsys) -> None:
    dataset = write_dataset_csv(SWEEP_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.004,0.02",
                "--sweep-exit-thresholds",
                "0.01,0.02",
            ]
        )

        output = capsys.readouterr().out
        assert "sweep summary:" in output
        assert "best score:" in output
        assert "best entry threshold:" in output
        assert "best exit threshold:" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_sweep_top_3_results_are_ranked_by_score(capsys) -> None:
    dataset = write_dataset_csv(SWEEP_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.004,0.02",
                "--sweep-exit-thresholds",
                "0.01,0.02",
            ]
        )

        output = capsys.readouterr().out
        ranked_lines = [
            line.strip()
            for line in output.splitlines()
            if line.strip().startswith(("1. score:", "2. score:", "3. score:"))
        ]
        scores = [Decimal(line.split("|")[0].split("score: ")[1]) for line in ranked_lines]
        assert len(scores) == 3
        assert scores == sorted(scores, reverse=True)
    finally:
        dataset.unlink(missing_ok=True)


def test_sweep_summary_includes_thresholds_and_key_metrics(capsys) -> None:
    dataset = write_dataset_csv(SWEEP_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.004,0.02",
                "--sweep-exit-thresholds",
                "0.01,0.02",
            ]
        )

        output = capsys.readouterr().out
        assert "best total PnL:" in output
        assert "best profit factor:" in output
        assert "best max drawdown:" in output
        assert "best closed trades:" in output
        assert "entry threshold:" in output
        assert "exit threshold:" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_sweep_report_file_is_created(capsys) -> None:
    dataset = write_dataset_csv(SWEEP_DATASET_CSV)
    report = sweep_report_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.004,0.02",
                "--sweep-exit-thresholds",
                "0.01,0.02",
                "--write-sweep-report",
                str(report),
            ]
        )

        assert report.exists()
        assert f"sweep report path: {report}" in capsys.readouterr().out
    finally:
        dataset.unlink(missing_ok=True)
        report.unlink(missing_ok=True)


def test_sweep_report_contains_best_result(capsys) -> None:
    dataset = write_dataset_csv(SWEEP_DATASET_CSV)
    report = sweep_report_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.004,0.02",
                "--sweep-exit-thresholds",
                "0.01,0.02",
                "--write-sweep-report",
                str(report),
            ]
        )

        content = report.read_text(encoding="utf-8")
        assert "## Best Result" in content
        assert "Score:" in content
        assert "Total PnL:" in content
    finally:
        dataset.unlink(missing_ok=True)
        report.unlink(missing_ok=True)


def test_sweep_report_contains_top_3_section(capsys) -> None:
    dataset = write_dataset_csv(SWEEP_DATASET_CSV)
    report = sweep_report_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.004,0.02",
                "--sweep-exit-thresholds",
                "0.01,0.02",
                "--write-sweep-report",
                str(report),
            ]
        )

        content = report.read_text(encoding="utf-8")
        assert "## Top 3 Results" in content
        assert "| Rank | Entry Threshold | Exit Threshold | Score |" in content
    finally:
        dataset.unlink(missing_ok=True)
        report.unlink(missing_ok=True)


def test_sweep_report_contains_tested_parameter_combinations(capsys) -> None:
    dataset = write_dataset_csv(SWEEP_DATASET_CSV)
    report = sweep_report_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.004,0.02",
                "--sweep-exit-thresholds",
                "0.01,0.02",
                "--write-sweep-report",
                str(report),
            ]
        )

        content = report.read_text(encoding="utf-8")
        assert "## Parameter Combinations Tested" in content
        assert "| 0.004 | 0.01 |" in content
        assert "| 0.02 | 0.02 |" in content
        assert "## Research Warnings" in content
    finally:
        dataset.unlink(missing_ok=True)
        report.unlink(missing_ok=True)


def test_write_sweep_report_without_sweep_is_rejected() -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    report = sweep_report_path()
    try:
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--config",
                    "config/paper.yaml",
                    "--backtest-csv",
                    str(dataset),
                    "--write-sweep-report",
                    str(report),
                ]
            )
    finally:
        dataset.unlink(missing_ok=True)
        report.unlink(missing_ok=True)

    assert exc.value.code == 2


def test_walk_forward_splits_dataset_into_selection_test_windows(capsys) -> None:
    dataset = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001,0.002",
                "--sweep-exit-thresholds",
                "0.001,0.002",
                "--walk-forward",
            ]
        )

        output = capsys.readouterr().out
        assert "selection bars: 6" in output
        assert "test bars: 4" in output
        assert "test warmup bars: 6" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_walk_forward_runs_test_window_with_selected_params(capsys) -> None:
    dataset = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001,0.002",
                "--sweep-exit-thresholds",
                "0.001,0.002",
                "--walk-forward",
            ]
        )

        output = capsys.readouterr().out
        assert "best selection entry threshold:" in output
        assert "best selection exit threshold:" in output
        assert "selection score:" in output
        assert "selection total PnL:" in output
        assert "selection closed trades:" in output
        assert "test total PnL:" in output
        assert "test profit factor:" in output
        assert "test max drawdown:" in output
        assert "test closed trades:" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_walk_forward_prints_pass_fail_verdict(capsys) -> None:
    dataset = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001",
                "--sweep-exit-thresholds",
                "0.001",
                "--walk-forward",
            ]
        )

        output = capsys.readouterr().out
        assert "verdict:" in output
        assert ("verdict: PASS" in output) or ("verdict: FAIL" in output)
    finally:
        dataset.unlink(missing_ok=True)


def test_walk_forward_without_sweep_options_is_rejected() -> None:
    dataset = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    try:
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--config",
                    "config/paper.yaml",
                    "--backtest-csv",
                    str(dataset),
                    "--walk-forward",
                ]
            )
    finally:
        dataset.unlink(missing_ok=True)

    assert exc.value.code == 2


def test_walk_forward_prints_selection_test_reason_counts(capsys) -> None:
    dataset = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001",
                "--sweep-exit-thresholds",
                "0.001",
                "--walk-forward",
            ]
        )

        output = capsys.readouterr().out
        assert "selection signal reason counts:" in output
        assert "selection risk reason counts:" in output
        assert "test signal reason counts:" in output
        assert "test risk reason counts:" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_walk_forward_prints_trade_diagnostics(capsys) -> None:
    dataset = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001",
                "--sweep-exit-thresholds",
                "0.001",
                "--walk-forward",
            ]
        )

        output = capsys.readouterr().out
        assert "selection trade diagnostics:" in output
        assert "test trade diagnostics:" in output
        assert "worst trade PnL:" in output
        assert "average bars held:" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_walk_forward_prints_edge_attribution(capsys) -> None:
    dataset = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001",
                "--sweep-exit-thresholds",
                "0.001",
                "--walk-forward",
            ]
        )

        output = capsys.readouterr().out
        assert "selection realized PnL:" in output
        assert "selection unrealized PnL:" in output
        assert "test realized PnL:" in output
        assert "test unrealized PnL:" in output
        assert "selection closed-trade edge attribution:" in output
        assert "test closed-trade edge attribution:" in output
        assert "PnL by exit reason:" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_multi_month_report_runs_across_multiple_csv_paths(capsys) -> None:
    first = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    second = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    report = multi_month_report_path()
    try:
        exit_code = main(
            [
                "--config",
                "config/paper.yaml",
                "--strategy-mode",
                "momentum_breakout",
                "--multi-month-report",
                str(report),
                "--backtest-csv",
                f"{first},{second}",
                "--sweep-entry-thresholds",
                "0.001,0.002",
                "--sweep-exit-thresholds",
                "0.001,0.002",
                "--walk-forward",
            ]
        )

        output = capsys.readouterr().out
        content = report.read_text(encoding="utf-8")
        assert exit_code == 0
        assert "multi-month report path:" in output
        assert str(first) in content
        assert str(second) in content
        assert "Number of datasets: 2" in content
        assert "PASS count:" in content
        assert "FAIL count:" in content
        assert "| Dataset | Test Total PnL |" in content
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)
        report.unlink(missing_ok=True)


def test_walk_forward_uses_selection_bars_as_test_warmup(capsys) -> None:
    dataset = write_dataset_csv(WALK_FORWARD_DATASET_CSV)
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--sweep-entry-thresholds",
                "0.001",
                "--sweep-exit-thresholds",
                "0.001",
                "--walk-forward",
            ]
        )

        output = capsys.readouterr().out
        assert "test warmup bars: 6" in output
        assert "test signal reason counts:" in output
    finally:
        dataset.unlink(missing_ok=True)


def test_append_backtest_csv_creates_file_with_headers(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    export = backtest_export_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--append-backtest-csv",
                str(export),
            ]
        )

        with export.open(newline="", encoding="utf-8") as file:
            rows = list(csv.reader(file))
        assert export.exists()
        assert rows[0][0:4] == [
            "dataset rows",
            "dataset symbols",
            "dataset start",
            "dataset end",
        ]
        assert len(rows) == 2
        assert "backtest CSV append:" in capsys.readouterr().out
    finally:
        dataset.unlink(missing_ok=True)
        export.unlink(missing_ok=True)


def test_second_append_adds_second_row(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    export = backtest_export_path()
    args = [
        "--config",
        "config/paper.yaml",
        "--backtest-csv",
        str(dataset),
        "--append-backtest-csv",
        str(export),
    ]
    try:
        main(args)
        main(args)

        with export.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        assert len(rows) == 2
        assert rows[0]["total cycles"] == "2"
        assert rows[1]["total cycles"] == "2"
    finally:
        dataset.unlink(missing_ok=True)
        export.unlink(missing_ok=True)


def test_append_backtest_csv_does_not_duplicate_headers(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    export = backtest_export_path()
    args = [
        "--config",
        "config/paper.yaml",
        "--backtest-csv",
        str(dataset),
        "--append-backtest-csv",
        str(export),
    ]
    try:
        main(args)
        main(args)

        lines = export.read_text(encoding="utf-8").splitlines()
        header_count = sum(1 for line in lines if line.startswith("dataset rows,"))
        assert header_count == 1
        assert len(lines) == 3
    finally:
        dataset.unlink(missing_ok=True)
        export.unlink(missing_ok=True)


def test_append_backtest_csv_without_backtest_csv_is_rejected() -> None:
    export = backtest_export_path()
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--dry-run-once",
                "--append-backtest-csv",
                str(export),
            ]
        )

    assert exc.value.code == 2
    export.unlink(missing_ok=True)


def test_export_backtest_csv_still_works_after_append_addition(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    export = backtest_export_path()
    try:
        exit_code = main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--export-backtest-csv",
                str(export),
            ]
        )

        with export.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        assert exit_code == 0
        assert len(rows) == 1
        assert rows[0]["dataset rows"] == "2"
    finally:
        dataset.unlink(missing_ok=True)
        export.unlink(missing_ok=True)


def test_missing_backtest_csv_rejected_clearly() -> None:
    with pytest.raises(ValueError, match="backtest CSV does not exist"):
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                "tests/fixtures/missing_backtest.csv",
            ]
        )


def test_existing_preview_commands_still_pass(capsys) -> None:
    exit_code = main(["--config", "config/paper.yaml", "--preview-cycles", "1"])

    assert exit_code == 0
    assert "total cycles: 1" in capsys.readouterr().out


def test_backtest_output_includes_buy_signals(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert "buy signals:" in capsys.readouterr().out


def test_backtest_output_includes_sell_signals(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert "sell signals:" in capsys.readouterr().out


def test_backtest_output_includes_hold_signals(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert "hold signals:" in capsys.readouterr().out


def test_backtest_output_includes_risk_approved_blocked_counts(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "approved risk decisions:" in output
    assert "blocked risk decisions:" in output
    assert "dirty reconciliations:" in output
    assert "order intents created:" in output


def test_backtest_output_includes_realized_pnl(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert "realized PnL:" in capsys.readouterr().out


def test_backtest_output_includes_unrealized_pnl(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert "unrealized PnL:" in capsys.readouterr().out


def test_backtest_output_includes_total_pnl(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert "total PnL:" in capsys.readouterr().out


def test_backtest_output_includes_ending_positions_count(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert "ending positions count:" in capsys.readouterr().out


def test_backtest_output_includes_total_costs(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert "total costs:" in capsys.readouterr().out


def test_backtest_output_includes_max_drawdown(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    assert "max drawdown:" in capsys.readouterr().out


def test_cli_prints_trade_level_metrics(capsys) -> None:
    path = write_dataset_csv(DATASET_CSV)
    try:
        main(["--config", "config/paper.yaml", "--backtest-csv", str(path)])
    finally:
        path.unlink(missing_ok=True)

    output = capsys.readouterr().out
    assert "closed trades:" in output
    assert "win rate:" in output
    assert "profit factor:" in output
    assert "expectancy:" in output


def test_saving_research_result_creates_jsonl_file(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    memory = research_memory_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--save-research-result",
                str(memory),
            ]
        )
        assert memory.exists()
        assert memory.read_text(encoding="utf-8").strip()
    finally:
        dataset.unlink(missing_ok=True)
        memory.unlink(missing_ok=True)


def test_saved_result_includes_experiment_id_and_score(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    memory = research_memory_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--save-research-result",
                str(memory),
            ]
        )
        content = memory.read_text(encoding="utf-8")
        assert '"experiment_id"' in content
        assert '"score"' in content
    finally:
        dataset.unlink(missing_ok=True)
        memory.unlink(missing_ok=True)


def test_duplicate_save_does_not_duplicate_jsonl_lines(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    memory = research_memory_path()
    args = [
        "--config",
        "config/paper.yaml",
        "--backtest-csv",
        str(dataset),
        "--save-research-result",
        str(memory),
    ]
    try:
        main(args)
        main(args)
        lines = [line for line in memory.read_text(encoding="utf-8").splitlines() if line]
        assert len(lines) == 1
        assert "duplicate ignored" in capsys.readouterr().out
    finally:
        dataset.unlink(missing_ok=True)
        memory.unlink(missing_ok=True)


def test_save_research_result_without_backtest_csv_is_rejected() -> None:
    memory = research_memory_path()
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--dry-run-once",
                "--save-research-result",
                str(memory),
            ]
        )

    assert exc.value.code == 2


def test_research_save_output_prints_score_and_warnings(capsys) -> None:
    dataset = write_dataset_csv(DATASET_CSV)
    memory = research_memory_path()
    try:
        main(
            [
                "--config",
                "config/paper.yaml",
                "--backtest-csv",
                str(dataset),
                "--save-research-result",
                str(memory),
            ]
        )
        output = capsys.readouterr().out
        assert "score:" in output
        assert "warnings:" in output
        assert "memory path:" in output
    finally:
        dataset.unlink(missing_ok=True)
        memory.unlink(missing_ok=True)


def test_ranks_saved_results_by_score(capsys) -> None:
    memory = research_memory_path()
    try:
        store = ResearchMemory(memory)
        store.save_result(experiment_result("low", Decimal("1")))
        store.save_result(experiment_result("high", Decimal("10")))

        main(["--rank-research-results", str(memory)])

        output = capsys.readouterr().out
        assert output.index("high") < output.index("low")
    finally:
        memory.unlink(missing_ok=True)


def test_rank_prints_top_experiment_first(capsys) -> None:
    memory = research_memory_path()
    try:
        store = ResearchMemory(memory)
        store.save_result(experiment_result("second", Decimal("2")))
        store.save_result(experiment_result("first", Decimal("20")))

        main(["--rank-research-results", str(memory)])

        first_result_line = [
            line for line in capsys.readouterr().out.splitlines() if line.startswith("1.")
        ][0]
        assert "experiment id: first" in first_result_line
    finally:
        memory.unlink(missing_ok=True)


def test_ranked_research_output_includes_max_drawdown(capsys) -> None:
    memory = research_memory_path()
    try:
        ResearchMemory(memory).save_result(experiment_result("best", Decimal("10")))

        main(["--rank-research-results", str(memory)])

        assert "max drawdown:" in capsys.readouterr().out
    finally:
        memory.unlink(missing_ok=True)


def test_rank_works_without_config(capsys) -> None:
    memory = research_memory_path()
    try:
        ResearchMemory(memory).save_result(experiment_result("only", Decimal("1")))

        exit_code = main(["--rank-research-results", str(memory)])

        assert exit_code == 0
        assert "ranked research results:" in capsys.readouterr().out
    finally:
        memory.unlink(missing_ok=True)


def test_missing_research_memory_file_handled_clearly(capsys) -> None:
    missing = Path("tests/fixtures/missing_research_memory.jsonl")

    exit_code = main(["--rank-research-results", str(missing)])

    assert exit_code == 0
    assert "research memory file does not exist" in capsys.readouterr().out


def test_existing_commands_still_pass_after_rank_addition(capsys) -> None:
    exit_code = main(["--config", "config/paper.yaml", "--dry-run-once"])

    assert exit_code == 0
    assert "signal action:" in capsys.readouterr().out


def test_writes_markdown_research_report(capsys) -> None:
    memory = research_memory_path()
    report = research_report_path()
    try:
        ResearchMemory(memory).save_result(experiment_result("best", Decimal("10")))

        exit_code = main(
            [
                "--rank-research-results",
                str(memory),
                "--write-research-report",
                str(report),
            ]
        )

        assert exit_code == 0
        assert report.exists()
        output = capsys.readouterr().out
        assert f"report path: {report}" in output
        assert "total experiments included: 1" in output
    finally:
        memory.unlink(missing_ok=True)
        report.unlink(missing_ok=True)


def test_research_report_contains_top_results(capsys) -> None:
    memory = research_memory_path()
    report = research_report_path()
    try:
        ResearchMemory(memory).save_result(experiment_result("best", Decimal("10")))

        main(
            [
                "--rank-research-results",
                str(memory),
                "--write-research-report",
                str(report),
            ]
        )

        content = report.read_text(encoding="utf-8")
        assert "Top Results" in content
        assert "best" in content
    finally:
        memory.unlink(missing_ok=True)
        report.unlink(missing_ok=True)


def test_write_research_report_requires_rank_research_results() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--write-research-report", "tests/fixtures/report.md"])

    assert exc.value.code == 2


def test_report_missing_memory_file_handled(capsys) -> None:
    missing = Path("tests/fixtures/missing_report_memory.jsonl")
    report = research_report_path()
    try:
        exit_code = main(
            [
                "--rank-research-results",
                str(missing),
                "--write-research-report",
                str(report),
            ]
        )

        assert exit_code == 0
        assert "research memory file does not exist" in capsys.readouterr().out
        assert not report.exists()
    finally:
        report.unlink(missing_ok=True)


def test_existing_commands_still_pass_after_report_addition(capsys) -> None:
    exit_code = main(["--config", "config/paper.yaml", "--preview-cycles", "1"])

    assert exit_code == 0
    assert "total cycles: 1" in capsys.readouterr().out
