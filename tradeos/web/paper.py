"""Bounded, opt-in paper supervision. No session resumes after process restart."""
from dataclasses import asdict, replace
from datetime import UTC, datetime
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


def enum_value(value):
    return str(getattr(value, "value", value))


def serial(value):
    return json.loads(json.dumps(value, default=lambda v: asdict(v) if hasattr(v, "__dataclass_fields__") else str(v)))


class PaperSupervisor:
    def __init__(self, config_path, db_path="data/paper.sqlite3", factory=None):
        self.config_path, self.path, self.factory = config_path, Path(db_path), factory
        # lock serializes checks/sessions and is held across broker calls. status_lock guards
        # status/active only and is never held across I/O or while acquiring lock.
        self.lock, self.status_lock, self.stop_event = Lock(), Lock(), Event()
        self.active = False
        self.thread = None
        self.status = {"mode": "ALPACA PAPER ONLY", "state": "idle", "decisions": [], "realized_pnl": None}
        self.broker = self.feed = None
        self.process_lock = None

    def _connect(self):
        config = load_config(self.config_path)
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
            response = self.broker.client.get_order_by_client_id(client_id)
            qty = Decimal(str(response.filled_qty or 0))
            prior = Decimal(order.get("filled_qty", "0"))
            if qty < prior:
                raise ValueError("Broker cumulative fills decreased")
            delta = qty - prior
            symbol = order["symbol"]
            expected[symbol] = str(Decimal(expected.get(symbol, "0")) + (delta if order["side"] == "buy" else -delta))
            order.update(broker_order_id=str(response.id), filled_qty=str(qty), filled_avg_price=str(response.filled_avg_price), status=enum_value(response.status))
            self.events.append_event("paper_order_observed", symbol, dict(order, client_order_id=client_id))
        # Persist quantities and cumulative fills together, preventing double application.
        self._put("reconciled", {"positions": expected, "orders": orders})
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

    def check(self, evaluate=False, submit=False):
        with self.lock:
            try:
                self._connect()
                account, local, daily = self._snapshot()
                clock = self.broker.client.get_clock()
                self._set(self.status, connection="connected", market="open" if clock.is_open else "closed", checked_at=datetime.now(UTC).isoformat())
                if not evaluate:
                    return self.view()
                if not clock.is_open:
                    self._set(self.status, data="market_closed", decisions=[])
                    return self.view()
                bars = self.feed.recent_bars(self.config.symbols, self.config.timeframe, _required_history_count(self.config) + 2)
                now = datetime.now(UTC)
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
                    if self.stop_event.is_set():
                        break
                    _, current_local, daily = self._snapshot()
                    if (datetime.now(UTC) - bar.timestamp - duration(bar.timeframe)).total_seconds() > self.config.data.max_bar_age_seconds:
                        raise ValueError("Data became stale before submission")
                    if not self.broker.client.get_clock().is_open:
                        raise ValueError("Market closed before submission")
                    # Refresh risk against the current broker account immediately before submission.
                    result = cycle.run(bar, self.config.strategy, self.config.risk, current_local, history=[b for b in bars if b.symbol == symbol and b.timestamp < bar.timestamp])
                    if not result.execution_allowed or not result.order_intent:
                        self._set(decision, **serial(result))
                        continue
                    if self.broker.get_open_orders():
                        self._set(decision, reason="Pending orders block further submissions")
                        continue
                    if daily <= -self.config.risk.max_daily_loss:
                        self._set(decision, reason="Daily account equity loss limit blocks submission")
                        continue
                    claim = hashlib.sha256(f"{symbol}:{bar.timestamp.isoformat()}:{bar.timeframe}".encode()).hexdigest()[:32]
                    if self._get("claim:" + claim):
                        self._set(decision, reason="Duplicate bar: order already attempted")
                        continue
                    client_id = "tos-" + claim
                    order = {"symbol": symbol, "side": str(result.order_intent.side), "status": "submission_uncertain", "filled_qty": "0", "client_order_id": client_id}
                    self._put("claim:" + claim, True)
                    ledger = self._get("reconciled", {"positions": {}, "orders": {}})
                    ledger["orders"][client_id] = order
                    self._put("reconciled", ledger)
                    self.events.append_event("paper_submission_attempt", symbol, order)
                    # Claim is durable before network access. Exceptions are never retried.
                    intent = replace(result.order_intent, metadata=dict(result.order_intent.metadata, client_order_id=client_id))
                    if self.stop_event.is_set():
                        # Claim remains consumed; no order was sent. Never retry the same bar.
                        raise ValueError("Session stopped before submission; paper order was not sent")
                    if (datetime.now(UTC) - bar.timestamp - duration(bar.timeframe)).total_seconds() > self.config.data.max_bar_age_seconds:
                        raise ValueError("Data became stale before submission; order claim retained")
                    submission = self.broker.submit_order(intent)
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

    def start(self, interval, cycles):
        if type(interval) is not int or not 5 <= interval <= 3600 or type(cycles) is not int or not 1 <= cycles <= 100:
            raise ValueError("Interval must be 5–3600 seconds; session limit 1–100 evaluations")
        with self.lock:
            with self.status_lock:
                if self.active:
                    raise ValueError("A paper session is already active")
                self.active = True
                self.status.update(state="running", session_id=str(uuid4()), interval=interval, limit=cycles, completed=0)
            self.stop_event.clear()
            self.thread = Thread(target=self._run, args=(interval, cycles), daemon=True)
            self.thread.start()
        return self.view()

    def _run(self, interval, cycles):
        try:
            for index in range(cycles):
                if self.stop_event.is_set():
                    break
                self.check(evaluate=True, submit=True)
                self._set(self.status, completed=index + 1)
                if index + 1 < cycles and self.stop_event.wait(interval):
                    break
        except ValueError:
            pass
        finally:
            with self.status_lock:
                self.active = False
                if self.status["state"] != "blocked":
                    self.status["state"] = "stopped"
            if hasattr(self, "events"):
                self.events.append_event("paper_session_ended", "", self.view())

    def stop(self):
        self.stop_event.set()
        # Read active and write state together so a session ending concurrently can't leave "stopping".
        with self.status_lock:
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
