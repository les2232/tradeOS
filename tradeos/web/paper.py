"""Bounded, opt-in paper supervision. No session resumes after process restart."""
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from uuid import uuid4
from zoneinfo import ZoneInfo

from tradeos.config.loader import load_config
from tradeos.data.alpaca_market_data import AlpacaLatestBarFeed, duration
from tradeos.domain.models import Position
from tradeos.engine.cycle import TradingCycle
from tradeos.engine.trading_loop import _required_history_count, _validate_paper_runtime
from tradeos.execution.broker import AlpacaPaperBroker
from tradeos.execution.reconciliation import ReconciliationService
from tradeos.risk.risk_manager import RiskManager
from tradeos.storage.sqlite import SQLiteEventStore
from tradeos.strategy.mean_reversion import MeanReversionStrategy
from tradeos.strategy.momentum_breakout import MomentumBreakoutStrategy


# Local submission lifecycle: claimed -> not_sent | sending -> accepted | submission_uncertain.
# claimed/not_sent never reached the broker because "sending" is persisted before the call.
NEVER_SENT = {"claimed", "not_sent"}


def enum_value(value):
    return str(getattr(value, "value", value))


def reduces_exposure(intent, long_qty, pending_sell_qty):
    """True only for a sell that shrinks an existing long net of pending sells, so it can never go short."""
    return enum_value(intent.side) == "sell" and Decimal(0) < intent.quantity <= long_qty - pending_sell_qty


def serial(value):
    return json.loads(json.dumps(value, default=lambda v: asdict(v) if hasattr(v, "__dataclass_fields__") else str(v)))


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


