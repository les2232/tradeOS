from dataclasses import replace
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
        p.start(0, 1)
    p.start(60, 2)
    with pytest.raises(ValueError, match='already active'):
        p.start(60, 2)
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
    p.start(5, 1)
    p.thread.join(timeout=3)
    assert not p.active
    assert p.view()['completed'] == 1
    assert p.view()['state'] == 'stopped'
    assert any(e['event_type'] == 'paper_session_ended' for e in p.history())
