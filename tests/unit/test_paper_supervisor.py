from dataclasses import replace
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from tradeos.config.loader import load_config
from tradeos.domain.enums import DataQualityStatus, TradeAction, StrategyMode
from tradeos.domain.models import Bar, PortfolioState, Signal
from tradeos.execution.broker import AlpacaPaperBroker, BrokerSafetyError, _order_status
from tradeos.web.app import create_app
from tradeos.web.paper import PaperSupervisor


@pytest.fixture
def paper(tmp_path, monkeypatch):
    config = load_config('config/paper.yaml')
    config = replace(config, symbols=['AAPL'], risk=replace(config.risk, entry_start_time='00:00', entry_end_time='23:59', no_entry_after='23:58', force_flatten_at='23:59'))
    monkeypatch.setattr('tradeos.web.paper.load_config', lambda _: config)
    broker, feed = MagicMock(), MagicMock()
    broker.client.get_account.return_value = NS(id='paper-account', last_equity='10000', status='ACTIVE', trading_blocked=False, account_blocked=False)
    broker.get_account.return_value = PortfolioState(D(10000), D(10000), D(10000), [], datetime.now(UTC))
    broker.get_open_orders.return_value = []
    timestamp = datetime.now(UTC) - timedelta(minutes=15, seconds=2)
    feed.recent_bars.return_value = [Bar('AAPL', timestamp - timedelta(minutes=15*i), D(100), D(101), D(99), D(100), D(100), '15Min', DataQualityStatus.OK) for i in range(55)][::-1]
    monkeypatch.setattr('tradeos.web.paper.MeanReversionStrategy.generate_signal', lambda self, bar, config, **kw: Signal(bar.symbol, TradeAction.BUY, StrategyMode.MEAN_REVERSION, bar.timestamp, 'test opportunity', D(1)))
    broker.client.get_clock.return_value = NS(is_open=True)
    broker.client.get_order_by_client_id.return_value = NS(id='broker-1', filled_qty='0', filled_avg_price=None, status='rejected')
    from tradeos.domain.models import OrderSubmission
    broker.submit_order.return_value = OrderSubmission("broker-1", "AAPL", "buy", D(10), "submitted", datetime.now(UTC))
    supervisor = PaperSupervisor('unused', tmp_path / 'paper.db', lambda _: (broker, feed))
    return supervisor, broker, feed


def begin(p, interval, cycles):
    """Check (binding the account), preview, then start with the previewed fingerprint, as the UI does."""
    p.check()
    return p.start(interval, cycles, p.session_preview(interval, cycles)['fingerprint'])


def test_idle_and_no_submit(paper):
    p, broker, _ = paper
    assert p.view()['state'] == 'idle'
    broker.get_account.assert_not_called()
    p.check(evaluate=True)
    broker.submit_order.assert_not_called()
    assert p.view()['decisions'][0]['signal']['action'] == 'buy'


def test_duplicate_claim_survives_restart(paper):
    p, broker, feed = paper
    p.check(evaluate=True, submit=True)
    assert broker.submit_order.call_count == 1
    p.check(evaluate=True, submit=True)
    assert broker.submit_order.call_count == 1
    p.process_lock.close()
    restarted = PaperSupervisor('unused', p.path, lambda _: (broker, feed))
    assert not restarted.active
    restarted.check(evaluate=True, submit=True)
    assert broker.submit_order.call_count == 1


def test_uncertain_submission_never_retried(paper):
    p, broker, _ = paper
    broker.submit_order.side_effect = TimeoutError('secret must not appear')
    with pytest.raises(ValueError, match='paper check blocked'):
        p.check(evaluate=True, submit=True)
    broker.client.get_order_by_client_id.side_effect = TimeoutError()
    with pytest.raises(ValueError):
        p.check(evaluate=True, submit=True)
    assert broker.submit_order.call_count == 1
    assert 'secret' not in p.view()['error']