class PaperSupervisor:
    def __init__(self, config_path, db_path="data/paper.sqlite3", factory=None):
        self.config_path, self.path, self.factory = config_path, Path(db_path), factory
        # lock serializes checks/sessions and is held across broker calls. status_lock guards
        # status/active/session only and is never held across I/O or while acquiring lock.
        self.lock, self.status_lock = Lock(), Lock()
        # Each session owns its stop event, created when it is activated and never cleared.
        self.active, self.session = False, None
        self.thread = None
        self.status = {"mode": "ALPACA PAPER ONLY", "state": "idle", "decisions": [], "realized_pnl": None}
        self.broker = self.feed = None
        self.process_lock = None

    def _connect(self, config=None):
        # A session passes its frozen config; manual checks read the file each time.
        config = config or load_config(self.config_path)
        _validate_paper_runtime(config, True)
        if config.risk.allow_shorting:
            raise ValueError("Supervised paper workflow requires allow_shorting: false")
        if self.process_lock is None:
            import fcntl
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(str(self.path) + ".lock", "a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                raise ValueError("Another paper server owns this ledger")
            self.process_lock = handle
        self.events = SQLiteEventStore(self.path)
        self.events.initialize()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS paper_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_broker_orders (broker_order_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE)")
        if self.broker is None:
            if self.factory:
                self.broker, self.feed = self.factory(config)
            else:
                self.broker = AlpacaPaperBroker.from_env(paper=True)
                self.feed = AlpacaLatestBarFeed.from_env(feed=config.data.feed)
        account_id = str(self.broker.client.get_account().id)
        previous = self._get("account_id")
        if previous and previous != account_id:
            raise ValueError("Paper ledger belongs to another account; use a separate ledger")
        self._put("account_id", account_id)
        self.config = config

    def _put(self, key, value):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO paper_state VALUES (?,?)", (key, json.dumps(serial(value))))

    def _save_ledger(self, ledger, claim=None):
        # The claim, JSON ledger and broker order id index commit together or not at all.
        with sqlite3.connect(self.path) as db:
            if claim:
                db.execute("INSERT OR REPLACE INTO paper_state VALUES (?,?)", ("claim:" + claim, "true"))
            db.execute("INSERT OR REPLACE INTO paper_state VALUES (?,?)", ("reconciled", json.dumps(serial(ledger))))
            for client_id, order in ledger["orders"].items():
                broker_id = order.get("broker_order_id")
                if not broker_id:
                    continue
                db.execute("INSERT OR IGNORE INTO paper_broker_orders VALUES (?,?)", (broker_id, client_id))
                if not db.execute("SELECT 1 FROM paper_broker_orders WHERE broker_order_id=? AND client_order_id=?", (broker_id, client_id)).fetchone():
                    raise ValueError(f"Broker order id {broker_id} for {client_id} conflicts with another recorded order; resolve manually before continuing")

    def _set(self, target, **fields):
        with self.status_lock:
            target.update(fields)

    def _snapshot(self):
        raw = self.broker.client.get_account()
        if getattr(raw, "trading_blocked", False) or getattr(raw, "account_blocked", False) or enum_value(getattr(raw, "status", "ACTIVE")) != "ACTIVE":
            raise ValueError("Paper account is blocked or inactive")
        account = self.broker.get_account()
        positions = account.positions
        if any("short" in p.source or p.symbol not in self.config.symbols for p in positions):
            raise ValueError("Unmanaged or short broker positions block execution")
        expected = self._get("positions", {})
        orders = self._get("orders", {})
        for client_id, order in orders.items():
            # Records from before lifecycle tracking are treated as uncertain, as they were then.
            state = order.get("submission", "submission_uncertain")
            if state in NEVER_SENT:
                continue
            try:
                response = self.broker.client.get_order_by_client_id(client_id)
            except Exception:
                raise ValueError(f"Paper order {client_id} is {state} and the broker could not confirm it; resolve it manually in the Alpaca paper console before continuing") from None
            qty = Decimal(str(response.filled_qty or 0))
            prior = Decimal(order.get("filled_qty", "0"))
            if qty < prior:
                raise ValueError("Broker cumulative fills decreased")
            delta = qty - prior
            symbol = order["symbol"]
            expected[symbol] = str(Decimal(expected.get(symbol, "0")) + (delta if order["side"] == "buy" else -delta))
            order.update(submission="accepted", broker_order_id=str(response.id), filled_qty=str(qty), filled_avg_price=str(response.filled_avg_price), status=enum_value(response.status))
            self.events.append_event("paper_order_observed", symbol, dict(order, client_order_id=client_id))
        # Persist quantities and cumulative fills together, preventing double application.
        self._save_ledger({"positions": expected, "orders": orders})
        local = [Position(s, Decimal(q), Decimal(0), Decimal(0), Decimal(0), "paper_ledger") for s, q in expected.items()]
        reconciliation = ReconciliationService().reconcile(positions, local)
        if not reconciliation.execution_allowed:
            raise ValueError("Broker/local state dirty; inspect paper account and ledger before continuing")
        unrealized = sum((p.unrealized_pl for p in positions), Decimal(0))
        daily = account.equity - Decimal(str(raw.last_equity))
        self._set(self.status, account=serial(account), orders=list(orders.values()), fills=[o for o in orders.values() if Decimal(o.get("filled_qty", "0")) > 0], positions=serial(positions), unrealized_pnl=str(unrealized), daily_equity_change=str(daily), realized_pnl=None, reconciliation="clean", strategy=str(self.config.strategy.strategy_mode), watchlist=self.config.symbols)
        return account, local, daily

    def _get(self, key, default=None):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT value FROM paper_state WHERE key=?", ("reconciled" if key in {"positions", "orders"} else key,)).fetchone()
        value = json.loads(row[0]) if row else None
        return value.get(key, default) if key in {"positions", "orders"} and value else (default if value is None else value)

    def check(self, evaluate=False, submit=False, session=None):
        stop = session["stop"] if session else Event()
        with self.lock:
            try:
                self._connect(session["config"] if session else None)
                account, local, daily = self._snapshot()
                clock = self.broker.client.get_clock()
                self._set(self.status, connection="connected", market="open" if clock.is_open else "closed", checked_at=datetime.now(UTC).isoformat())
                if not evaluate:
                    return self.view()
                if not clock.is_open:
                    self._set(self.status, data="market_closed", decisions=[])
                    return self.view()
                bars = self.feed.recent_bars(self.config.symbols, self.config.timeframe, _required_history_count(self.config) + 2)
                now = self._now()
                latest = {}
                seen = set()
                for bar in sorted(bars, key=lambda b: (b.timestamp, b.symbol)):
                    key = (bar.symbol, bar.timestamp)
                    if key in seen or bar.data_quality != "ok" or bar.timestamp.utcoffset() is None:
                        raise ValueError("Invalid or duplicate market bars")
                    seen.add(key)
                    if bar.symbol not in self.config.symbols or bar.timeframe != self.config.timeframe:
                        raise ValueError("Unexpected market data symbol/timeframe")
                    latest[bar.symbol] = max(latest.get(bar.symbol, bar), bar, key=lambda b: b.timestamp)
                if set(latest) != set(self.config.symbols):
                    self._set(self.status, data="missing_data")
                    raise ValueError("Missing watchlist market data")
                ages = {s: (now - b.timestamp - duration(b.timeframe)).total_seconds() for s, b in latest.items()}
                self._set(self.status, freshness_seconds_after_bar_close=ages)
                if any(age < 0 or age > self.config.data.max_bar_age_seconds for age in ages.values()):
                    self._set(self.status, data="stale_data")
                    raise ValueError("Stale or future market data blocks all orders")
                if self.config.data.require_same_session and any(b.timestamp.astimezone(ZoneInfo("America/New_York")).date() != now.astimezone(ZoneInfo("America/New_York")).date() for b in latest.values()):
                    raise ValueError("Market data is from another session")
                self._set(self.status, data="fresh", decisions=[])
                strategy = MeanReversionStrategy() if self.config.strategy.strategy_mode == "mean_reversion" else MomentumBreakoutStrategy()
                submitted_this_evaluation = False
                for symbol, bar in latest.items():
                    cycle = TradingCycle(strategy, RiskManager(), self.broker, ReconciliationService(), execute_orders=False, event_store=self.events)
                    result = cycle.run(bar, self.config.strategy, self.config.risk, local, history=[b for b in bars if b.symbol == symbol and b.timestamp < bar.timestamp])
                    decision = serial(result)
                    with self.status_lock:
                        self.status["decisions"].append(decision)
                    if not submit or not result.order_intent or not result.execution_allowed or submitted_this_evaluation:
                        continue
                    if stop.is_set():
                        break
                    _, current_local, daily = self._snapshot()
                    if (self._now() - bar.timestamp - duration(bar.timeframe)).total_seconds() > self.config.data.max_bar_age_seconds:
                        raise ValueError("Data became stale before submission")
                    if not self.broker.client.get_clock().is_open:
                        raise ValueError("Market closed before submission")
                    # Refresh risk against the current broker account immediately before submission.
                    result = cycle.run(bar, self.config.strategy, self.config.risk, current_local, history=[b for b in bars if b.symbol == symbol and b.timestamp < bar.timestamp])
                    if not result.execution_allowed or not result.order_intent:
                        self._set(decision, **serial(result))
                        continue
                    open_orders = self.broker.get_open_orders()
                    if open_orders:
                        self._set(decision, reason="Pending orders block further submissions")
                        continue
                    # At the loss limit only validated exposure-reducing sells may still go out.
                    long_qty = sum((p.quantity for p in current_local if p.symbol == symbol), Decimal(0))
                    pending_sells = sum((o.quantity for o in open_orders if o.symbol == symbol and enum_value(o.side) == "sell"), Decimal(0))
                    if daily <= -self.config.risk.max_daily_loss and not reduces_exposure(result.order_intent, long_qty, pending_sells):
                        self._set(decision, reason="Daily account equity loss limit blocks submission; only exposure-reducing sells are allowed")
                        continue
                    claim = hashlib.sha256(f"{symbol}:{bar.timestamp.isoformat()}:{bar.timeframe}".encode()).hexdigest()[:32]
                    if self._get("claim:" + claim):
                        self._set(decision, reason="Duplicate bar: order already attempted")
                        continue
                    client_id = "tos-" + claim
                    order = {"symbol": symbol, "side": str(result.order_intent.side), "submission": "claimed", "status": "not_submitted", "filled_qty": "0", "client_order_id": client_id}
                    ledger = self._get("reconciled", {"positions": {}, "orders": {}})
                    ledger["orders"][client_id] = order
                    self._save_ledger(ledger, claim=claim)
                    self.events.append_event("paper_submission_attempt", symbol, order)
                    # Claim is durable before network access and stays consumed in every outcome:
                    # a bar is attempted at most once and exceptions are never retried.
                    intent = replace(result.order_intent, metadata=dict(result.order_intent.metadata, client_order_id=client_id))
                    abort = "Session stopped" if stop.is_set() else "Session reached its end time" if session and self._now() >= session["deadline"] else None
                    if abort:
                        order["submission"] = "not_sent"
                        self._save_ledger(ledger)
                        self._set(decision, reason=abort + " before submission; paper order was not sent")
                        self.events.append_event("paper_submission_not_sent", symbol, order)
                        break
                    if (self._now() - bar.timestamp - duration(bar.timeframe)).total_seconds() > self.config.data.max_bar_age_seconds:
                        order["submission"] = "not_sent"
                        self._save_ledger(ledger)
                        raise ValueError("Data became stale before submission; paper order was not sent")
                    order["submission"] = "sending"
                    self._save_ledger(ledger)
                    try:
                        submission = self.broker.submit_order(intent)
                    except Exception:
                        order["submission"] = "submission_uncertain"
                        self._save_ledger(ledger)
                        raise
                    order.update(submission="accepted", broker_order_id=submission.broker_order_id or None, status=enum_value(submission.status))
                    self._save_ledger(ledger)
                    self._set(decision, order_submission=serial(submission))
                    self.events.append_event("paper_submission_response", symbol, serial(submission))
                    # One submission per evaluation avoids decisions based on stale account state.
                    submitted_this_evaluation = True
                self._snapshot()
                with self.status_lock:
                    evaluation = serial(self.status)
                    self.status.pop("error", None)
                    if not self.active:
                        self.status["state"] = "idle"
                self.events.append_event("paper_evaluation", "", evaluation)
                return self.view()
            except Exception as exc:
                # Do not expose SDK errors which may contain request credentials.
                error = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: paper check blocked; inspect server log/account"
                self._set(self.status, connection="error", error=error, state="blocked")
                if hasattr(self, "events"):
                    self.events.append_event("paper_blocked", "", {"reason": error})
                raise ValueError(error) from None

    def _now(self):
        return datetime.now(UTC)

    def _session_settings(self, interval, cycles, max_minutes):
        """Load and validate config once, returning it with the snapshot the operator confirms."""
        if type(interval) is not int or not 5 <= interval <= 3600 or type(cycles) is not int or not 1 <= cycles <= 100:
            raise ValueError("Interval must be 5–3600 seconds; session limit 1–100 evaluations")
        if type(max_minutes) is not int or not 1 <= max_minutes <= 390:
            raise ValueError("Maximum session duration must be 1–390 minutes")
        config = load_config(self.config_path)
        _validate_paper_runtime(config, True)
        if config.risk.allow_shorting:
            raise ValueError("Supervised paper workflow requires allow_shorting: false")
        try:
            account_id = self._get("account_id") if self.path.exists() else None
        except sqlite3.OperationalError:
            account_id = None
        if not account_id:
            raise ValueError("Run Check first so the session is bound to a paper account")
        # At most one order per evaluation, so evaluations bound the number of orders.
        snapshot = serial({"label": "Automatic PAPER session", "account_id": account_id, "watchlist": config.symbols, "timeframe": config.timeframe, "strategy": config.strategy, "risk": config.risk, "data": config.data, "interval_seconds": interval, "max_evaluations": cycles, "max_possible_orders": cycles, "max_duration_minutes": max_minutes, "ends_at_market_close": True})
        return config, snapshot

    def session_preview(self, interval, cycles, max_minutes):
        _, snapshot = self._session_settings(interval, cycles, max_minutes)
        return {"session": snapshot, "fingerprint": fingerprint(snapshot)}

    def start(self, interval, cycles, max_minutes, confirmed_fingerprint):
        with self.lock:
            with self.status_lock:
                if self.active:
                    raise ValueError("A paper session is already active")
            try:
                config, snapshot = self._session_settings(interval, cycles, max_minutes)
                if confirmed_fingerprint != fingerprint(snapshot):
                    raise ValueError("Session settings changed since they were shown; review the Automatic PAPER session and confirm again")
                self._connect(config)
                clock = self.broker.client.get_clock()
                if not clock.is_open:
                    raise ValueError("Market is closed; a PAPER session can only start while it is open")
                close = clock.next_close
                if not isinstance(close, datetime) or close.utcoffset() is None:
                    raise ValueError("Broker clock next_close must be timezone-aware")
                # Both sides are aware datetimes, so the comparison is correct across time zones.
                cap, close = self._now() + timedelta(minutes=max_minutes), close.astimezone(UTC)
                deadline, deadline_reason = (close, "market close") if close <= cap else (cap, "time limit")
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"{type(exc).__name__}: paper start blocked; inspect server log/account") from None
            # The session runs entirely from this frozen config, never rereading the file.
            session = {"id": str(uuid4()), "stop": Event(), "config": config, "deadline": deadline, "deadline_reason": deadline_reason}
            self.events.append_event("paper_session_started", "", {"session_id": session["id"], "fingerprint": fingerprint(snapshot), "session": snapshot, "deadline": deadline.isoformat(), "deadline_reason": deadline_reason})
            with self.status_lock:
                # A Stop can only target a session once it exists, so nothing can erase it.
                self.active, self.session = True, session
                self.status.update(state="running", session_id=session["id"], interval=interval, limit=cycles, completed=0, session_config=snapshot, deadline=deadline.isoformat(), deadline_reason=deadline_reason, ended_reason=None)
            self.thread = Thread(target=self._run, args=(session, interval, cycles), daemon=True)
            self.thread.start()
        return self.view()

    def _run(self, session, interval, cycles):
        stop = session["stop"]
        try:
            for index in range(cycles):
                if stop.is_set():
                    break
                if self._now() >= session["deadline"]:
                    self._set(self.status, ended_reason="Session reached its " + session["deadline_reason"])
                    break
                self.check(evaluate=True, submit=True, session=session)
                self._set(self.status, completed=index + 1)
                remaining = (session["deadline"] - self._now()).total_seconds()
                if index + 1 < cycles and remaining > 0 and self._pause(stop, min(interval, remaining)):
                    break
        except ValueError:
            pass
        finally:
            with self.status_lock:
                self.active, self.session = False, None
                if self.status["state"] != "blocked":
                    self.status["state"] = "stopped"
            if hasattr(self, "events"):
                self.events.append_event("paper_session_ended", "", self.view())

    def _pause(self, stop, seconds):
        """Wait between evaluations; True when stopped."""
        return stop.wait(seconds)

    def stop(self):
        # Read active and write state together so a session ending concurrently can't leave "stopping".
        with self.status_lock:
            if self.session:
                self.session["stop"].set()
            self.status["state"] = "stopping" if self.active else "stopped"
        return self.view()

    def history(self):
        if not self.path.exists():
            return []
        store = SQLiteEventStore(self.path)
        return serial(store.list_events())

    def view(self):
        with self.status_lock:
            return serial(dict(self.status, active=self.active, stop_policy="Stop prevents further submissions. An in-flight request may complete. Pending DAY orders and positions remain at Alpaca; manage them in Alpaca paper console. Restart never resumes a session."))
