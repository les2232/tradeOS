# TradeOS V3 — Project Status

_Last updated: 2026-10-31, after the PR #2 review fixes. Re-run the "How to re-check"
commands at the bottom to refresh._

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
| `main` | `6c9eef0` (PR #1 merge commit) |
| PR #1 | **MERGED**; its branch `feat/offline-browser-dashboard` deleted locally and on origin |
| PR #2 | **OPEN**, ready for review: https://github.com/les2232/tradeOS/pull/2 (`feat/paper-supervisor` → `main`). Not merged. |
| Deployment | none. The dashboard is a private dev server and nothing is hosted. |

Commits on `feat/paper-supervisor` (on top of `6c9eef0`):

- `c8f1d67` feat: add browser paper-trading supervisor
- `138e2e6` refactor: retire desktop paper console in favor of browser console
- `86aa3e4` refactor: make CLI paper pass read-only
- `b794954` docs: update STATUS.md after branch split and console retirement
- `1aab9fb` test: cover unmanaged and short position gate on browser paper path
- `3be9512` fix: guard paper session status with lock
- `942bfad` ci: run paper and browser tests
- `d35d8c9` docs: update STATUS.md with safety audit, status lock, and test results
- Review fixes (see below): `e9d1ae5`, `a941273`, `12ff26a`, `f526db7`, `25ea26f`
- (this file) docs: rule 4, ledger lock scope, and review-fix status

## Tests and CI

| Where | Result |
|---|---|
| GitHub CI on `d35d8c9` (PR #2, run 41122537087) | **green**: 378 passed, 0 skipped on Python 3.11 and 3.14 |
| Local, Python 3.14.2, after the review fixes | **401 passed, 0 skipped** |
| GitHub CI after the review fixes | runs on the push of these commits; check `gh pr checks 2` |

Count history: 371 → 373 (unmanaged/short gate) → 375 (status lock) → 378 (formerly
skipped tests run) → 401 (review-fix regression and guard tests).

CI triggers on PRs to `main`, pushes to `main` and manual dispatch, installs
`.[dev,web,paper,browser]` plus Playwright Chromium, and fails on any skipped test.
Running locally needs `PLAYWRIGHT_BROWSERS_PATH` set, or the browser test skips.

## PR #2 review fixes (2026-10-31)

A review of `d35d8c9` found these in `tradeos/web/paper.py`. Every test below
was run against `d35d8c9` and against the fix.

| Fix | Change | Test(s) | On `d35d8c9` |
|---|---|---|---|
| 1. Start could erase a concurrent Stop | Each session gets its own stop `Event`, created in the same lock that marks it active; nothing is cleared afterwards | `test_stop_during_start_gap_prevents_submission` | fails: an order is submitted after Stop |
| 2. Daily-loss guard blocked protective exits | At the limit, only sells with qty ≤ reconciled long − pending sells pass | `test_daily_loss_allows_reducing_sell` (regression); `test_daily_loss_blocks_buy`, `test_daily_loss_blocks_sell_exceeding_available_long` (guards) | regression fails; guards pass there (everything was blocked) or need the new function |
| 3. Aborted attempt left `submission_uncertain` | Order lifecycle `claimed` → `not_sent` \| `sending` → `accepted` \| `submission_uncertain`; broker queried for all but `claimed`/`not_sent` | `test_stop_before_send_marks_not_sent_and_reconciles_clean`, `test_crash_left_sending_blocks_with_manual_resolution`, `test_send_error_stays_uncertain_and_blocks` | first two fail on behaviour; the third already blocked there and fails only on the new field |
| Rule 4 backing | `paper_broker_orders` table (broker id primary key, client id unique), written in the same transaction as the claim and JSON ledger | `test_duplicate_broker_order_id_blocks`, `test_broker_id_conflict_rolls_back_ledger_json` | both fail (no constraint) |
| 4. Config reloaded every evaluation | Snapshot frozen at Start, previewed with a fingerprint as "Automatic PAPER session", journaled; Start rejected if it changed | `test_session_uses_frozen_config_after_disk_change`, `test_start_rejects_settings_changed_since_preview`, `test_session_preview_route` | frozen-config test fails (session blocked on reloaded config); others need the new API |
| 5. No wall-clock cap | Max duration 1–390 min in the snapshot; deadline = earlier of cap and broker `next_close` (aware UTC); Start refused when closed or `next_close` naive | `test_session_ends_at_wall_clock_cap`, `test_session_deadline_compares_non_utc_next_close[Tokyo, New_York]`, `test_start_rejected_when_market_closed`, `test_start_rejects_naive_next_close` | market-closed and naive tests fail (Start succeeded); others: feature absent. Timezone test also fails if `next_close` is read as wall-clock time |

Stop still only prevents new submissions: an in-flight request may complete, and
nothing cancels orders or liquidates positions. Check and Evaluate stay read-only.

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

## Remaining before merging PR #2

1. Green GitHub CI on the review-fix commits (3.11 + 3.14).
2. Reviewer sign-off on the fixes above.

## After PR #2

- **First supervised paper session** on a dedicated, empty paper account: Check,
  Evaluate once, then a short session. Record the outcome here and download the journal.
  **The browser path has still never been run against a real Alpaca paper account.**
  Nothing here is verified against Alpaca; every paper test uses mocks.
- **Consolidate fill logic:** the browser supervisor applies fills itself
  (`PaperSupervisor._snapshot`), separately from `FillProcessor`. Merge the two in a
  later PR.

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
- **Ledger lock scope:** the process lock covers one ledger file, not the broker
  account. Two ledgers pointed at the same paper account aren't detected; keep one
  canonical ledger per account.
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