@pytest.mark.parametrize('kind', ['stale', 'missing', 'future', 'closed', 'open_order'])
def test_data_and_pending_gates(paper, kind):
    p, broker, feed = paper
    if kind == 'stale':
        feed.recent_bars.return_value = [replace(b, timestamp=b.timestamp-timedelta(hours=1)) for b in feed.recent_bars.return_value]
    if kind == 'future':
        feed.recent_bars.return_value = [replace(b, timestamp=b.timestamp+timedelta(days=1)) for b in feed.recent_bars.return_value]
    if kind == 'missing':
        feed.recent_bars.return_value = []
    if kind == 'closed':
        broker.client.get_clock.return_value.is_open = False
    if kind == 'open_order':
        broker.get_open_orders.return_value = [object()]
    try:
        p.check(evaluate=True, submit=True)
    except ValueError:
        pass
    broker.submit_order.assert_not_called()


def test_dirty_reconciliation_blocks(paper):
    p, broker, _ = paper
    from tradeos.domain.models import Position
    account = broker.get_account.return_value
    broker.get_account.return_value = replace(account, positions=[Position('AAPL', D(1), D(100), D(100), D(0), 'alpaca_paper')])
    with pytest.raises(ValueError, match='dirty'):
        p.check(evaluate=True, submit=True)
    broker.submit_order.assert_not_called()


@pytest.mark.parametrize('symbol,source', [('COP', 'alpaca_paper'), ('AAPL', 'alpaca_paper_short')])
def test_unmanaged_or_short_position_blocks(paper, symbol, source):
    p, broker, _ = paper
    from tradeos.domain.models import Position
    account = broker.get_account.return_value
    broker.get_account.return_value = replace(account, positions=[Position(symbol, D(1), D(100), D(100), D(0), source)])
    with pytest.raises(ValueError, match='Unmanaged or short'):
        p.check(evaluate=True, submit=True)
    broker.submit_order.assert_not_called()


def test_partial_fills_are_cumulative_and_idempotent(paper):
    p, broker, _ = paper
    p.check(evaluate=True, submit=True)
    from tradeos.domain.models import Position
    broker.get_account.return_value = replace(broker.get_account.return_value, positions=[Position('AAPL', D(2), D(100), D(200), D(0), 'alpaca_paper')])
    broker.client.get_order_by_client_id.return_value = NS(id='broker-1', filled_qty='2', filled_avg_price='100', status='partially_filled')
    p.check()
    p.check()
    assert p._get('positions')['AAPL'] == '2'
    assert p.view()['orders'][0]['status'] == 'partially_filled'


def test_start_stop_and_bounds(paper):
    p, broker, _ = paper
    with pytest.raises(ValueError):
        begin(p, 0, 1)
    begin(p, 60, 2)
    with pytest.raises(ValueError, match='already active'):
        begin(p, 60, 2)
    p.stop()
    p.thread.join(timeout=3)
    assert not p.active
    broker.client.cancel_orders.assert_not_called()
    broker.client.close_all_positions.assert_not_called()


def test_paper_only_before_sdk(monkeypatch):
    forbidden = MagicMock(side_effect=AssertionError('SDK imported'))
    monkeypatch.setattr('tradeos.execution.broker._import_alpaca_trading_dependencies', forbidden)
    with pytest.raises(BrokerSafetyError):
        AlpacaPaperBroker.from_env(paper=False, env={})
    forbidden.assert_not_called()


def test_routes_require_explicit_confirmation(paper):
    p, _, _ = paper
    client = create_app(paper_supervisor=p).test_client()
    assert client.get('/api/paper').json['state'] == 'idle'
    assert client.post('/api/paper/start', json={}).status_code == 400
    assert client.post('/api/paper/start', json={}, headers={'X-TradeOS-Request': 'paper'}).status_code == 400
    assert client.post('/api/paper/evaluate', json={}, headers={'X-TradeOS-Request': 'paper'}).status_code == 200
    assert b'Alpaca PAPER trading' in client.get('/').data


def test_terminal_partial_status():
    assert _order_status('canceled', D(2)) == 'canceled'


@pytest.mark.parametrize('field', ['live', 'nonpaper', 'shorting'])
def test_invalid_runtime_rejected_before_connection(paper, monkeypatch, field):
    p, broker, _ = paper
    config = load_config('config/paper.yaml')
    if field == 'live':
        config = replace(config, execution_mode='live')
    elif field == 'nonpaper':
        config = replace(config, broker=replace(config.broker, paper=False))
    else:
        config = replace(config, risk=replace(config.risk, allow_shorting=True))
    monkeypatch.setattr('tradeos.web.paper.load_config', lambda _: config)
    with pytest.raises(ValueError):
        p.check(evaluate=True, submit=True)
    broker.get_account.assert_not_called()
    broker.submit_order.assert_not_called()


