# TradeOS V3

TradeOS V3 is a clean rebuild of the trading system focused on safety, reliability, testability, and live/backtest parity.

## Current Status

TradeOS V3 now has safe offline contracts for configuration, domain models, strategy signals, risk gating, fake broker execution, reconciliation, event storage, backtesting, and deterministic research scoring.

## Safety Status

- Alpaca historical-data and paper-broker adapters are lazy-loaded only when requested.
- No real-money live trading command exists yet.
- A paper-safe live loop exists for one bounded pass through configured symbols.
- Paper live mode is bounded to one pass unless a future scheduler is added.
- Strategy code generates signals only.
- RiskManager is the final gate before an order intent can be created.
- Reconciliation blocks execution when broker and local state differ.

## Run Tests

```bash
python -m pytest tests/unit
```

## Dry Run Once

```bash
python -m tradeos.main --config config/paper.yaml --dry-run-once
```

## Preview Fake Cycles

```bash
python -m tradeos.main --config config/paper.yaml --preview-cycles 5
```

## Backtest From CSV

```bash
python -m tradeos.main --config config/paper.yaml --backtest-csv path/to/bars.csv
```

## Download Historical Bars

Historical downloads are for offline datasets only. This command reads market data and writes a CSV; it does not submit orders, start a live loop, or enable live trading.

Required environment variables:

```bash
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
```

Example:

```bash
python -m tradeos.main --download-history --symbols AAPL,MSFT,NVDA --start 2026-01-01 --end 2026-02-01 --timeframe 15Min --feed iex --output data/real_bars.csv
```

## Paper Live Loop

Install the optional Alpaca dependency and set paper API credentials:

```bash
python -m pip install -e ".[paper]"
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
```

Run one bounded paper-safe pass using live market data and the paper broker account:

```bash
python -m tradeos.main --config config/momentum_test.yaml --paper-live-once
```

The loop loads recent historical bars first so strategies have enough SMA
context before evaluating the latest live bar.

## Paper GUI

Launch the desktop paper console from the project directory:

```bash
python -m tradeos.dashboard.app
```

Use **Check Signals** for the default no-submit pass. The paper-submit button
stays disabled until you explicitly enable it in the GUI.

By default, this does not submit paper orders. To allow paper order submission, use the explicit opt-in flag:

```bash
python -m tradeos.main --config config/momentum_test.yaml --paper-live-once --submit-paper-orders
```

## Generate Sample Dataset

```bash
python scripts/generate_sample_dataset.py
```

This writes deterministic synthetic 15-minute bars for AAPL, MSFT, and NVDA to `data/sample_multisymbol_bars.csv`.

## Multi-Symbol Backtest

```bash
python -m tradeos.main --config config/paper.yaml --backtest-csv data/sample_multisymbol_bars.csv --entry-threshold-pct 0.005 --exit-threshold-pct 0.005
```

## Backtest With Parameter Overrides

```bash
python -m tradeos.main --config config/paper.yaml --backtest-csv path/to/bars.csv --entry-threshold-pct 0.002 --exit-threshold-pct 0.0015
```

## Save Research Result

```bash
python -m tradeos.main --config config/paper.yaml --backtest-csv path/to/bars.csv --save-research-result research_memory/experiments.jsonl
```

## Rank Research Results

```bash
python -m tradeos.main --rank-research-results research_memory/experiments.jsonl
```

## Run Parameter Sweep

```bash
python -m tradeos.main --config config/paper.yaml --backtest-csv path/to/bars.csv --sweep-entry-thresholds 0.001,0.002,0.003 --sweep-exit-thresholds 0.001,0.002,0.003 --append-backtest-csv results/sweep_results.csv --save-research-result research_memory/experiments.jsonl --write-sweep-report reports/sweep_report.md
```

## Sweep Sample Dataset

```bash
python -m tradeos.main --config config/paper.yaml --backtest-csv data/sample_multisymbol_bars.csv --sweep-entry-thresholds 0.001,0.002,0.003 --sweep-exit-thresholds 0.001,0.002,0.003 --append-backtest-csv results/sweep_results.csv --save-research-result research_memory/experiments.jsonl --write-sweep-report reports/sweep_report.md
```

## Current Architecture Summary

- Strategy implementations generate `Signal` objects only; they never submit orders or inspect broker state.
- RiskManager approves or blocks signals before any order intent is created.
- Reconciliation compares broker positions against local positions and blocks execution if state is dirty.
- BacktestRunner runs bars through the same TradingCycle used by dry-run flows.
- Research Agent converts backtest results into scored experiments and stores them in JSONL memory.

## Build Order

1. Define domain models, enums, and configuration schemas.
2. Add configuration loading and validation tests.
3. Build observability primitives for logs, events, and reports.
4. Implement storage interfaces and repository tests.
5. Add market data adapters behind explicit interfaces.
6. Build strategy interfaces and a test-only mean reversion placeholder.
7. Implement portfolio accounting and broker reconciliation.
8. Add risk rules as the final gate before execution.
9. Build execution interfaces, order lifecycle handling, and idempotent fill processing.
10. Create backtest/live parity tests before enabling any live behavior.
