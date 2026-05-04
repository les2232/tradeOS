"""Application entrypoint for TradeOS V3.

The entrypoint will eventually coordinate configuration loading, observability,
and the selected runtime mode. It intentionally performs no trading behavior in
the skeleton.
"""

import argparse
import csv
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from collections.abc import Sequence
from collections.abc import Mapping

from tradeos.backtest.runner import BacktestResult, BacktestRunner
from tradeos.backtest.multi_month import (
    MultiMonthDatasetResult,
    write_multi_month_report,
)
from tradeos.backtest.walk_forward import (
    WalkForwardCandidate,
    WalkForwardResult,
    build_warmup_history,
    select_best_candidate,
    split_walk_forward_bars,
    walk_forward_verdict,
)
from tradeos.config.loader import load_config
from tradeos.config.schema import StrategyConfig
from tradeos.data.alpaca_market_data import download_historical_bars_to_csv
from tradeos.data.alpaca_market_data import AlpacaLatestBarFeed
from tradeos.data.historical_dataset import DatasetSummary, load_bars_from_csv, summarize_bars
from tradeos.domain.enums import DataQualityStatus
from tradeos.domain.models import Bar, PortfolioState
from tradeos.engine.cycle import TradingCycle
from tradeos.engine.trading_loop import PaperTradingLoop, TradingLoopSafetyError
from tradeos.execution.broker import AlpacaPaperBroker, FakeBroker
from tradeos.execution.reconciliation import ReconciliationService
from tradeos.observability.events import CycleSummary, summarize_latest_cycle
from tradeos.observability.reports import summarize_event_counts
from tradeos.research.experiment import ExperimentSpec
from tradeos.research.memory import ResearchMemory
from tradeos.research.report import write_markdown_report
from tradeos.risk.risk_manager import RiskManager
from tradeos.storage.sqlite import SQLiteEventStore
from tradeos.strategy.mean_reversion import MeanReversionStrategy
from tradeos.strategy.momentum_breakout import MomentumBreakoutStrategy
from tradeos.domain.enums import StrategyMode


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    """Run the safe TradeOS V3 command-line entrypoint."""

    parser = argparse.ArgumentParser(description="TradeOS V3")
    parser.add_argument("--config", help="Path to a V3 YAML config file.")
    parser.add_argument(
        "--dry-run-once",
        action="store_true",
        help="Run one deterministic fake-broker cycle without submitting orders.",
    )
    parser.add_argument(
        "--preview-cycles",
        type=_positive_int,
        help="Run N deterministic dry-run cycles without submitting orders.",
    )
    parser.add_argument(
        "--dataset-csv",
        help="CSV dataset of historical bars for preview mode.",
    )
    parser.add_argument(
        "--backtest-csv",
        help="CSV dataset of historical bars for offline backtest mode.",
    )
    parser.add_argument(
        "--save-research-result",
        help="Optional JSONL research memory path for scored backtest results.",
    )
    parser.add_argument(
        "--rank-research-results",
        help="Rank and print saved research results from a JSONL memory file.",
    )
    parser.add_argument(
        "--write-research-report",
        help="Write a markdown report from ranked research results.",
    )
    parser.add_argument(
        "--write-sweep-report",
        help="Optional markdown report path for parameter sweep results.",
    )
    parser.add_argument(
        "--export-backtest-csv",
        help="Optional CSV path for exporting one row of backtest metrics.",
    )
    parser.add_argument(
        "--append-backtest-csv",
        help="Optional CSV path for appending one row of backtest metrics.",
    )
    parser.add_argument(
        "--entry-threshold-pct",
        type=_positive_decimal_override,
        help="Override strategy entry threshold for backtest experiments.",
    )
    parser.add_argument(
        "--exit-threshold-pct",
        type=_positive_decimal_override,
        help="Override strategy exit threshold for backtest experiments.",
    )
    parser.add_argument(
        "--sma-bars",
        type=_positive_int_override,
        help="Override strategy SMA bars for backtest experiments.",
    )
    parser.add_argument(
        "--max-atr-percentile",
        type=_atr_percentile_override,
        help="Override strategy max ATR percentile for backtest experiments.",
    )
    parser.add_argument(
        "--strategy-mode",
        choices=[item.value for item in StrategyMode],
        help="Override strategy mode for backtest experiments.",
    )
    parser.add_argument(
        "--breakout-threshold-pct",
        type=_positive_decimal_override,
        help="Override momentum breakout entry threshold.",
    )
    parser.add_argument(
        "--momentum-exit-threshold-pct",
        type=_positive_decimal_override,
        help="Override momentum exit threshold.",
    )
    parser.add_argument(
        "--momentum-stop-loss-pct",
        type=_positive_decimal_override,
        help="Override momentum stop loss threshold.",
    )
    parser.add_argument(
        "--sweep-entry-thresholds",
        type=_decimal_list,
        help="Comma-separated entry thresholds for backtest parameter sweeps.",
    )
    parser.add_argument(
        "--sweep-exit-thresholds",
        type=_decimal_list,
        help="Comma-separated exit thresholds for backtest parameter sweeps.",
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run walk-forward selection/test evaluation from sweep parameters.",
    )
    parser.add_argument(
        "--multi-month-report",
        help="Write a markdown report across multiple walk-forward CSV datasets.",
    )
    parser.add_argument(
        "--download-history",
        action="store_true",
        help="Download Alpaca historical bars to CSV for offline backtesting.",
    )
    parser.add_argument(
        "--paper-live-once",
        action="store_true",
        help="Run one paper-safe live data loop using the configured paper broker.",
    )
    parser.add_argument(
        "--submit-paper-orders",
        action="store_true",
        help="Allow --paper-live-once to submit orders to the paper broker.",
    )
    parser.add_argument("--symbols", help="Comma-separated symbols for history download.")
    parser.add_argument("--start", help="History download start date/datetime.")
    parser.add_argument("--end", help="History download end date/datetime.")
    parser.add_argument("--timeframe", help="History download timeframe, such as 15Min.")
    parser.add_argument("--feed", help="History download feed, such as iex.")
    parser.add_argument("--output", help="History download output CSV path.")
    args = parser.parse_args(argv)

    selected_commands = sum(
        [
            bool(args.dry_run_once),
            args.preview_cycles is not None,
            args.backtest_csv is not None,
            args.rank_research_results is not None,
            bool(args.download_history),
            bool(args.paper_live_once),
        ]
    )
    if selected_commands != 1:
        parser.error(
            "choose exactly one of --dry-run-once, --preview-cycles, --backtest-csv, "
            "--rank-research-results, --download-history, or --paper-live-once"
        )
    if args.download_history:
        _run_download_history(args, parser, env=env)
        return 0
    if args.write_research_report and args.rank_research_results is None:
        parser.error("--write-research-report requires --rank-research-results")
    if args.write_sweep_report and not _has_sweep_overrides(args):
        parser.error("--write-sweep-report requires sweep mode")
    if args.rank_research_results is not None:
        _print_ranked_research_results(
            args.rank_research_results,
            args.write_research_report,
        )
        return 0
    if args.config is None:
        parser.error("--config is required for this command")
    if args.submit_paper_orders and not args.paper_live_once:
        parser.error("--submit-paper-orders requires --paper-live-once")
    if args.dataset_csv and args.preview_cycles is None:
        parser.error("--dataset-csv requires --preview-cycles")
    if args.save_research_result and args.backtest_csv is None:
        parser.error("--save-research-result requires --backtest-csv")
    if args.export_backtest_csv and args.backtest_csv is None:
        parser.error("--export-backtest-csv requires --backtest-csv")
    if args.append_backtest_csv and args.backtest_csv is None:
        parser.error("--append-backtest-csv requires --backtest-csv")
    if _has_strategy_overrides(args) and args.backtest_csv is None:
        parser.error("strategy parameter overrides require --backtest-csv")
    if _has_sweep_overrides(args) and args.backtest_csv is None:
        parser.error("strategy parameter sweeps require --backtest-csv")
    if _has_sweep_overrides(args) and args.export_backtest_csv is not None:
        parser.error("--export-backtest-csv is only for single backtests; use --append-backtest-csv with sweeps")
    if args.walk_forward and not (args.backtest_csv and _has_sweep_overrides(args)):
        parser.error("--walk-forward requires --backtest-csv and sweep options")
    if args.multi_month_report and not (
        args.backtest_csv and args.walk_forward and _has_sweep_overrides(args)
    ):
        parser.error("--multi-month-report requires --backtest-csv, --walk-forward, and sweep options")

    config = load_config(args.config)
    if args.paper_live_once:
        _run_paper_live_once(config, submit_orders=args.submit_paper_orders, env=env)
        return 0
    if args.backtest_csv is not None:
        active_strategy = _strategy_config_with_overrides(config.strategy, args)
        if args.multi_month_report is not None:
            _run_multi_month_report(
                config=replace(config, strategy=active_strategy),
                dataset_csvs=args.backtest_csv,
                sweep_entry_thresholds=args.sweep_entry_thresholds,
                sweep_exit_thresholds=args.sweep_exit_thresholds,
                report_path=args.multi_month_report,
            )
            return 0
        _run_backtest_csv(
            replace(config, strategy=active_strategy),
            args.backtest_csv,
            args.save_research_result,
            args.export_backtest_csv,
            args.append_backtest_csv,
            args.sweep_entry_thresholds,
            args.sweep_exit_thresholds,
            args.write_sweep_report,
            args.walk_forward,
        )
        return 0

    event_store = SQLiteEventStore(":memory:")
    broker = FakeBroker(_starting_portfolio())
    cycle = TradingCycle(
        strategy=_strategy_from_config(config.strategy),
        risk_manager=RiskManager(),
        broker=broker,
        reconciliation_service=ReconciliationService(),
        execute_orders=False,
        event_store=event_store,
    )

    if args.preview_cycles is not None:
        bars = _preview_bars(
            symbol=config.symbols[0],
            timeframe=config.timeframe,
            count=args.preview_cycles,
            dataset_csv=args.dataset_csv,
        )
        for input_bar in bars:
            cycle.run(
                bar=input_bar,
                strategy_config=config.strategy,
                risk_config=config.risk,
                local_positions=[],
            )
        _print_preview_summary(len(bars), event_store)
        return 0

    result = cycle.run(
        bar=_fake_bar(config.symbols[0], config.timeframe, 0),
        strategy_config=config.strategy,
        risk_config=config.risk,
        local_positions=[],
    )

    print(f"symbol: {result.signal.symbol}")
    print(f"signal action: {result.signal.action.value}")
    print(f"signal reason: {result.signal.reason}")
    print(f"risk status: {result.risk_decision.status.value}")
    print(f"risk reason: {result.risk_decision.reason}")
    print(f"reconciliation status: {result.reconciliation_result.status.value}")
    print(f"execution allowed: {str(result.execution_allowed).lower()}")
    print(f"order created: {_yes_no(result.order_intent is not None)}")
    print(f"fill received: {_yes_no(result.fill is not None)}")
    return 0


