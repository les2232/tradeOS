# TradeOS V3 — Project Status

_Last updated: 2026-10-30, after the branch split, PR #1 merge, and desktop console
retirement. Re-run the "How to re-check" commands at the bottom to refresh._

## What this is

TradeOS V3 is a personal, safety-first rebuild of an algorithmic stock-trading
system (AAPL/MSFT/NVDA, 15-minute bars, long-only mean-reversion and momentum
strategies). It is built for one operator, the repo owner, to research strategies
offline (synthetic or CSV backtests, parameter sweeps, scored research memory) and
then run tightly supervised **Alpaca PAPER** sessions. Real-money trading is
deliberately absent. `AGENTS.md` holds the non-negotiable rules: risk manager is
the final gate, broker state is truth, a dirty state blocks execution, and every
change needs tests.

## Git / GitHub state

| Item | State |
|---|---|
| `main` (origin) | `6c9eef0` merge commit for PR #1 (regular merge, not squash) |
| PR #1 "feat: add offline research dashboard" | **MERGED** 2026-10-30 |
| Paper branch | `feat/paper-supervisor`, rebased onto `origin/main`, pushed, in sync with origin |
| Paper PR | **not opened yet** (waits on the items below) |
| Deployment | none. The dashboard is a private dev server and nothing is hosted. |

Commits on `feat/paper-supervisor` (on top of `6c9eef0`):

- `c8f1d67` feat: add browser paper-trading supervisor
- `138e2e6` refactor: retire desktop paper console in favor of browser console
- `86aa3e4` refactor: make CLI paper pass read-only
- (this file) docs: update STATUS.md

**Force push note:** the branch was first pushed as a backup at `6f5e512` (the paper
commit before the rebase). Rebasing onto the merged `main` rewrote it, and the
`--force-with-lease` push replaced `6f5e512` on origin with the rebased history. The
content of `6f5e512` is unchanged in `c8f1d67`; only its parent differs.

## Done

| Item | Status | Evidence |
|---|---|---|
| Merge PR #1 | ✅ | merged with a merge commit; CI green on 3.11 + 3.14 beforehand |
| Split paper work onto `feat/paper-supervisor` | ✅ | committed, rebased cleanly onto `origin/main`, pushed |
| Delete the two `.patch` files | ✅ | both verified already applied before deletion (`git apply --check -R`: browser-dashboard patch at `a52bbae`; offline-startup patch at HEAD for every file except README, whose text landed in `a52bbae` and was later revised). They were untracked, so there was no commit. |
| Retire the desktop console | ✅ | `tradeos/dashboard/` (Tkinter app + empty placeholder packages), `tests/unit/dashboard/test_app.py` (21 tests), and its README section removed. No entry point and no non-stdlib dependency existed. |
| Browser console is the only paper order path | ✅ | `--submit-paper-orders` removed from the CLI. `--paper-live-once` is now read-only (see below). |

### Why `--paper-live-once` can't submit orders

`_run_paper_live_once` in `tradeos/main.py` hard-codes `TradingCycle(execute_orders=False)`
and `PaperTradingLoop(allow_order_submission=False)`. The only broker
`submit_order` call in the engine is in `TradingCycle.run` (`tradeos/engine/cycle.py`),
and it is behind `if not self.execute_orders: return ... "dry_run_order_not_submitted"`.
Covered by `test_paper_live_once_never_submits_orders_for_approved_buys`: an always-BUY
strategy gets 3 risk-approved intents, and a broker whose `submit_order` raises is never
called. Flipping `execute_orders` to `True` makes that test fail. Passing the old
flag exits with `unrecognized arguments: --submit-paper-orders`
(`test_submit_paper_orders_flag_is_rejected`).

## Remaining before opening the paper PR

1. **Status lock, with a test.** `tradeos/web/paper.py` changes `self.status` outside
   `self.lock` in `_run()` (L237, L244–245) and `stop()` (L251), and `view()` (L261)
   reads it without the lock. `_snapshot()` (L109) is fine because it is only called
   from `check()`, which holds the lock.
2. **Install `.[paper]` locally and in CI** so the 3 skipped tests run. `ci.yml` installs
   only `.[dev,web]` (L39). Skipped today:
   - `tests/unit/test_alpaca_market_data.py`: 2 SDK contract tests (`alpaca-py` not installed)
   - `tests/integration/test_paper_browser.py`: Playwright test (Playwright not installed;
     it also needs `PLAYWRIGHT_BROWSERS_PATH`)

Then open the paper PR.

## After the paper PR

3. **First supervised paper session** on a dedicated, empty paper account: check,
   evaluate once, then a short session. Record the outcome here and download the
   journal. No session has run yet: there's no `data/` dir, no `paper.sqlite3`, and no
   Alpaca credentials in the environment.

## Tests

`python -m pytest`: **371 passed, 3 skipped, 0 failed** (at `86aa3e4`).

How the count got there:
- 391 / 3 skipped after the paper commit (370 committed on PR #1 + 21 new)
- 370 after retiring the desktop console (−21 desktop console tests)
- 371 after making the CLI read-only: −2 submission tests in `test_paper_live_cli.py`
  (submit blocked by unmanaged positions; flag requires `--paper-live-once`), +2 new CLI
  tests (never submits; old flag rejected), +1 engine test that keeps coverage of
  `PaperTradingLoop`'s unmanaged-positions submission gate
  (`tests/unit/engine/test_trading_loop.py`)

## Risks

- **Live-mode paths:** none reachable, and none of this work touched them.
  `config/live.yaml` (`execution_mode: live`, `broker.paper: false`) exists but is dormant:
  - `_validate_paper_runtime` rejects it.
  - `AlpacaPaperBroker` raises on `paper=False` (both `__init__` and `from_env`).
  - The web research endpoint rejects `config/live.yaml` (tested).
  - No code acts on `ExecutionMode.LIVE`.
- **One paper-submission path.** The desktop console and the CLI submit flag are gone,
  so only the browser supervisor submits, through its `paper.sqlite3` ledger and order
  claims.
- **Unrun tests:** 2 Alpaca SDK contract tests + 1 browser test, locally and in CI.
- **Thread safety:** the session status dict is shared without a lock (see above).
- **Secrets:** none found. `tests/fixtures/paper_credentials.env` holds only the
  test placeholders `'file-key'` / `"file-secret"`. No `.env`, and no Alpaca
  variables in the environment.

## Housekeeping (optional)

- The local `main` branch is still at `3b386ce`; `git switch main && git pull` brings it
  up to `6c9eef0`.
- The merged `feat/offline-browser-dashboard` branch still exists locally and on origin.

## Planned but not started (longer term)

- Real-money live trading (intentionally out of scope)
- Background broker streaming; realized P&L reporting
- Strategy threshold / risk-hour tuning
- `config/strategy_mean_reversion.yaml` is a disabled placeholder

## How to re-check

```bash
git status && git log --oneline -5 && git worktree list && git stash list
gh pr list --state all && gh run list --limit 5
python -m pytest -o addopts="-p no:cacheprovider" -q | tail -1
ls -la data/ 2>/dev/null   # paper ledger appears here after a first session
```
