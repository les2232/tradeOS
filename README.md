# TradeOS V3

TradeOS V3 is a clean rebuild of the trading system focused on safety, reliability, testability, and live/backtest parity.

## Automated Checks

[![CI](https://github.com/les2232/tradeOS/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/les2232/tradeOS/actions/workflows/ci.yml)

Pull requests targeting `main`, pushes to `main`, and manual runs execute the
full offline/simulated test suite on Python 3.11 and 3.14. CI installs the web
and development dependencies, checks dependency consistency, builds a wheel,
and verifies the dashboard assets and shared sample generator are packaged.
Tests run without broker credentials or repository secrets.

## Browser dashboard (offline research)

From the repository root, install the optional web dependency and start:

```bash
python -m pip install -e ".[web]"
python -m tradeos.web.app --host 0.0.0.0 --port 8000
```

In Codespaces, open **Ports → 8000 → Open in Browser** and keep visibility
**Private**. If needed, add port 8000 in the Ports tab. After installation you
can also use **Terminal → Run Task → TradeOS: Start browser dashboard**.
Leave the server running while using the dashboard; restart it after the
Codespace stops. On your own machine, omit `--host` and open localhost:8000.

Click **Run backtest** to use 1,500 synthetic bars, or upload a validated CSV.
Adjust either strategy's thresholds, commission, and slippage; inspect simulated
P&L, closed trades, and risk reasons. Download Run JSON for exact values,
configuration and dataset SHA-256, or Trades CSV for closed trade details.
Runs are not saved to research memory: download before refreshing the page.
Displayed dollars are rounded; JSON preserves Decimal precision.

The dashboard invokes the existing BacktestRunner with FakeBroker and the
existing risk settings, with end-of-window flattening just like the CLI.
Threshold inputs are percentages: 0.5% maps to the engine's 0.005. The offline backtest endpoint never reads broker credentials or submits orders.
The separate PAPER console below has explicit submission controls. The synthetic
generator is shared with the existing CLI script. Its 15-minute series is
synthetic and does not model exchange sessions or market calendars.

CSV limits: 2 MB, 10,000 bars total, 3,000 per symbol. Symbols/timeframes must
match the selected server configuration (default AAPL, MSFT, NVDA / 15Min).
Uploaded files are processed temporarily and removed afterward. One run is
allowed at a time per server. This is a private development dashboard, not a
publicly deployed service; Flask debug mode is disabled. Charts show cumulative
simulated P&L rather than account equity. GUI interaction and API tests are
separate from connected broker integration tests.

## Offline verification

Fresh-checkout verification: offline CSV backtests and fake-broker commands are
supported. The original GitHub commit omitted `tradeos/data`, preventing CLI
startup. The offline CSV loader is now included, and `/data/` ignores generated
datasets without hiding Python source. CSV input is validated and sorted by
UTC timestamp and symbol; malformed and duplicate bars reject the dataset.

The Alpaca market-data adapter is now included and optional dependencies remain
lazy-loaded. Connected broker execution has not been validated with credentials.
Synthetic results demonstrate software behavior, not trading efficacy.

For a credential-free offline demo, install the project and generate synthetic
data, then run the existing backtest:

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/unit
python scripts/generate_sample_dataset.py
python -m tradeos.main --config config/paper.yaml --backtest-csv data/sample_multisymbol_bars.csv
```

TradeOS V3 now has safe offline contracts for configuration, domain models, strategy signals, risk gating, fake broker execution, reconciliation, event storage, backtesting, and deterministic research scoring.

## Safety Status

- Alpaca historical-data and paper-broker adapters are lazy-loaded only when requested.
- No real-money live trading command exists yet.
- A paper-safe live loop exists for one bounded pass through configured symbols.
- The browser paper supervisor supports bounded sessions; CLI mode remains one pass.
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

## Supervised browser PAPER workflow

```bash
cd /workspaces/tradeOS
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,web,paper]"
python -m pytest tests/unit
python -m tradeos.web.app --config config/paper.yaml --host 0.0.0.0 --port 8000
```

Before launching, set `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` to **paper-account**
credentials using Codespaces secrets, then restart the Codespace to expose them
to the server. Alternatively, enter them privately in your terminal using
`read -rsp "Paper API key: " ALPACA_API_KEY; echo` and
`read -rsp "Paper secret: " ALPACA_SECRET_KEY; echo`, then
`export ALPACA_API_KEY ALPACA_SECRET_KEY`. Never put secrets in source or chat.
No custom trading endpoint is accepted; the SDK uses its paper endpoint only.

Open Ports → 8000 → Open in Browser, keeping the port **Private**. This is a
single-process private development server, not a public authenticated service.
Start with a dedicated, flat paper account with no pending orders. Click
**Check connection / account**, then **Evaluate once · no orders**. Inspect the
strategy reasons, risk rejections and account details. Select an interval
(5–3600 seconds) and evaluation limit (1–100), check the PAPER authorization
box, then click **Start PAPER session**. Start is the explicit submission action.
The session runs its first evaluation immediately and stops on a blocking error.
Only one session/server may own the ledger; no session resumes after restart.

Data uses the configured watchlist, timeframe and IEX/SIP feed entitlement.
Only completed bars are evaluated. Freshness is measured from **bar close**,
using `data.max_bar_age_seconds`; with the default 15Min / 120-second setting,
most evaluations between closes safely report stale data. Start shortly after a
quarter-hour close, or deliberately configure a larger freshness allowance if
appropriate. Market-closed and missing-data states never submit orders. Risk
hours in the existing configuration are **UTC**, including its early 15:30
entry cutoff and 15:55 force-flatten rule. Review these settings and the default
mean-reversion thresholds before running; this milestone does not tune them.
Force-flatten only runs during an active evaluation with valid data: it is not a
guaranteed scheduled liquidation, and stopping leaves positions exposed.

**Stop session** prevents subsequent submissions and interrupts interval waits.
A request already in flight may complete. Pending DAY orders are not canceled
and positions are not liquidated. Check/manage both in the Alpaca paper console.
Stop does not wait for a stalled broker request; do not start another session
until the current one has ended. Use Check afterward to refresh broker fills.

`data/paper.sqlite3` persists cycle events, sessions, client-order claims,
order observations and reconciled position quantities. Download the persistent
PAPER journal to review across restarts. Keep this database with its account;
do not delete it to bypass a reconciliation block. The supervisor queries known
orders by client ID, applies cumulative filled quantities once, and compares
resulting local quantities to the broker before allowing execution. Partial
fills, rejected, canceled and expired orders remain visible. Existing positions
not explained by this ledger, manual account changes, account switches, or
unresolved submissions block trading. Resolve discrepancies in the paper
account and inspect the journal; automatic adoption/reset is intentionally absent.
A timed-out submission is never blindly retried. Pending orders anywhere in the
paper account block further submissions. There is at most one new order per
evaluation and one attempt per symbol/bar across restarts.

Account equity, buying power, broker unrealized P&L and equity change since prior
close are displayed. Realized P&L is explicitly unavailable; equity change is not
labeled realized profit. The existing RiskManager runs before submission; an
additional account-equity daily-loss guard includes realized losses. Journal
fills are cumulative observations per broker order ID, not additive executions.
Broker visibility refreshes during checks/evaluations, not by a background stream.

Automated tests use mocks; SDK contract tests additionally exercise installed
Alpaca request models without network calls. Credentials, feed entitlement,
real broker fills and broker-connected restart recovery remain unverified until
your first supervised paper session. No real-money execution is enabled here.