def _run_download_history(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    required = {
        "--symbols": args.symbols,
        "--start": args.start,
        "--end": args.end,
        "--timeframe": args.timeframe,
        "--feed": args.feed,
        "--output": args.output,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        parser.error("--download-history requires " + ", ".join(missing))
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    if not symbols:
        parser.error("--symbols must include at least one symbol")
    row_count = download_historical_bars_to_csv(
        symbols=symbols,
        start=args.start,
        end=args.end,
        timeframe=args.timeframe,
        feed=args.feed,
        output=args.output,
        env=env,
    )
    print("historical data download complete")
    print(f"symbols: {', '.join(symbols)}")
    print(f"rows: {row_count}")
    print(f"output: {args.output}")


def _run_paper_live_once(
    config,
    *,
    submit_orders: bool = False,
    env: Mapping[str, str] | None = None,
) -> None:
    event_store = SQLiteEventStore("data/paper_live_events.sqlite")
    broker = AlpacaPaperBroker.from_env(paper=config.broker.paper, env=env)
    market_data = AlpacaLatestBarFeed.from_env(feed=config.data.feed, env=env)
    cycle = TradingCycle(
        strategy=_strategy_from_config(config.strategy),
        risk_manager=RiskManager(),
        broker=broker,
        reconciliation_service=ReconciliationService(),
        execute_orders=submit_orders,
        event_store=event_store,
    )
    try:
        result = PaperTradingLoop(
            config=config,
            broker=broker,
            market_data=market_data,
            cycle=cycle,
            allow_order_submission=submit_orders,
        ).run_once()
    except TradingLoopSafetyError as exc:
        print(f"paper live loop blocked: {exc}")
        print("paper order submission: no")
        return
    print("paper live loop complete")
    print(f"symbols requested: {', '.join(config.symbols)}")
    print(f"bars received: {result.bars_received}")
    print(f"warmup bars loaded: {result.warmup_bars_loaded}")
    print(f"cycles completed: {result.cycles_completed}")
    if result.unmanaged_positions:
        print(f"unmanaged positions: {', '.join(result.unmanaged_positions)}")
    else:
        print("unmanaged positions: none")
    print(f"paper order submission: {_yes_no(submit_orders)}")
    print("cycle results:")
    for cycle_result in result.results:
        blocked_rules = (
            ",".join(cycle_result.risk_decision.blocked_rules)
            if cycle_result.risk_decision.blocked_rules
            else "none"
        )
        print(
            f"  {cycle_result.signal.symbol}: {cycle_result.signal.action.value} | "
            f"bar {cycle_result.signal.timestamp.isoformat()} | "
            f"risk {cycle_result.risk_decision.status.value} | "
            f"signal {cycle_result.signal.reason} | "
            f"rules {blocked_rules} | "
            f"reconciliation {cycle_result.reconciliation_result.status.value} | "
            f"execution {_cycle_execution_status(cycle_result)} | "
            f"{cycle_result.reason}"
        )
    print("event store: data/paper_live_events.sqlite")


def _cycle_execution_status(cycle_result) -> str:
    if cycle_result.fill is not None:
        return "filled"
    if cycle_result.order_submission is not None:
        return cycle_result.order_submission.status.value
    if not cycle_result.execution_allowed:
        return "blocked"
    if cycle_result.order_intent is not None:
        return "no_submit"
    return "not_evaluated"


def _starting_portfolio() -> PortfolioState:
    cash = Decimal("100000")
    return PortfolioState(
        cash=cash,
        equity=cash,
        buying_power=cash,
        positions=[],
        timestamp=datetime.now(UTC),
    )


def _fake_bar(symbol: str, timeframe: str, index: int = 0) -> Bar:
    close = Decimal("100") + Decimal(index)
    return Bar(
        symbol=symbol,
        timestamp=datetime.now(UTC),
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        timeframe=timeframe,
        data_quality=DataQualityStatus.OK,
    )


def _preview_bars(
    *,
    symbol: str,
    timeframe: str,
    count: int,
    dataset_csv: str | None,
) -> list[Bar]:
    if dataset_csv is None:
        return [_fake_bar(symbol, timeframe, index) for index in range(count)]

    dataset_path = Path(dataset_csv)
    if not dataset_path.exists():
        raise ValueError(f"dataset CSV does not exist: {dataset_csv}")
    bars = [bar for bar in load_bars_from_csv(dataset_path) if bar.symbol == symbol]
    return bars[:count]


def _load_symbols_bars(dataset_csv: str, symbols: list[str]) -> list[Bar]:
    dataset_path = Path(dataset_csv)
    if not dataset_path.exists():
        raise ValueError(f"backtest CSV does not exist: {dataset_csv}")
    allowed_symbols = set(symbols)
    return [bar for bar in load_bars_from_csv(dataset_path) if bar.symbol in allowed_symbols]


def _run_backtest_csv(
    config,
    dataset_csv: str,
    save_research_result: str | None = None,
    export_backtest_csv: str | None = None,
    append_backtest_csv: str | None = None,
    sweep_entry_thresholds: list[Decimal] | None = None,
    sweep_exit_thresholds: list[Decimal] | None = None,
    write_sweep_report: str | None = None,
    walk_forward: bool = False,
) -> None:
    bars = _load_symbols_bars(dataset_csv, config.symbols)
    dataset_summary = summarize_bars(bars)
    _print_dataset_summary(dataset_summary)
    if sweep_entry_thresholds is not None or sweep_exit_thresholds is not None:
        if walk_forward:
            _run_walk_forward(
                config=config,
                dataset_csv=dataset_csv,
                bars=bars,
                sweep_entry_thresholds=sweep_entry_thresholds,
                sweep_exit_thresholds=sweep_exit_thresholds,
            )
            return
        _run_backtest_sweep(
            config=config,
            dataset_csv=dataset_csv,
            bars=bars,
            dataset_summary=dataset_summary,
            save_research_result=save_research_result,
            append_backtest_csv=append_backtest_csv,
            sweep_entry_thresholds=sweep_entry_thresholds,
            sweep_exit_thresholds=sweep_exit_thresholds,
            write_sweep_report=write_sweep_report,
        )
        return

    _print_strategy_parameters(config.strategy)
    result = _run_single_backtest(config, bars)
    _print_backtest_result(result)
    if export_backtest_csv is not None:
        _write_backtest_csv_export(
            result=result,
            dataset_summary=dataset_summary,
            strategy_config=config.strategy,
            path=export_backtest_csv,
        )
        print(f"backtest CSV export: {export_backtest_csv}")
    if append_backtest_csv is not None:
        _append_backtest_csv_export(
            result=result,
            dataset_summary=dataset_summary,
            strategy_config=config.strategy,
            path=append_backtest_csv,
        )
        print(f"backtest CSV append: {append_backtest_csv}")
    if save_research_result is not None:
        _save_research_result(
            config=config,
            dataset_csv=dataset_csv,
            bars=bars,
            result=result,
            memory_path=save_research_result,
        )


def _run_multi_month_report(
    *,
    config,
    dataset_csvs: str,
    sweep_entry_thresholds: list[Decimal] | None,
    sweep_exit_thresholds: list[Decimal] | None,
    report_path: str,
) -> None:
    results: list[MultiMonthDatasetResult] = []
    for dataset_csv in _csv_path_list(dataset_csvs):
        bars = _load_symbols_bars(dataset_csv, config.symbols)
        walk_forward_result = _evaluate_walk_forward(
            config=config,
            dataset_csv=dataset_csv,
            bars=bars,
            sweep_entry_thresholds=sweep_entry_thresholds,
            sweep_exit_thresholds=sweep_exit_thresholds,
        )
        results.append(
            MultiMonthDatasetResult(
                dataset_path=dataset_csv,
                walk_forward_result=walk_forward_result,
            )
        )
    summary = write_multi_month_report(results=results, path=report_path)
    print(f"multi-month report path: {report_path}")
    print(f"datasets: {summary.dataset_count}")
    print(f"PASS count: {summary.pass_count}")
    print(f"FAIL count: {summary.fail_count}")
    print(f"consistency score: {summary.consistency_score}")


def _csv_path_list(value: str) -> list[str]:
    paths = [item.strip() for item in value.split(",") if item.strip()]
    if not paths:
        raise ValueError("--backtest-csv must include at least one CSV path")
    return paths


def _run_single_backtest(
    config,
    bars: list[Bar],
    initial_history: dict[str, list[Bar]] | None = None,
) -> BacktestResult:
    backtest_runner = BacktestRunner(
        strategy=_strategy_from_config(config.strategy),
        risk_manager=RiskManager(),
        broker=FakeBroker(_starting_portfolio()),
        reconciliation_service=ReconciliationService(),
    )
    result = backtest_runner.run(
        bars=bars,
        strategy_config=config.strategy,
        risk_config=config.risk,
        local_positions=[],
        backtest_costs=config.backtest_costs,
        initial_history=initial_history,
        force_flatten_end=True,
    )
    return result


def _run_backtest_sweep(
    *,
    config,
    dataset_csv: str,
    bars: list[Bar],
    dataset_summary: DatasetSummary,
    save_research_result: str | None,
    append_backtest_csv: str | None,
    sweep_entry_thresholds: list[Decimal] | None,
    sweep_exit_thresholds: list[Decimal] | None,
    write_sweep_report: str | None,
) -> None:
    entry_thresholds = sweep_entry_thresholds or [config.strategy.entry_threshold_pct]
    exit_thresholds = sweep_exit_thresholds or [config.strategy.exit_threshold_pct]
    print("parameter sweep results:")
    print(
        "entry_threshold_pct | exit_threshold_pct | total PnL | profit factor | "
        "max drawdown | closed trades | score"
    )
    sweep_results = []
    for entry_threshold in entry_thresholds:
        for exit_threshold in exit_thresholds:
            strategy_config = _strategy_config_for_sweep(
                config.strategy,
                entry_threshold,
                exit_threshold,
            )
            sweep_config = replace(config, strategy=strategy_config)
            result = _run_single_backtest(sweep_config, bars)
            experiment_result = _experiment_result_from_backtest(
                config=sweep_config,
                dataset_csv=dataset_csv,
                bars=bars,
                result=result,
            )
            print(
                f"{entry_threshold} | {exit_threshold} | {result.total_pnl} | "
                f"{result.profit_factor} | {result.max_drawdown} | "
                f"{result.closed_trades} | {experiment_result.score}"
            )
            sweep_results.append((strategy_config, result, experiment_result))
            if append_backtest_csv is not None:
                _append_backtest_csv_export(
                    result=result,
                    dataset_summary=dataset_summary,
                    strategy_config=strategy_config,
                    path=append_backtest_csv,
                )
            if save_research_result is not None:
                _save_experiment_result(experiment_result, save_research_result)
    if append_backtest_csv is not None:
        print(f"backtest CSV append: {append_backtest_csv}")
    if save_research_result is not None:
        print(f"memory path: {save_research_result}")
    _print_sweep_summary(sweep_results)
    if write_sweep_report is not None:
        _write_sweep_report(
            sweep_results=sweep_results,
            entry_thresholds=entry_thresholds,
            exit_thresholds=exit_thresholds,
            path=write_sweep_report,
        )
        print(f"sweep report path: {write_sweep_report}")


def _run_walk_forward(
    *,
    config,
    dataset_csv: str,
    bars: list[Bar],
    sweep_entry_thresholds: list[Decimal] | None,
    sweep_exit_thresholds: list[Decimal] | None,
) -> None:
    result = _evaluate_walk_forward(
        config=config,
        dataset_csv=dataset_csv,
        bars=bars,
        sweep_entry_thresholds=sweep_entry_thresholds,
        sweep_exit_thresholds=sweep_exit_thresholds,
    )
    _print_walk_forward_result(result)


def _evaluate_walk_forward(
    *,
    config,
    dataset_csv: str,
    bars: list[Bar],
    sweep_entry_thresholds: list[Decimal] | None,
    sweep_exit_thresholds: list[Decimal] | None,
) -> WalkForwardResult:
    selection_bars, test_bars = split_walk_forward_bars(bars)
    entry_thresholds = sweep_entry_thresholds or [config.strategy.entry_threshold_pct]
    exit_thresholds = sweep_exit_thresholds or [config.strategy.exit_threshold_pct]
    candidates: list[WalkForwardCandidate] = []
    for entry_threshold in entry_thresholds:
        for exit_threshold in exit_thresholds:
            strategy_config = _strategy_config_for_sweep(
                config.strategy,
                entry_threshold,
                exit_threshold,
            )
            selection_config = replace(config, strategy=strategy_config)
            selection_result = _run_single_backtest(selection_config, selection_bars)
            experiment_result = _experiment_result_from_backtest(
                config=selection_config,
                dataset_csv=dataset_csv,
                bars=selection_bars,
                result=selection_result,
            )
            candidates.append(
                WalkForwardCandidate(
                    strategy_config=strategy_config,
                    backtest_result=selection_result,
                    experiment_result=experiment_result,
                )
            )

    best_candidate = select_best_candidate(candidates)
    test_result = None
    warmup_history = build_warmup_history(selection_bars)
    if best_candidate is not None:
        test_config = replace(config, strategy=best_candidate.strategy_config)
        test_result = _run_single_backtest(
            test_config,
            test_bars,
            initial_history=warmup_history,
        )
    return WalkForwardResult(
        selection_bars=len(selection_bars),
        test_bars=len(test_bars),
        test_warmup_bars=sum(len(symbol_history) for symbol_history in warmup_history.values()),
        best_candidate=best_candidate,
        test_result=test_result,
        verdict=walk_forward_verdict(test_result),
    )


def _print_walk_forward_result(result: WalkForwardResult) -> None:
    print("walk-forward result:")
    print(f"selection bars: {result.selection_bars}")
    print(f"test bars: {result.test_bars}")
    print(f"test warmup bars: {result.test_warmup_bars}")
    if result.best_candidate is None or result.test_result is None:
        print("no walk-forward result")
        print(f"verdict: {result.verdict}")
        return

    candidate = result.best_candidate
    print(f"best selection entry threshold: {_selected_entry_threshold(candidate.strategy_config)}")
    print(f"best selection exit threshold: {_selected_exit_threshold(candidate.strategy_config)}")
    print(f"selection score: {candidate.experiment_result.score}")
    print(f"selection total PnL: {candidate.backtest_result.total_pnl}")
    print(f"selection realized PnL: {candidate.backtest_result.realized_pnl}")
    print(f"selection unrealized PnL: {candidate.backtest_result.unrealized_pnl}")
    print(f"selection closed trades: {candidate.backtest_result.closed_trades}")
    print("selection trade diagnostics:")
    _print_trade_diagnostics(candidate.backtest_result)
    print("selection closed-trade edge attribution:")
    _print_edge_attribution(candidate.backtest_result)
    print("selection signal reason counts:")
    _print_counts(candidate.backtest_result.signal_reason_counts)
    print("selection risk reason counts:")
    _print_counts(candidate.backtest_result.risk_reason_counts)
    print(f"test total PnL: {result.test_result.total_pnl}")
    print(f"test realized PnL: {result.test_result.realized_pnl}")
    print(f"test unrealized PnL: {result.test_result.unrealized_pnl}")
    print(f"test profit factor: {result.test_result.profit_factor}")
    print(f"test max drawdown: {result.test_result.max_drawdown}")
    print(f"test closed trades: {result.test_result.closed_trades}")
    print("test trade diagnostics:")
    _print_trade_diagnostics(result.test_result)
    print("test closed-trade edge attribution:")
    _print_edge_attribution(result.test_result)
    print("test signal reason counts:")
    _print_counts(result.test_result.signal_reason_counts)
    print("test risk reason counts:")
    _print_counts(result.test_result.risk_reason_counts)
    print(f"verdict: {result.verdict}")


def _print_sweep_summary(sweep_results) -> None:
    print("sweep summary:")
    if not sweep_results:
        print("  no sweep results")
        return

    ranked = sorted(sweep_results, key=lambda item: item[2].score, reverse=True)
    best_strategy, best_result, best_experiment = ranked[0]
    print(f"best score: {best_experiment.score}")
    print(f"best entry threshold: {_selected_entry_threshold(best_strategy)}")
    print(f"best exit threshold: {_selected_exit_threshold(best_strategy)}")
    print(f"best total PnL: {best_result.total_pnl}")
    print(f"best profit factor: {best_result.profit_factor}")
    print(f"best max drawdown: {best_result.max_drawdown}")
    print(f"best closed trades: {best_result.closed_trades}")
    print("top 3 sweep results:")
    for rank, (strategy, result, experiment) in enumerate(ranked[:3], start=1):
        print(
            f"{rank}. score: {experiment.score} | "
            f"entry threshold: {_selected_entry_threshold(strategy)} | "
            f"exit threshold: {_selected_exit_threshold(strategy)} | "
            f"total PnL: {result.total_pnl} | "
            f"profit factor: {result.profit_factor} | "
            f"max drawdown: {result.max_drawdown} | "
            f"closed trades: {result.closed_trades}"
        )


def _write_sweep_report(
    *,
    sweep_results,
    entry_thresholds: list[Decimal],
    exit_thresholds: list[Decimal],
    path: str,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ranked = sorted(sweep_results, key=lambda item: item[2].score, reverse=True)
    lines = [
        "# Parameter Sweep Report",
        "",
        f"Total sweep runs: {len(sweep_results)}",
        "",
        "## Best Result",
    ]
    if not ranked:
        lines.extend(["", "No sweep results."])
    else:
        best_strategy, best_result, best_experiment = ranked[0]
        lines.extend(
            [
                "",
                f"- Score: {best_experiment.score}",
                f"- Entry threshold: {_selected_entry_threshold(best_strategy)}",
                f"- Exit threshold: {_selected_exit_threshold(best_strategy)}",
                f"- Total PnL: {best_result.total_pnl}",
                f"- Profit factor: {best_result.profit_factor}",
                f"- Max drawdown: {best_result.max_drawdown}",
                f"- Closed trades: {best_result.closed_trades}",
                "",
                "## Top 3 Results",
                "",
                "| Rank | Entry Threshold | Exit Threshold | Score | Total PnL | Profit Factor | Max Drawdown | Closed Trades |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for rank, (strategy, result, experiment) in enumerate(ranked[:3], start=1):
            lines.append(
                f"| {rank} | {_selected_entry_threshold(strategy)} | "
                f"{_selected_exit_threshold(strategy)} | {experiment.score} | "
                f"{result.total_pnl} | {result.profit_factor} | "
                f"{result.max_drawdown} | {result.closed_trades} |"
            )
        lines.extend(["", "## Research Warnings", ""])
        warning_lines = [
            (
                f"- {experiment.experiment_id}: "
                f"{', '.join(experiment.warnings) if experiment.warnings else 'none'}"
            )
            for _, _, experiment in ranked
        ]
        lines.extend(warning_lines)

    lines.extend(
        [
            "",
            "## Parameter Combinations Tested",
            "",
            "| Entry Threshold | Exit Threshold |",
            "| --- | --- |",
        ]
    )
    for entry_threshold in entry_thresholds:
        for exit_threshold in exit_thresholds:
            lines.append(f"| {entry_threshold} | {exit_threshold} |")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _experiment_spec_from_backtest(config, dataset_csv: str, bars: list[Bar]) -> ExperimentSpec:
    if not bars:
        raise ValueError("cannot save research result without filtered bars")
    tested_symbols = sorted({bar.symbol for bar in bars})
    return ExperimentSpec(
        strategy_mode=config.strategy.strategy_mode,
        symbols=tested_symbols,
        start_date=bars[0].timestamp.date(),
        end_date=bars[-1].timestamp.date(),
        timeframe=config.timeframe,
        parameters={
            "sma_bars": config.strategy.sma_bars,
            "entry_threshold_pct": str(config.strategy.entry_threshold_pct),
            "exit_threshold_pct": str(config.strategy.exit_threshold_pct),
            "trend_filter_enabled": config.strategy.trend_filter_enabled,
            "max_atr_percentile": str(config.strategy.max_atr_percentile),
            "breakout_sma_bars": config.strategy.breakout_sma_bars,
            "breakout_threshold_pct": str(config.strategy.breakout_threshold_pct),
            "momentum_exit_threshold_pct": str(
                config.strategy.momentum_exit_threshold_pct
            ),
            "momentum_stop_loss_pct": str(config.strategy.momentum_stop_loss_pct),
            "require_positive_sma_slope": config.strategy.require_positive_sma_slope,
        },
        dataset_id=Path(dataset_csv).name,
    )


def _experiment_result_from_backtest(
    *,
    config,
    dataset_csv: str,
    bars: list[Bar],
    result: BacktestResult,
):
    experiment_spec = _experiment_spec_from_backtest(
        config=config,
        dataset_csv=dataset_csv,
        bars=bars,
    )
    backtest_runner = BacktestRunner(
        strategy=_strategy_from_config(config.strategy),
        risk_manager=RiskManager(),
        broker=FakeBroker(_starting_portfolio()),
        reconciliation_service=ReconciliationService(),
    )
    return backtest_runner.to_experiment_result(result, experiment_spec)


def _save_research_result(
    *,
    config,
    dataset_csv: str,
    bars: list[Bar],
    result: BacktestResult,
    memory_path: str,
) -> None:
    experiment_result = _experiment_result_from_backtest(
        config=config,
        dataset_csv=dataset_csv,
        bars=bars,
        result=result,
    )
    _save_experiment_result(experiment_result, memory_path)
    print(f"memory path: {memory_path}")


def _save_experiment_result(experiment_result, memory_path: str) -> None:
    memory = ResearchMemory(memory_path)
    duplicate = memory.has_experiment(experiment_result.experiment_id)
    memory.save_result(experiment_result)
    print(f"experiment id: {experiment_result.experiment_id}")
    print(f"score: {experiment_result.score}")
    print(
        "warnings: "
        f"{', '.join(experiment_result.warnings) if experiment_result.warnings else 'none'}"
    )
    if duplicate:
        print("research result already exists; duplicate ignored")
    else:
        print("research result saved")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("--preview-cycles must be at least 1")
    return parsed


def _positive_int_override(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("override must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("override must be positive")
    return parsed


def _positive_decimal_override(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError("override must be a decimal value") from exc
    if parsed <= Decimal("0"):
        raise argparse.ArgumentTypeError("override must be positive")
    return parsed


def _atr_percentile_override(value: str) -> Decimal:
    parsed = _positive_decimal_override(value)
    if parsed > Decimal("100"):
        raise argparse.ArgumentTypeError("max ATR percentile must be between 0 and 100")
    return parsed


def _decimal_list(value: str) -> list[Decimal]:
    raw_values = [item.strip() for item in value.split(",")]
    if not raw_values or any(not item for item in raw_values):
        raise argparse.ArgumentTypeError("threshold list must contain decimal values")
    parsed_values = []
    for item in raw_values:
        try:
            parsed = Decimal(item)
        except Exception as exc:
            raise argparse.ArgumentTypeError(
                "threshold list must contain decimal values"
            ) from exc
        if parsed <= Decimal("0"):
            raise argparse.ArgumentTypeError("threshold values must be positive")
        parsed_values.append(parsed)
    return parsed_values


def _has_strategy_overrides(args: argparse.Namespace) -> bool:
    return any(
        value is not None
        for value in [
            args.strategy_mode,
            args.entry_threshold_pct,
            args.exit_threshold_pct,
            args.sma_bars,
            args.max_atr_percentile,
            args.breakout_threshold_pct,
            args.momentum_exit_threshold_pct,
            args.momentum_stop_loss_pct,
        ]
    )


def _has_sweep_overrides(args: argparse.Namespace) -> bool:
    return args.sweep_entry_thresholds is not None or args.sweep_exit_thresholds is not None


def _strategy_config_with_overrides(
    strategy_config: StrategyConfig,
    args: argparse.Namespace,
) -> StrategyConfig:
    return replace(
        strategy_config,
        strategy_mode=(
            StrategyMode(args.strategy_mode)
            if args.strategy_mode is not None
            else strategy_config.strategy_mode
        ),
        entry_threshold_pct=(
            args.entry_threshold_pct
            if args.entry_threshold_pct is not None
            else strategy_config.entry_threshold_pct
        ),
        exit_threshold_pct=(
            args.exit_threshold_pct
            if args.exit_threshold_pct is not None
            else strategy_config.exit_threshold_pct
        ),
        sma_bars=args.sma_bars if args.sma_bars is not None else strategy_config.sma_bars,
        max_atr_percentile=(
            args.max_atr_percentile
            if args.max_atr_percentile is not None
            else strategy_config.max_atr_percentile
        ),
        breakout_threshold_pct=(
            args.breakout_threshold_pct
            if args.breakout_threshold_pct is not None
            else strategy_config.breakout_threshold_pct
        ),
        momentum_exit_threshold_pct=(
            args.momentum_exit_threshold_pct
            if args.momentum_exit_threshold_pct is not None
            else strategy_config.momentum_exit_threshold_pct
        ),
        momentum_stop_loss_pct=(
            args.momentum_stop_loss_pct
            if args.momentum_stop_loss_pct is not None
            else strategy_config.momentum_stop_loss_pct
        ),
    )


def _strategy_config_for_sweep(
    strategy_config: StrategyConfig,
    entry_threshold: Decimal,
    exit_threshold: Decimal,
) -> StrategyConfig:
    if strategy_config.strategy_mode == StrategyMode.MOMENTUM_BREAKOUT:
        return replace(
            strategy_config,
            breakout_threshold_pct=entry_threshold,
            momentum_exit_threshold_pct=exit_threshold,
        )
    return replace(
        strategy_config,
        entry_threshold_pct=entry_threshold,
        exit_threshold_pct=exit_threshold,
    )


def _selected_entry_threshold(strategy_config: StrategyConfig) -> Decimal:
    if strategy_config.strategy_mode == StrategyMode.MOMENTUM_BREAKOUT:
        return strategy_config.breakout_threshold_pct
    return strategy_config.entry_threshold_pct


def _selected_exit_threshold(strategy_config: StrategyConfig) -> Decimal:
    if strategy_config.strategy_mode == StrategyMode.MOMENTUM_BREAKOUT:
        return strategy_config.momentum_exit_threshold_pct
    return strategy_config.exit_threshold_pct


def _strategy_from_config(strategy_config: StrategyConfig):
    if strategy_config.strategy_mode == StrategyMode.MEAN_REVERSION:
        return MeanReversionStrategy()
    if strategy_config.strategy_mode == StrategyMode.MOMENTUM_BREAKOUT:
        return MomentumBreakoutStrategy()
    raise ValueError(f"unsupported strategy mode: {strategy_config.strategy_mode}")


def _print_preview_summary(total_cycles: int, event_store: SQLiteEventStore) -> None:
    events = event_store.list_events()
    counts = summarize_event_counts(events)
    latest = summarize_latest_cycle(events)

    print(f"total cycles: {total_cycles}")
    print("event counts:")
    for event_type in sorted(counts):
        print(f"  {event_type}: {counts[event_type]}")
    print("latest cycle summary:")
    if latest is None:
        print("  none")
        return
    _print_cycle_summary(latest)


def _print_cycle_summary(summary: CycleSummary) -> None:
    print(f"  symbol: {summary.symbol}")
    print(f"  signal action: {summary.signal_action}")
    print(f"  signal reason: {summary.signal_reason}")
    print(f"  risk status: {summary.risk_status}")
    print(f"  risk reason: {summary.risk_reason}")
    print(f"  reconciliation status: {summary.reconciliation_status}")
    print(f"  execution allowed: {str(summary.execution_allowed).lower()}")
    print(f"  order created: {_yes_no(summary.order_created)}")
    print(f"  fill received: {_yes_no(summary.fill_received)}")
    print(f"  cycle reason: {summary.cycle_reason}")


def _print_backtest_result(result: BacktestResult) -> None:
    print(f"total bars: {result.total_bars}")
    print(f"total cycles: {result.total_cycles}")
    print(f"signals: {len(result.signals)}")
    print(f"orders created: {len(result.orders_created)}")
    print(f"fills: {len(result.fills)}")
    print(f"buy signals: {result.buy_signals}")
    print(f"sell signals: {result.sell_signals}")
    print(f"hold signals: {result.hold_signals}")
    print(f"approved risk decisions: {result.approved_risk_decisions}")
    print(f"blocked risk decisions: {result.blocked_risk_decisions}")
    print(f"dirty reconciliations: {result.dirty_reconciliation_count}")
    print(f"order intents created: {result.order_intents_created}")
    print(f"realized PnL: {result.realized_pnl}")
    print(f"unrealized PnL: {result.unrealized_pnl}")
    print(f"total PnL: {result.total_pnl}")
    print(f"max drawdown: {result.max_drawdown}")
    print(f"total costs: {result.total_costs}")
    print(f"ending positions count: {len(result.positions_end)}")
    print(f"closed trades: {result.closed_trades}")
    print(f"forced flatten trades: {result.forced_flatten_trades}")
    print(f"winning trades: {result.winning_trades}")
    print(f"losing trades: {result.losing_trades}")
    print(f"win rate: {result.win_rate}")
    print(f"gross profit: {result.gross_profit}")
    print(f"gross loss: {result.gross_loss}")
    print(f"profit factor: {result.profit_factor}")
    print(f"expectancy: {result.expectancy}")
    print("trade diagnostics:")
    _print_trade_diagnostics(result)
    print("closed-trade edge attribution:")
    _print_edge_attribution(result)
    print("signal reason counts:")
    _print_counts(result.signal_reason_counts)
    print("risk reason counts:")
    _print_counts(result.risk_reason_counts)
    print("event counts:")
    _print_counts(result.event_counts)
    print("latest cycle summary:")
    if result.latest_cycle is None:
        print("  none")
        return
    _print_cycle_summary(result.latest_cycle)


def _print_dataset_summary(summary: DatasetSummary) -> None:
    print(f"dataset rows: {summary.total_rows}")
    print(f"dataset symbols: {', '.join(summary.symbols) if summary.symbols else 'none'}")
    print(f"symbol count: {len(summary.symbols)}")
    print(f"tested symbols: {', '.join(summary.symbols) if summary.symbols else 'none'}")
    print(
        "dataset start: "
        f"{summary.start_timestamp.isoformat() if summary.start_timestamp else 'none'}"
    )
    print(
        "dataset end: "
        f"{summary.end_timestamp.isoformat() if summary.end_timestamp else 'none'}"
    )
    print("timeframe counts:")
    for timeframe in sorted(summary.timeframe_counts):
        print(f"  {timeframe}: {summary.timeframe_counts[timeframe]}")


def _print_counts(counts: dict[str, int] | dict[str, Decimal], indent: str = "  ") -> None:
    if not counts:
        print(f"{indent}none")
        return
    for key in sorted(counts):
        print(f"{indent}{key}: {counts[key]}")


def _print_trade_diagnostics(result: BacktestResult) -> None:
    trades = result.closed_trade_details
    if not trades:
        print("  worst trade PnL: 0")
        print("  best trade PnL: 0")
        print("  average bars held: 0")
        print("  worst max adverse excursion: 0")
        return
    worst_trade_pnl = min(trade.pnl for trade in trades)
    best_trade_pnl = max(trade.pnl for trade in trades)
    average_bars_held = Decimal(sum(trade.bars_held for trade in trades)) / Decimal(
        len(trades)
    )
    worst_adverse = min(trade.max_adverse_excursion for trade in trades)
    print(f"  worst trade PnL: {worst_trade_pnl}")
    print(f"  best trade PnL: {best_trade_pnl}")
    print(f"  average bars held: {average_bars_held}")
    print(f"  worst max adverse excursion: {worst_adverse}")


def _print_edge_attribution(result: BacktestResult) -> None:
    print(f"  Closed trade PnL total: {result.closed_trade_pnl_total}")
    print(f"  Attribution PnL total: {result.attribution_pnl_total}")
    print(f"  Unrealized PnL: {result.unrealized_pnl}")
    print("  PnL by symbol:")
    _print_counts(result.pnl_by_symbol, indent="    ")
    print("  Closed trades by symbol:")
    _print_counts(result.closed_trades_by_symbol, indent="    ")
    print("  PnL by entry hour UTC:")
    _print_counts(result.pnl_by_entry_hour, indent="    ")
    print("  Closed trades by entry hour UTC:")
    _print_counts(result.closed_trades_by_entry_hour, indent="    ")
    print("  PnL by exit reason:")
    _print_counts(result.pnl_by_exit_reason, indent="    ")
    print("  Closed trades by exit reason:")
    _print_counts(result.closed_trades_by_exit_reason, indent="    ")


def _print_strategy_parameters(strategy_config: StrategyConfig) -> None:
    print("active strategy parameters:")
    print(f"  strategy mode: {strategy_config.strategy_mode.value}")
    print(f"  sma bars: {strategy_config.sma_bars}")
    print(f"  entry threshold pct: {strategy_config.entry_threshold_pct}")
    print(f"  exit threshold pct: {strategy_config.exit_threshold_pct}")
    print(f"  breakout threshold pct: {strategy_config.breakout_threshold_pct}")
    print(
        "  momentum exit threshold pct: "
        f"{strategy_config.momentum_exit_threshold_pct}"
    )
    print(f"  momentum stop loss pct: {strategy_config.momentum_stop_loss_pct}")
    print(f"  max atr percentile: {strategy_config.max_atr_percentile}")


def _write_backtest_csv_export(
    *,
    result: BacktestResult,
    dataset_summary: DatasetSummary,
    strategy_config: StrategyConfig,
    path: str,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row = _backtest_csv_row(result, dataset_summary, strategy_config)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


def _append_backtest_csv_export(
    *,
    result: BacktestResult,
    dataset_summary: DatasetSummary,
    strategy_config: StrategyConfig,
    path: str,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row = _backtest_csv_row(result, dataset_summary, strategy_config)
    write_header = not output_path.exists() or output_path.stat().st_size == 0
    with output_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _backtest_csv_row(
    result: BacktestResult,
    dataset_summary: DatasetSummary,
    strategy_config: StrategyConfig,
) -> dict[str, object]:
    return {
        "dataset rows": dataset_summary.total_rows,
        "dataset symbols": ", ".join(dataset_summary.symbols),
        "dataset start": (
            dataset_summary.start_timestamp.isoformat()
            if dataset_summary.start_timestamp
            else ""
        ),
        "dataset end": (
            dataset_summary.end_timestamp.isoformat()
            if dataset_summary.end_timestamp
            else ""
        ),
        "sma bars": strategy_config.sma_bars,
        "entry threshold pct": strategy_config.entry_threshold_pct,
        "exit threshold pct": strategy_config.exit_threshold_pct,
        "max atr percentile": strategy_config.max_atr_percentile,
        "total cycles": result.total_cycles,
        "buy signals": result.buy_signals,
        "sell signals": result.sell_signals,
        "hold signals": result.hold_signals,
        "approved risk decisions": result.approved_risk_decisions,
        "blocked risk decisions": result.blocked_risk_decisions,
        "order intents created": result.order_intents_created,
        "realized PnL": result.realized_pnl,
        "unrealized PnL": result.unrealized_pnl,
        "total PnL": result.total_pnl,
        "total costs": result.total_costs,
        "max drawdown": result.max_drawdown,
        "closed trades": result.closed_trades,
        "win rate": result.win_rate,
        "profit factor": result.profit_factor,
        "expectancy": result.expectancy,
    }


def _print_ranked_research_results(
    memory_path: str,
    report_path: str | None = None,
) -> None:
    path = Path(memory_path)
    if not path.exists():
        print(f"research memory file does not exist: {memory_path}")
        return

    results = ResearchMemory(path).rank_results()
    if not results:
        print(f"research memory has no results: {memory_path}")
        return

    if report_path is not None:
        write_markdown_report(results, report_path)
        print(f"report path: {report_path}")
        print(f"total experiments included: {len(results)}")

    print("ranked research results:")
    for index, result in enumerate(results, start=1):
        print(
            f"{index}. experiment id: {result.experiment_id} | "
            f"strategy: {result.spec.strategy_mode.value} | "
            f"score: {result.score} | "
            f"profit factor: {result.profit_factor} | "
            f"max drawdown: {result.max_drawdown} | "
            f"net PnL: {result.net_pnl} | "
            f"num trades: {result.num_trades} | "
            f"warnings: {len(result.warnings)}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
