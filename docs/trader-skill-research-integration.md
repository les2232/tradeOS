# Trader Skill Research Integration (Issue #3)

Status: **design only**. This branch is based on `main` and must not import broker execution from PR #2.

## Purpose

Adapt narrowly selected, read-only research functions from [Jakeschincariol/trader-skill](https://github.com/Jakeschincariol/trader-skill) into TradeOS V3. Initial focus: SEC company financial snapshots and deterministic rule screening. Keep the existing offline backtest and strategy/runtime code unchanged.

## Upstream and license

- Repository: `Jakeschincariol/trader-skill` (MIT)
- Relevant upstream files: `trader-analyst/facts.py`, `trader-screener/screen.py`, relevant `SKILL.md` files, their tests and fixtures.
- Before copying code, pin a reviewed upstream commit, inspect full source/tests/dependencies, and retain its MIT copyright and license notice in the derived code/package. Do **not** simply install all 11 skills.

## Phases

### A. Offline, pure research core (first implementation PR)
1. Define a versioned snapshot contract: source URL, ticker, filing period, retrieved-at UTC, source publication time when available, units, metrics and content SHA-256.
2. Validate and load local JSON test fixtures (with explicit size limits, duplicates, invalid numbers, missing fields and deterministic ordering).
3. Implement a pure screening function: validated rules, PASS/FAIL and reason per metric, no automatic treatment of screening as an order or recommendation.
4. Save deterministic reports, including provenance and input hash, without broker calls.
5. Add focused tests and the existing two-version CI matrix.

### B. Dashboard presentation (later PR)
Add read-only screening results to the browser research dashboard; no order actions or broker API endpoints.

### C. Optional explicit SEC acquisition (later PR)
Implement a manually initiated, isolated SEC downloader with appropriate user agent, rate limits, provenance and local snapshotting. Research evaluation must remain completely offline.

## Architecture constraints

```text
Manual SEC fetch (optional, separate)
                  |
                  v
          Saved, dated snapshot
                  |
                  v
        Validate + fingerprint
                  |
                  v
       Pure rules-based screener
                  |
                  v
      Read-only report/dashboard
```

Absolutely no dependencies on `trader-broker`, brokerage MCP connections, execution schedulers, Alpaca credentials, position flattening or order submission. Never expose a path from screening output to live strategy order intents as part of this feature.

## Security and safety checks

- Network-disabled fixtures and tests.
- Invalid, inconsistent, or missing required fields rejected, not guessed.
- Stale financial records identified with filing/as-of dates; stale thresholds belong to explicit rules.
- Fail-closed screening rules: missing required numeric metrics fail the rule.
- No broker credentials read, no Alpaca client constructed, and no order/cancel/flatten calls.
- Research-only workflow does not weaken any existing runtime guard.
- Avoid copy/pasting example trading policies as TradeOS execution policy; `trader-risk` treats live accounts as a warning, not a hard failure.

## Acceptance evidence

- Record upstream pinned commit, reviewed file list, license attribution.
- Record test command, results and TradeOS commit SHA in the PR.
- CI green for Python 3.11 and 3.14.
- PR review verifies the no-live/no-autonomous-orders boundary.
- Do not merge before all acceptance criteria pass.

Tracking: https://github.com/les2232/tradeOS/issues/3
