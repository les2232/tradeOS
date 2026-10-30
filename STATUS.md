# TradeOS V3 — Project Status

_Last updated: 2026-10-30, after the safety audit, status lock, and paper/browser test
enablement. Re-run the "How to re-check" commands at the bottom to refresh._

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
| `main` | `6c9eef0` (PR #1 merge commit); local `main` fast-forwarded to match |
| PR #1 | **MERGED** 2026-10-30; its branch `feat/offline-browser-dashboard` deleted locally and on origin |
| Paper branch | `feat/paper-supervisor`, pushed, in sync with origin |
| Paper PR | **not opened yet**: waiting on a green GitHub CI run (see below) |
| Deployment | none. The dashboard is a private dev server and nothing is hosted. |

Commits on `feat/paper-supervisor` (on top of `6c9eef0`):

- `c8f1d67` feat: add browser paper-trading supervisor
- `138e2e6` refactor: retire desktop paper console in favor of browser console
- `86aa3e4` refactor: make CLI paper pass read-only
- `b794954` docs: update STATUS.md after branch split and console retirement
- `1aab9fb` test: cover unmanaged and short position gate on browser paper path
- `3be9512` fix: guard paper session status with lock
- `942bfad` ci: run paper and browser tests
- (this file) docs: update STATUS.md

The first backup push of this branch (`6f5e512`) was replaced on origin by a
`--force-with-lease` push after the rebase onto `main`; its content is in `c8f1d67`.

## Tests

| Where | Result |
|---|---|
| Local, Python 3.14.2 | **378 passed, 0 skipped** |
| Local, Python 3.11.18 (scratch venv) | **378 passed, 0 skipped** |
| Local, outbound network blocked, no Alpaca env vars | 378 passed; zero non-loopback connection attempts |
| GitHub CI (3.11 + 3.14) | **not run yet** for these commits (see "Blocker") |

Count history: 371 → 373 (+2 unmanaged/short gate tests) → 375 (+2 status lock tests)
→ 378 (the 3 formerly skipped tests now run).

Running locally needs `PLAYWRIGHT_BROWSERS_PATH` set, or the browser test skips
(see README "Automated Checks").

## Blocker: GitHub CI hasn't run on the paper commits

CI triggers on PRs to `main`, pushes to `main`, and manual dispatch. The token used
here cannot dispatch workflows (`HTTP 403: Resource not accessible by integration`),
so the new CI config (`.[dev,web,paper,browser]`, Playwright Chromium, fail on any skip)
has not run on GitHub. To unblock, either:

- run it manually: **Actions → CI → Run workflow → `feat/paper-supervisor`**, or
  `gh workflow run ci.yml --ref feat/paper-supervisor` with a token that has `actions: write`; or
- open the paper PR as a draft so the `pull_request` trigger runs it.

## Safety audit: browser paper submission path (2026-10-30)

Path: dashboard **Start** button (`dashboard.js`, `data-paper="start"`, requires the
confirm checkbox) → `POST /api/paper/start` (`app.py`: requires the
`X-TradeOS-Request: paper` header, JSON, and `confirm_paper_orders: true`) →
`PaperSupervisor.start()` → session thread `_run()` → `check(evaluate=True, submit=True)`
→ `self.broker.submit_order(intent)` (`paper.py`). Manual **Check** and **Evaluate**
call `check()` with `submit=False` and never submit.

It is its **own path**, not `PaperTradingLoop(allow_order_submission=True)`. It runs
`TradingCycle(execute_orders=False)` to get the signal and risk decision, then submits
itself after more gates. Nothing in the repo constructs `PaperTradingLoop` with
`allow_order_submission=True` any more. That loop's gates are now exercised only
by its own unit tests.

| Gate | Code | Tests | Mutation check |
|---|---|---|---|
| Open orders block submission | `paper.py` `check()`: `if self.broker.get_open_orders(): ... continue`, right before the claim and submit | `test_data_and_pending_gates[open_order]` | removing it fails a test ✅ |
| Unmanaged (outside watchlist) or short positions block | `paper.py` `_snapshot()`: `if any("short" in p.source or p.symbol not in self.config.symbols ...): raise` | `test_unmanaged_or_short_position_blocks[COP-…]`, `[AAPL-…short]` (**added in `1aab9fb`**) | was **untested** before (removing it passed all 371 tests); now fails 2 tests ✅ |
| Unexplained positions (broker vs. ledger) block | `paper.py` `_snapshot()`: `ReconciliationService().reconcile(...)`; `if not execution_allowed: raise` | `test_dirty_reconciliation_blocks` | removing it fails a test ✅ |

`_snapshot()` runs at the start of every check and again right before each submission.
Reconciliation also catches outside-watchlist positions, so that case is double-gated.

## Status lock (`3be9512`)

- A new `status_lock` guards every read and write of `status` and `active` in `_run()`,
  `stop()`, `view()`, `start()`, and `check()`.
- **Why not `self.lock`:** `check()` holds `self.lock` across all broker calls and calls
  `view()` while holding it. Taking it in `view()` or `stop()` would deadlock (non-reentrant
  `Lock`), and it would make Stop and dashboard polling wait on in-flight network calls.
- `status_lock` is held only for in-memory dict work: never across broker calls, sleeps,
  SQLite, or journal writes. It is always taken after `self.lock`, never before.
- **Real race fixed:** `stop()` read `active` and wrote `state` separately. A session
  ending in between left the console stuck on "stopping" with `active=False`.
- Tests:
  - `test_stop_racing_session_end_never_leaves_stopping` forces that interleaving.
    It fails 10/10 without the lock and passes 10/10 with it.
  - `test_concurrent_view_and_stop_during_session` hammers `view()`/`stop()` from 6 threads
    during a session. It is a deadlock/exception smoke test only: under the GIL, torn reads
    can't be forced reliably, and it passes even without the lock.

## Remaining before merging the paper PR

1. Green GitHub CI on 3.11 + 3.14 (blocked as above), then open the paper PR.

## After the paper PR

2. **First supervised paper session** on a dedicated, empty paper account: check,
   evaluate once, then a short session. Record the outcome here and download the
   journal. No session has run yet: there's no `data/` dir, no `paper.sqlite3`, and no
   Alpaca credentials in the environment.

## Risks

- **Live-mode paths:** none reachable, and none of this work touched them.
  `config/live.yaml` (`execution_mode: live`, `broker.paper: false`) exists but is dormant:
  - `_validate_paper_runtime` rejects it.
  - `AlpacaPaperBroker` raises on `paper=False` (both `__init__` and `from_env`).
  - The web research endpoint rejects `config/live.yaml` (tested).
  - No code acts on `ExecutionMode.LIVE`.
- **One paper-submission path:** the browser supervisor. The desktop console is retired and
  the CLI's `--paper-live-once` is read-only.
- **Never run against a real paper account.** Every paper test uses mocks or fakes.
- **Secrets:** none found. `tests/fixtures/paper_credentials.env` holds only the
  test placeholders `'file-key'` / `"file-secret"`. No `.env`, and no Alpaca
  variables in the environment.

## Planned but not started (longer term)

- Real-money live trading (intentionally out of scope)
- Background broker streaming; realized P&L reporting
- Strategy threshold / risk-hour tuning
- `config/strategy_mean_reversion.yaml` is a disabled placeholder

## How to re-check

```bash
git status && git log --oneline -5 && git worktree list && git stash list
gh pr list --state all && gh run list --limit 5
PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright python -m pytest -q -rs | tail -1
ls -la data/ 2>/dev/null   # paper ledger appears here after a first session
```