def test_second_server_cannot_own_ledger(paper):
    p, broker, feed = paper
    p.check()
    second = PaperSupervisor('unused', p.path, lambda _: (broker, feed))
    with pytest.raises(ValueError, match='Another paper server'):
        second.check()


def test_daily_equity_loss_blocks_submission(paper):
    p, broker, _ = paper
    broker.client.get_account.return_value.last_equity = '11000'
    p.check(evaluate=True, submit=True)
    broker.submit_order.assert_not_called()
    assert 'Daily account equity loss' in p.view()['decisions'][0]['reason']


def test_session_limit_finishes_without_resume(paper):
    p, _, _ = paper
    begin(p, 5, 1)
    p.thread.join(timeout=3)
    assert not p.active
    assert p.view()['completed'] == 1
    assert p.view()['state'] == 'stopped'
    assert any(e['event_type'] == 'paper_session_ended' for e in p.history())


class StaleActiveReadSupervisor(PaperSupervisor):
    """Widens stop()'s read-active/write-state window so the session can end inside it."""

    def __init__(self, *args, **kwargs):
        self.armed, self.release, self.run_done = False, threading.Event(), threading.Event()
        super().__init__(*args, **kwargs)

    @property
    def active(self):
        value = self._active
        if self.armed and threading.current_thread() is not self.thread:
            self.armed = False
            self.release.set()
            # Without status_lock the session finishes here; with it, the session waits for stop().
            self.run_done.wait(1)
        return value

    @active.setter
    def active(self, value):
        self._active = value

    def _run(self, *args):
        try:
            super()._run(*args)
        finally:
            self.run_done.set()


def test_stop_racing_session_end_never_leaves_stopping(paper):
    p, broker, feed = paper
    racy = StaleActiveReadSupervisor('unused', p.path, lambda _: (broker, feed))
    bars = feed.recent_bars.return_value
    feed.recent_bars.side_effect = lambda *a, **kw: racy.release.wait(5) and bars
    begin(racy, 5, 1)
    racy.armed = True
    racy.stop()
    racy.thread.join(timeout=5)
    view = racy.view()
    assert view['active'] is False
    assert view['state'] == 'stopped'


