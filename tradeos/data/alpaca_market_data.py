"""Optional Alpaca stock bars; timestamps identify the start of each bar."""
import csv
import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tradeos.domain.enums import DataQualityStatus
from tradeos.domain.models import Bar


def duration(timeframe):
    match = re.fullmatch(r"([1-9][0-9]*)(Min|Hour|Day)", timeframe)
    if not match:
        raise ValueError("Supported timeframes: NMin, NHour, NDay")
    return timedelta(seconds=int(match[1]) * {"Min": 60, "Hour": 3600, "Day": 86400}[match[2]])


class AlpacaLatestBarFeed:
    def __init__(self, client, feed="iex"):
        self.client, self.feed = client, feed

    @classmethod
    def from_env(cls, *, feed="iex", env=None):
        source = os.environ if env is None else env
        if not all(source.get(k) for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")):
            raise ValueError("Set ALPACA_API_KEY and ALPACA_SECRET_KEY in the server environment")
        from alpaca.data.historical import StockHistoricalDataClient
        return cls(StockHistoricalDataClient(source["ALPACA_API_KEY"], source["ALPACA_SECRET_KEY"]), feed)

    def bars(self, symbols, timeframe, start, end, limit=None):
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import DataFeed
        from alpaca.common.enums import Sort
        duration(timeframe)
        match = re.fullmatch(r"(\d+)(Min|Hour|Day)", timeframe)
        frame = TimeFrame(int(match[1]), {"Min": TimeFrameUnit.Minute, "Hour": TimeFrameUnit.Hour, "Day": TimeFrameUnit.Day}[match[2]])
        result = []
        # Per-symbol limits avoid starving later symbols in Alpaca's sorted response.
        for symbol in symbols:
            response = self.client.get_stock_bars(StockBarsRequest(symbol_or_symbols=[symbol], timeframe=frame, start=start, end=end, limit=limit, sort=Sort.DESC, feed=DataFeed(self.feed)))
            for item in response.data.get(symbol, []):
                values = [Decimal(str(getattr(item, key))) for key in ("open", "high", "low", "close", "volume")]
                if not all(v.is_finite() for v in values) or min(values[:4]) <= 0 or values[1] < max(values[0], values[3], values[2]) or values[2] > min(values[0], values[3]):
                    raise ValueError("Invalid OHLC market data")
                result.append(Bar(symbol, item.timestamp.astimezone(UTC) if item.timestamp.tzinfo else item.timestamp, *values, timeframe, DataQualityStatus.OK))
        return sorted(result, key=lambda b: (b.timestamp, b.symbol))

    def recent_bars(self, symbols, timeframe, limit):
        now = datetime.now(UTC)
        return self.bars(symbols, timeframe, now - timedelta(days=30), now - duration(timeframe), limit)

    def latest_bars(self, symbols, timeframe):
        return self.recent_bars(symbols, timeframe, 1)


def download_historical_bars_to_csv(*, symbols, start, end, timeframe, feed, output, env=None):
    bars = AlpacaLatestBarFeed.from_env(feed=feed, env=env).bars(symbols, timeframe, datetime.fromisoformat(start), datetime.fromisoformat(end))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["symbol", "timestamp", "open", "high", "low", "close", "volume", "timeframe"])
        for b in bars:
            writer.writerow([b.symbol, b.timestamp.isoformat(), b.open, b.high, b.low, b.close, b.volume, b.timeframe])
    return len(bars)
