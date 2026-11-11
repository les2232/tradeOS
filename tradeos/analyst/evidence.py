"""Deterministic evidence packages for the bot performance analyst.

An evidence package is a compact, JSON-safe summary of what the event log
proves about one paper session (or one symbol within it). Every fact carries
the event refs (``E<sequence>``) it was derived from, and every known gap in
the record is listed under ``limitations``. Explanations, human or model
written, may only cite refs that appear in the package.

Nothing here contacts a broker or a model; it only reads recorded events.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any

from tradeos.analyst.sessions import SequencedEvent, SessionWindow, ref, select_window


EVIDENCE_VERSION = 1
# Same threshold the research scorer uses before it trusts a result.
MIN_CLOSED_TRADES = 30
MAX_NOTABLE_EVENTS = 80
MAX_SYMBOL_DECISIONS = 20
PROTECTIVE_REASONS = {"force_flatten_at_triggered", "max_daily_loss_triggered", "stop_loss_triggered"}
EVALUATION_MARKERS = {"paper_evaluation", "paper_blocked"}
REDACTED_KEYS = {"account_id"}
# How to read terms that are easy to misinterpret. Shipped inside every
# package so a model reads them with the data.
GLOSSARY = {
    "decision": "The final strategy, risk and reconciliation result for one symbol in one evaluation.",
    "cycle_runs": "All recorded cycle runs, including re-checks made immediately before a submission.",
    "dry_run_order_not_submitted": (
        "The cycle created an order intent without sending it. In supervised paper sessions the "
        "supervisor sends approved intents separately; see paper_order entries for what was sent."
    ),
    "submission": (
        "claimed or not_sent: never reached the broker. sending or submission_uncertain: may have "
        "reached the broker and must be checked manually. accepted: the broker acknowledged it."
    ),
    "strategy_inputs": "Values the strategy computed for that bar. Thresholds and deviation_pct are fractions: 0.01 means 1%.",
    "realized_pnl_from_matched_fills": "FIFO-matched buy and sell fills within this scope only, before fees.",
    "ref": "Event sequence number in the TradeOS event store; cite it to support a statement.",
}
_REF_PATTERN = re.compile(r"^E\d+$")


@dataclass
class _Cycle:
    start: SequencedEvent
    evaluation: int
    symbol: str
    bar_timestamp: str | None
    signal: SequencedEvent | None = None
    risk: SequencedEvent | None = None
    reconciliation: SequencedEvent | None = None
    completed: SequencedEvent | None = None
    extra: list[SequencedEvent] = field(default_factory=list)

    @property
    def anchor(self) -> SequencedEvent:
        return self.signal or self.start

    def refs(self) -> list[str]:
        items = [self.start, self.reconciliation, self.signal, self.risk, *self.extra, self.completed]
        return [ref(item[0]) for item in items if item is not None]

    def payload(self, name: str) -> dict[str, Any]:
        item = getattr(self, name)
        return item[1].payload if item is not None else {}

    def decision(self) -> dict[str, Any]:
        signal, risk, completed = self.payload("signal"), self.payload("risk"), self.payload("completed")
        return {
            "ref": ref(self.anchor[0]),
            "refs": self.refs(),
            "at": self.anchor[1].timestamp,
            "symbol": self.symbol,
            "bar_timestamp": self.bar_timestamp,
            "action": signal.get("action"),
            "signal_reason": signal.get("reason"),
            "risk_status": risk.get("status"),
            "risk_reason": risk.get("reason"),
            "blocked_rules": list(risk.get("blocked_rules") or []),
            "reconciliation": self.payload("reconciliation").get("status"),
            "outcome": completed.get("reason"),
            "order_created": bool(completed.get("order_created", False)),
        }


def build_evidence(
    events: list[SequencedEvent],
    session_id: str | None = None,
    symbol: str | None = None,
    source_label: str = "event store",
) -> dict[str, Any]:
    """Build the evidence package for one session (default: the latest)."""

    window = select_window(events, session_id)
    symbol = symbol.upper() if symbol else None
    limitations: list[dict[str, Any]] = []

    cycles, incomplete = _cycles(window.events)
    has_markers = any(item[1].event_type in EVALUATION_MARKERS for item in window.events)
    decision_cycles = _decision_cycles(cycles, has_markers)
    decisions = [cycle.decision() for cycle in decision_cycles]
    orders = _orders(window, events)
    fills = _fills(window, orders)
    trades, open_lots, unmatched = _round_trips(fills)

    identity = _identity(window, limitations)
    package: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "source": {
            "label": source_label,
            "events_in_store": len(events),
            "store_last_ref": ref(events[-1][0]) if events else None,
            "window_first_ref": ref(window.events[0][0]) if window.events else None,
            "window_last_ref": ref(window.events[-1][0]) if window.events else None,
            "window_first_at": window.events[0][1].timestamp if window.events else None,
            "window_last_at": window.events[-1][1].timestamp if window.events else None,
        },
        "identity": identity,
        "focus_symbol": symbol,
        "metrics": _metrics(cycles, decisions, orders, fills, trades, open_lots),
        "per_symbol": _per_symbol(decisions, fills, trades),
        "trades": trades,
        "open_positions_from_session_fills": open_lots,
        "blocks": [
            {"ref": ref(seq), "at": event.timestamp, "reason": event.payload.get("reason")}
            for seq, event in window.events
            if event.event_type == "paper_blocked"
        ],
        "notable_events": [],
        "limitations": limitations,
        "glossary": GLOSSARY,
    }

    notable =_notable(window, decisions, orders)
    if len(notable) > MAX_NOTABLE_EVENTS:
        package["notable_events_omitted"] = len(notable) - MAX_NOTABLE_EVENTS
        notable = notable[-MAX_NOTABLE_EVENTS:]
    package["notable_events"] = notable

    if symbol:
        package["symbol_detail"] = _symbol_detail(symbol, decision_cycles, fills, trades, identity)

    _add_limitations(
        limitations, window, cycles, decision_cycles, incomplete, orders, trades, unmatched, symbol, identity
    )
    return package


def collect_refs(value: Any) -> set[str]:
    """Every event ref that appears anywhere in a package."""

    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= collect_refs(item)
    elif isinstance(value, list):
        for item in value:
            found |= collect_refs(item)
    elif isinstance(value, str) and _REF_PATTERN.match(value):
        found.add(value)
    return found


def _cycles(window_events: list[SequencedEvent]) -> tuple[list[_Cycle], list[_Cycle]]:
    cycles: list[_Cycle] = []
    incomplete: list[_Cycle] = []
    current: _Cycle | None = None
    evaluation = 0
    for item in window_events:
        event = item[1]
        kind = event.event_type
        if kind in EVALUATION_MARKERS:
            evaluation += 1
        if kind == "cycle_started":
            if current is not None:
                incomplete.append(current)
            current = _Cycle(
                start=item,
                evaluation=evaluation,
                symbol=event.symbol,
                bar_timestamp=event.payload.get("bar_timestamp"),
            )
            continue
        if current is None:
            continue
        if kind == "signal_generated":
            current.signal = item
        elif kind == "risk_decision":
            current.risk = item
        elif kind == "reconciliation_result":
            current.reconciliation = item
        elif kind in {"order_intent_created", "order_submitted", "fill_received"}:
            current.extra.append(item)
        elif kind == "cycle_completed":
            current.completed = item
            cycles.append(current)
            current = None
    if current is not None:
        incomplete.append(current)
    return cycles, incomplete


def _decision_cycles(cycles: list[_Cycle], has_markers: bool) -> list[_Cycle]:
    """One decision per symbol per evaluation; the last run is the effective one.

    The paper supervisor re-runs a cycle against the refreshed account right
    before submitting, so an evaluation can hold two runs for one symbol.
    """

    if not has_markers:
        return list(cycles)
    latest: dict[tuple[int, str], _Cycle] = {}
    for cycle in cycles:
        latest[(cycle.evaluation, cycle.symbol)] = cycle
    return sorted(latest.values(), key=lambda cycle: cycle.start[0])


def _orders(window: SessionWindow, all_events: list[SequencedEvent]) -> list[dict[str, Any]]:
    """Paper orders attempted in the window, with their latest broker observation.

    Fills keep being observed at later checks, so observations are taken from
    the whole log, not only the window.
    """

    orders: dict[str, dict[str, Any]] = {}
    for seq, event in window.events:
        payload = event.payload
        client_id = payload.get("client_order_id")
        if event.event_type == "paper_submission_attempt" and client_id:
            orders[client_id] = {
                "client_order_id": client_id,
                "symbol": payload.get("symbol") or event.symbol,
                "side": _side(payload.get("side")),
                "attempt_ref": ref(seq),
                "attempted_at": event.timestamp,
                "submission": "claimed",
                "status": None,
                "requested_quantity": None,
                "filled_qty": "0",
                "filled_avg_price": None,
                "response_ref": None,
                "observed_ref": None,
                "observed_after_window": False,
            }
        elif event.event_type == "paper_submission_not_sent" and client_id in orders:
            orders[client_id].update(submission="not_sent", response_ref=ref(seq))

    # Responses carry only the broker id; match them to the preceding attempt.
    pending: list[str] = []
    for seq, event in window.events:
        if event.event_type == "paper_submission_attempt" and event.payload.get("client_order_id") in orders:
            pending.append(event.payload["client_order_id"])
        elif event.event_type == "paper_submission_response" and pending:
            order = orders[pending.pop()]
            order.update(
                submission="accepted",
                status=_text(event.payload.get("status")),
                requested_quantity=_text(event.payload.get("requested_quantity")),
                broker_order_id=_text(event.payload.get("broker_order_id")),
                response_ref=ref(seq),
            )
        elif event.event_type == "paper_submission_not_sent":
            pending = [item for item in pending if item != event.payload.get("client_order_id")]

    window_last = window.events[-1][0] if window.events else 0
    for seq, event in all_events:
        if event.event_type != "paper_order_observed":
            continue
        order = orders.get(event.payload.get("client_order_id"))
        if order is None:
            continue
        payload = event.payload
        order.update(
            submission=payload.get("submission", order["submission"]),
            status=_text(payload.get("status")),
            filled_qty=_text(payload.get("filled_qty")) or "0",
            filled_avg_price=_price_text(payload.get("filled_avg_price")),
            broker_order_id=_text(payload.get("broker_order_id")),
            observed_ref=ref(seq),
            observed_after_window=seq > window_last,
        )
    return sorted(orders.values(), key=lambda order: int(order["attempt_ref"][1:]))


def _fills(window: SessionWindow, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    for order in orders:
        quantity = _decimal(order["filled_qty"])
        price = _decimal(order["filled_avg_price"])
        if quantity and quantity > 0 and price is not None:
            fills.append({
                "ref": order["observed_ref"],
                "order": order["attempt_ref"],
                "symbol": order["symbol"],
                "side": order["side"],
                "quantity": str(quantity),
                "price": str(price),
                "status": order["status"],
                "source": "paper_order_observed",
            })
    # Fills reported directly by a cycle that executed its own order.
    for seq, event in window.events:
        if event.event_type == "fill_received":
            payload = event.payload
            fills.append({
                "ref": ref(seq),
                "order": ref(seq),
                "symbol": event.symbol,
                "side": _side(payload.get("side")),
                "quantity": _text(payload.get("quantity")),
                "price": _text(payload.get("fill_price")),
                "status": _text(payload.get("order_status")),
                "source": "fill_received",
            })
    return sorted(fills, key=lambda fill: int(fill["order"][1:]))


def _round_trips(fills: list[dict[str, Any]]):
    """FIFO-match sells against earlier buys from the same window.

    Sells with no matching buy in the window (positions opened earlier) are
    reported as unmatched instead of guessed.
    """

    lots: dict[str, list[list[Any]]] = defaultdict(list)
    trades: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for fill in fills:
        quantity, price = Decimal(fill["quantity"]), Decimal(fill["price"])
        symbol = fill["symbol"]
        if fill["side"] == "buy":
            lots[symbol].append([quantity, price, fill["ref"]])
            continue
        remaining, cost, entry_refs = quantity, Decimal("0"), []
        while remaining > 0 and lots[symbol]:
            lot = lots[symbol][0]
            matched = min(remaining, lot[0])
            cost += matched * lot[1]
            entry_refs.append(lot[2])
            lot[0] -= matched
            remaining -= matched
            if lot[0] == 0:
                lots[symbol].pop(0)
        closed = quantity - remaining
        if closed > 0:
            pnl = closed * price - cost
            trades.append({
                "symbol": symbol,
                "quantity": str(closed),
                "entry_avg_price": str((cost / closed).quantize(Decimal("0.0001"))),
                "exit_price": str(price),
                "pnl": str(pnl.quantize(Decimal("0.01"))),
                "entry_refs": entry_refs,
                "exit_ref": fill["ref"],
            })
        if remaining > 0:
            unmatched.append({"symbol": symbol, "quantity": str(remaining), "ref": fill["ref"]})
    open_lots = [
        {"symbol": symbol, "quantity": str(lot[0]), "price": str(lot[1]), "ref": lot[2]}
        for symbol, items in lots.items()
        for lot in items
        if lot[0] > 0
    ]
    return trades, open_lots, unmatched


def _metrics(cycles, decisions, orders, fills, trades, open_lots) -> dict[str, Any]:
    pnls = [Decimal(trade["pnl"]) for trade in trades]
    wins = sum(1 for pnl in pnls if pnl > 0)
    submissions = Counter(order["submission"] for order in orders)
    return {
        "cycle_runs": len(cycles),
        "decisions": len(decisions),
        "distinct_bars_evaluated": (
            len({(item["symbol"], item["bar_timestamp"]) for item in decisions})
            if decisions and all(item["bar_timestamp"] for item in decisions)
            else None
        ),
        "signals": _counts(item["action"] for item in decisions),
        "signal_reasons": _counts(item["signal_reason"] for item in decisions),
        "risk": _counts(item["risk_status"] for item in decisions),
        "risk_reasons": _counts(item["risk_reason"] for item in decisions if item["risk_status"] == "blocked"),
        "blocked_rules": _counts(rule for item in decisions for rule in item["blocked_rules"]),
        "reconciliation_dirty_decisions": sum(1 for item in decisions if item["reconciliation"] == "dirty"),
        "order_intents": sum(1 for item in decisions if item["order_created"]),
        "paper_submission_attempts": len(orders),
        "paper_submissions": dict(sorted(submissions.items())),
        "paper_order_statuses": _counts(order["status"] for order in orders if order["status"]),
        "fills": len(fills),
        "closed_trades": len(trades),
        "winning_trades": wins,
        "win_rate": str((Decimal(wins) / len(pnls)).quantize(Decimal("0.0001"))) if pnls else None,
        "realized_pnl_from_matched_fills": str(sum(pnls, Decimal("0")).quantize(Decimal("0.01"))) if pnls else None,
        "expectancy_per_closed_trade": (
            str((sum(pnls, Decimal("0")) / len(pnls)).quantize(Decimal("0.01"))) if pnls else None
        ),
        "open_lots_from_session_fills": len(open_lots),
    }


def _per_symbol(decisions, fills, trades) -> dict[str, Any]:
    symbols = sorted({item["symbol"] for item in decisions} | {fill["symbol"] for fill in fills})
    result = {}
    for symbol in symbols:
        own = [item for item in decisions if item["symbol"] == symbol]
        pnls = [Decimal(trade["pnl"]) for trade in trades if trade["symbol"] == symbol]
        result[symbol] = {
            "decisions": len(own),
            "signals": _counts(item["action"] for item in own),
            "top_signal_reasons": dict(Counter(item["signal_reason"] for item in own if item["signal_reason"]).most_common(3)),
            "risk_blocked": sum(1 for item in own if item["risk_status"] == "blocked"),
            "fills": sum(1 for fill in fills if fill["symbol"] == symbol),
            "closed_trades": len(pnls),
            "realized_pnl_from_matched_fills": str(sum(pnls, Decimal("0")).quantize(Decimal("0.01"))) if pnls else None,
        }
    return result


def _symbol_detail(symbol, decision_cycles, fills, trades, identity) -> dict[str, Any]:
    own = [cycle for cycle in decision_cycles if cycle.symbol == symbol]
    recent = own[-MAX_SYMBOL_DECISIONS:]
    rows = []
    for cycle in recent:
        row = cycle.decision()
        row["strategy_inputs"] = dict(cycle.payload("signal").get("metadata") or {})
        rows.append(row)
    watchlist = (identity.get("config") or {}).get("watchlist")
    return {
        "symbol": symbol,
        "in_watchlist": None if watchlist is None else symbol in watchlist,
        "decisions_total": len(own),
        "decisions_shown": len(rows),
        "recent_decisions": rows,
        "fills": [fill for fill in fills if fill["symbol"] == symbol],
        "trades": [trade for trade in trades if trade["symbol"] == symbol],
    }


def _notable(window: SessionWindow, decisions, orders) -> list[dict[str, Any]]:
    notable: list[tuple[int, dict[str, Any]]] = []
    for item in decisions:
        if item["action"] in {"buy", "sell"} or item["reconciliation"] == "dirty" or item["signal_reason"] in PROTECTIVE_REASONS:
            kind = "protective_signal" if item["signal_reason"] in PROTECTIVE_REASONS else "trade_signal"
            if item["reconciliation"] == "dirty":
                kind = "reconciliation_dirty"
            notable.append((int(item["ref"][1:]), {
                "ref": item["ref"], "type": kind, "at": item["at"], "symbol": item["symbol"],
                "detail": {key: item[key] for key in ("action", "signal_reason", "risk_status", "risk_reason", "outcome")},
            }))
    for order in orders:
        notable.append((int(order["attempt_ref"][1:]), {
            "ref": order["attempt_ref"], "type": "paper_order", "at": order["attempted_at"], "symbol": order["symbol"],
            "detail": {key: order[key] for key in ("side", "submission", "status", "filled_qty", "filled_avg_price", "response_ref", "observed_ref")},
        }))
    for seq, event in window.events:
        if event.event_type == "paper_blocked":
            notable.append((seq, {"ref": ref(seq), "type": "blocked", "at": event.timestamp, "symbol": None,
                                  "detail": {"reason": event.payload.get("reason")}}))
        elif event.event_type in {"paper_session_started", "paper_session_ended"}:
            payload = event.payload
            detail = (
                {"deadline": payload.get("deadline"), "deadline_reason": payload.get("deadline_reason")}
                if event.event_type == "paper_session_started"
                else {key: payload.get(key) for key in ("state", "ended_reason", "completed", "limit", "error")}
            )
            notable.append((seq, {"ref": ref(seq), "type": event.event_type, "at": event.timestamp, "symbol": None, "detail": detail}))
    return [item for _, item in sorted(notable, key=lambda pair: pair[0])]


def _identity(window: SessionWindow, limitations: list[dict[str, Any]]) -> dict[str, Any]:
    if not window.is_session:
        return {
            "scope": "unsessioned",
            "session_id": None,
            "config": None,
            "config_fingerprint": None,
            "config_fingerprint_verified": None,
        }
    start_seq, start = window.start_event
    payload = start.payload
    snapshot = payload.get("session") or {}
    stored = payload.get("fingerprint")
    # Mirrors tradeos.web.paper.fingerprint without importing the broker stack.
    recomputed = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest() if snapshot else None
    verified = None if not (stored and recomputed) else stored == recomputed
    if verified is False:
        limitations.append({
            "code": "config_fingerprint_mismatch",
            "message": "The recorded session settings do not match the fingerprint the operator confirmed.",
            "refs": [ref(start_seq)],
        })
    end_payload = window.end_event[1].payload if window.end_event else {}
    return {
        "scope": "session",
        "session_id": window.session_id,
        "started_at": start.timestamp,
        "started_ref": ref(start_seq),
        "ended_at": window.end_event[1].timestamp if window.end_event else None,
        "ended_ref": ref(window.end_event[0]) if window.end_event else None,
        "end_state": end_payload.get("state"),
        "ended_reason": end_payload.get("ended_reason"),
        "evaluations_completed": end_payload.get("completed"),
        "deadline": payload.get("deadline"),
        "deadline_reason": payload.get("deadline_reason"),
        "config": _redact(snapshot),
        "config_fingerprint": stored,
        "config_fingerprint_verified": verified,
    }


def _add_limitations(limitations, window, cycles, decision_cycles, incomplete, orders, trades, unmatched, symbol, identity):
    def add(code, message, refs=None):
        entry = {"code": code, "message": message}
        if refs:
            entry["refs"] = refs[:10]
        limitations.append(entry)

    if not window.events:
        add("no_events", "No events were recorded for this scope.")
    if identity["scope"] == "unsessioned":
        add("no_session_markers", "These events are not inside a supervised paper session, so there is no frozen session configuration to verify.")
    if window.is_session and window.end_event is None:
        add(
            "session_not_ended",
            "No end event was recorded: the session is still running, or the server stopped before it could record the end.",
            [ref(window.start_event[0])],
        )
    if incomplete:
        add("incomplete_cycles", f"{len(incomplete)} cycle(s) started without a completion event.", [ref(c.start[0]) for c in incomplete])
    rechecks = len(cycles) - len(decision_cycles)
    if rechecks > 0:
        add("pre_submission_rechecks", f"{rechecks} extra cycle run(s) re-checked a decision before submission; counts use the final run per symbol per evaluation.")
    if cycles and any(cycle.bar_timestamp is None for cycle in cycles):
        add("missing_bar_timestamps", "Some cycles were recorded without the bar timestamp, so decisions cannot all be tied to a specific bar.")
    if symbol and any(not (cycle.payload("signal").get("metadata")) for cycle in decision_cycles if cycle.symbol == symbol):
        add("missing_signal_inputs", "Some decisions were recorded without the strategy's inputs (SMA, deviation, thresholds), so the exact reason values are unavailable for them.")
    uncertain = [order for order in orders if order["submission"] in {"sending", "submission_uncertain"}]
    if uncertain:
        add("uncertain_submissions", f"{len(uncertain)} paper order(s) have an unconfirmed submission state; check them in the Alpaca paper console.", [o["attempt_ref"] for o in uncertain])
    unobserved = [order for order in orders if order["submission"] == "accepted" and order["observed_ref"] is None]
    if unobserved:
        add("orders_not_yet_observed", f"{len(unobserved)} accepted order(s) have no later broker observation, so their fills are unknown.", [o["attempt_ref"] for o in unobserved])
    if unmatched:
        add("unmatched_sells", "Some sells close positions opened before this scope; their profit and loss is not computed.", [item["ref"] for item in unmatched])
    if orders or trades:
        add("pnl_excludes_fees", "Profit and loss is computed from matched fill prices only and excludes fees and unrealized changes.")
    if len(trades) < MIN_CLOSED_TRADES:
        add("small_sample", f"Only {len(trades)} closed trade(s); at least {MIN_CLOSED_TRADES} are needed before results say anything about the strategy's edge.")


def _counts(values) -> dict[str, int]:
    return dict(sorted(Counter(value for value in values if value is not None).items()))


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("redacted" if key in REDACTED_KEYS else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _side(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    text = text.lower()
    return text.split(".")[-1]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _price_text(value: Any) -> str | None:
    number = _decimal(value)
    return None if number is None else str(number)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", "None"):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None