def test_concurrent_view_and_stop_during_session(paper):
    # Deadlock/exception smoke test; the GIL makes torn reads too rare to assert on reliably.
    p, broker, feed = paper
    bars = feed.recent_bars.return_value
    in_check = threading.Event()
    feed.recent_bars.side_effect = lambda *a, **kw: in_check.set() or bars
    errors = []

    def hammer(stop_at):
        try:
            for i in range(300):
                view = p.view()
                assert view['state'] in {'running', 'stopping', 'stopped', 'idle'}
                if i == stop_at:
                    p.stop()
        except Exception as exc:  # surfaced below; threads swallow assertion errors
            errors.append(exc)

    begin(p, 5, 3)
    assert in_check.wait(5)
    threads = [threading.Thread(target=hammer, args=(50 * n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    p.thread.join(timeout=10)
    assert errors == []
    assert not p.thread.is_alive()
    view = p.view()
    assert view['active'] is False
    assert view['state'] == 'stopped'


class StopOnFirstHook:
    """Calls stop() at whichever start-gap hook runs first: clearing a shared event, or creating the thread."""

    def __init__(self, supervisor, real_thread):
        self.supervisor, self.real_thread, self.fired = supervisor, real_thread, False

    def fire(self):
        if not self.fired:
            self.fired = True
            self.supervisor.stop()

    def clear(self):  # stands in for a shared stop event that start() clears after activation
        self.fire()
        self.flag.clear()

    def thread(self, *args, **kwargs):
        self.fire()
        return self.real_thread(*args, **kwargs)


def test_stop_during_start_gap_prevents_submission(paper, monkeypatch):
    p, broker, _ = paper
    hook = StopOnFirstHook(p, threading.Thread)
    if hasattr(p, 'stop_event'):
        hook.flag = p.stop_event
        p.stop_event = NS(set=hook.flag.set, clear=hook.clear, is_set=hook.flag.is_set, wait=hook.flag.wait)
    monkeypatch.setattr('tradeos.web.paper.Thread', hook.thread)
    begin(p, 5, 1)
    p.thread.join(timeout=5)
    assert hook.fired
    broker.submit_order.assert_not_called()
    assert p.view()['state'] == 'stopped'


def hold_long(p, broker, monkeypatch, qty, action=TradeAction.SELL):
    """Clean ledger and broker both long `qty` AAPL, the strategy emitting `action`, daily loss at the limit."""
    from tradeos.domain.models import Position
    p.check()
    p._put('reconciled', {'positions': {'AAPL': str(qty)}, 'orders': {}})
    positions = [Position('AAPL', D(qty), D(100), D(100) * qty, D(0), 'alpaca_paper')] if qty else []
    broker.get_account.return_value = replace(broker.get_account.return_value, positions=positions)
    broker.get_positions.return_value = positions
    broker.client.get_account.return_value.last_equity = '11000'
    monkeypatch.setattr('tradeos.web.paper.MeanReversionStrategy.generate_signal', lambda self, bar, config, **kw: Signal(bar.symbol, action, StrategyMode.MEAN_REVERSION, bar.timestamp, 'test exit', D(1)))


def test_daily_loss_blocks_buy(paper, monkeypatch):
    p, broker, _ = paper
    hold_long(p, broker, monkeypatch, 0, TradeAction.BUY)
    p.check(evaluate=True, submit=True)
    broker.submit_order.assert_not_called()
    assert 'Daily account equity loss' in p.view()['decisions'][0]['reason']


def test_daily_loss_allows_reducing_sell(paper, monkeypatch):
    p, broker, _ = paper
    hold_long(p, broker, monkeypatch, 10)
    p.check(evaluate=True, submit=True)
    assert broker.submit_order.call_count == 1
    intent = broker.submit_order.call_args.args[0]
    assert (str(intent.side), intent.quantity) == ('sell', D(10))


@pytest.mark.parametrize('side,qty,long_qty,pending,allowed', [
    ('sell', 10, 10, 0, True), ('sell', 4, 10, 6, True), ('sell', 10, 10, 1, False),
    ('sell', 11, 10, 0, False), ('sell', 1, 0, 0, False), ('sell', 0, 10, 0, False), ('buy', 1, 10, 0, False)])
def test_daily_loss_blocks_sell_exceeding_available_long(side, qty, long_qty, pending, allowed):
    from tradeos.web.paper import reduces_exposure
    assert reduces_exposure(NS(side=side, quantity=D(qty)), D(long_qty), D(pending)) is allowed


def only_order(p):
    (order,) = p._get('orders').values()
    return order


def test_stop_before_send_marks_not_sent_and_reconciles_clean(paper, monkeypatch):
    p, broker, _ = paper
    from tradeos.storage.sqlite import SQLiteEventStore
    real_append = SQLiteEventStore.append_event

    def stop_after_claim(store, event_type, *args, **kwargs):
        real_append(store, event_type, *args, **kwargs)
        if event_type == 'paper_submission_attempt':
            p.stop()

    monkeypatch.setattr(SQLiteEventStore, 'append_event', stop_after_claim)
    begin(p, 5, 1)
    p.thread.join(timeout=5)
    broker.submit_order.assert_not_called()
    broker.client.get_order_by_client_id.reset_mock()
    broker.client.get_order_by_client_id.side_effect = LookupError('order not found')
    p.check()
    broker.client.get_order_by_client_id.assert_not_called()
    assert p.view()['reconciliation'] == 'clean'
    assert only_order(p)['submission'] == 'not_sent'


def test_send_error_stays_uncertain_and_blocks(paper):
    p, broker, _ = paper
    broker.submit_order.side_effect = ConnectionError('reset mid-request')
    with pytest.raises(ValueError):
        p.check(evaluate=True, submit=True)
    assert only_order(p)['submission'] == 'submission_uncertain'
    broker.client.get_order_by_client_id.side_effect = LookupError('order not found')
    with pytest.raises(ValueError, match='resolve it manually'):
        p.check()


def test_crash_left_sending_blocks_with_manual_resolution(paper):
    p, broker, _ = paper
    p.check()
    order = {'symbol': 'AAPL', 'side': 'buy', 'submission': 'sending', 'status': 'not_submitted', 'filled_qty': '0', 'client_order_id': 'tos-crashed'}
    p._put('reconciled', {'positions': {}, 'orders': {'tos-crashed': order}})
    broker.client.get_order_by_client_id.side_effect = LookupError('order not found')
    with pytest.raises(ValueError, match='tos-crashed is sending .* resolve it manually'):
        p.check()
    broker.submit_order.assert_not_called()


def test_duplicate_broker_order_id_blocks(paper):
    p, broker, _ = paper
    p.check()
    earlier = {'symbol': 'AAPL', 'side': 'buy', 'submission': 'accepted', 'status': 'rejected', 'filled_qty': '0', 'client_order_id': 'tos-earlier', 'broker_order_id': 'broker-1'}
    p._put('reconciled', {'positions': {}, 'orders': {'tos-earlier': earlier}})
    with pytest.raises(ValueError, match='broker-1 .* conflicts with another recorded order'):
        p.check(evaluate=True, submit=True)


def test_broker_id_conflict_rolls_back_ledger_json(paper):
    import sqlite3
    p, broker, _ = paper
    p.check()
    with sqlite3.connect(p.path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS paper_broker_orders (broker_order_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE)")
        db.execute("INSERT INTO paper_broker_orders VALUES ('broker-1', 'tos-other')")
    with pytest.raises(ValueError, match='conflicts'):
        p.check(evaluate=True, submit=True)
    order = only_order(p)
    assert order['submission'] == 'sending'
    assert 'broker_order_id' not in order


def test_session_uses_frozen_config_after_disk_change(paper, monkeypatch):
    p, broker, feed = paper
    import tradeos.web.paper as module
    original = module.load_config('unused')
    edited = replace(original, symbols=['MSFT'], risk=replace(original.risk, max_daily_loss=D(1)))
    bars = feed.recent_bars.return_value

    def edit_config_after_first_evaluation(*args, **kwargs):
        monkeypatch.setattr('tradeos.web.paper.load_config', lambda _: edited)
        return bars

    feed.recent_bars.side_effect = edit_config_after_first_evaluation
    p._pause = lambda stop, seconds: stop.is_set()
    begin(p, 5, 2)
    p.thread.join(timeout=10)
    view = p.view()
    assert (view['state'], view['completed']) == ('stopped', 2), view.get('error')
    assert feed.recent_bars.call_count == 2
    assert p.config.symbols == ['AAPL'] and p.config.risk.max_daily_loss == original.risk.max_daily_loss
    started = [e for e in p.history() if e['event_type'] == 'paper_session_started']
    assert started[0]['payload']['session']['risk']['max_daily_loss'] == str(original.risk.max_daily_loss)


def test_start_rejects_settings_changed_since_preview(paper, monkeypatch):
    p, broker, _ = paper
    import tradeos.web.paper as module
    original = module.load_config('unused')
    p.check()
    shown = p.session_preview(5, 1)
    assert shown['session']['label'] == 'Automatic PAPER session'
    assert shown['session']['max_possible_orders'] == 1
    monkeypatch.setattr('tradeos.web.paper.load_config', lambda _: replace(original, risk=replace(original.risk, max_daily_loss=D(1))))
    with pytest.raises(ValueError, match='changed since they were shown'):
        p.start(5, 1, shown['fingerprint'])
    assert not p.active
    broker.submit_order.assert_not_called()


def test_session_preview_route(paper):
    p, _, _ = paper
    client = create_app(paper_supervisor=p).test_client()
    assert 'Run Check first' in client.get('/api/paper/session?interval=60&cycles=5').json['error']
    p.check()
    preview = client.get('/api/paper/session?interval=60&cycles=5').json
    assert preview['session']['account_id'] == 'paper-account' and len(preview['fingerprint']) == 64
    assert client.get('/api/paper/session?interval=1&cycles=5').status_code == 400
