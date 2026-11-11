"""SDK contract tests run when the optional paper extra is installed."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from tradeos.data.alpaca_market_data import AlpacaLatestBarFeed, duration
from tradeos.execution.broker import AlpacaPaperBroker
from tradeos.domain.models import OrderIntent
from tradeos.domain.enums import OrderSide


def test_timeframe_and_missing_credentials():
    assert duration('15Min') == timedelta(minutes=15)
    with pytest.raises(ValueError):
        duration('invalid')
    with pytest.raises(ValueError, match='ALPACA_API_KEY'):
        AlpacaLatestBarFeed.from_env(env={})


def test_sdk_bar_request_and_validation():
    pytest.importorskip('alpaca')
    client = MagicMock()
    now = datetime.now(UTC)
    item = NS(timestamp=now-timedelta(minutes=16), open=100, high=102, low=99, close=101, volume=1000)
    client.get_stock_bars.return_value = NS(data={'AAPL': [item]})
    feed = AlpacaLatestBarFeed(client)
    bars = feed.recent_bars(['AAPL'], '15Min', 55)
    request = client.get_stock_bars.call_args.args[0]
    assert str(request.timeframe) == '15Min'
    assert request.feed.value == 'iex'
    assert request.limit == 55
    assert request.end.replace(tzinfo=UTC) <= now - timedelta(minutes=15) + timedelta(seconds=1)
    assert bars[0].close == Decimal(101)
    item.timestamp = item.timestamp.replace(tzinfo=None)
    with pytest.raises(ValueError, match='timezone-aware'):
        feed.recent_bars(['AAPL'], '15Min', 55)
    item.timestamp = now
    item.close = float('nan')
    with pytest.raises(ValueError, match='OHLC'):
        feed.recent_bars(['AAPL'], '15Min', 55)


def test_sdk_paper_order_client_id_and_rounding():
    pytest.importorskip('alpaca')
    from alpaca.trading.enums import OrderStatus
    client = MagicMock()
    client.submit_order.return_value = NS(id='order1', status=OrderStatus.ACCEPTED, filled_qty='0', submitted_at=datetime.now(UTC))
    broker = AlpacaPaperBroker(client, paper=True)
    result = broker.submit_order(OrderIntent('AAPL', OrderSide.BUY, Decimal(1000), 'limit', 'day', Decimal(103), 'approved', {'client_order_id': 'tos-test'}))
    assert result.status == 'submitted'
    request = client.submit_order.call_args.kwargs['order_data']
    assert request.client_order_id == 'tos-test'
    assert Decimal(str(request.qty)) * Decimal(103) <= Decimal(1000)
