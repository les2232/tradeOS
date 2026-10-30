"""Validated, deterministic CSV input for offline research."""

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tradeos.domain.enums import DataQualityStatus
from tradeos.domain.models import Bar


@dataclass(frozen=True)
class DatasetSummary:
    total_rows: int
    symbols: list[str]
    start_timestamp: datetime | None
    end_timestamp: datetime | None
    timeframe_counts: dict[str, int]


def load_bars_from_csv(path: str | Path) -> list[Bar]:
    """Reject malformed/duplicate bars and return timestamp/symbol order.

    Timestamps must include an offset; normalize them to UTC. No network or
    broker access occurs. A bad row rejects the entire dataset.
    """
    bars = []
    seen = set()
    fields = {"symbol", "timestamp", "open", "high", "low", "close", "volume", "timeframe"}
    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not fields.issubset(reader.fieldnames or []):
            raise ValueError("dataset CSV missing required columns: " + ", ".join(sorted(fields - set(reader.fieldnames or []))))
        for row_number, row in enumerate(reader, start=2):
            try:
                symbol = row["symbol"].strip()
                timeframe = row["timeframe"].strip()
                if not symbol or not timeframe:
                    raise ValueError("symbol and timeframe must not be blank")
                timestamp = datetime.fromisoformat(row["timestamp"])
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise ValueError("timestamp must be timezone-aware")
                timestamp = timestamp.astimezone(UTC)
                values = {name: Decimal(row[name]) for name in ("open", "high", "low", "close", "volume")}
                if any(not value.is_finite() for value in values.values()):
                    raise ValueError("prices and volume must be finite")
                if any(values[name] <= 0 for name in ("open", "high", "low", "close")):
                    raise ValueError("prices must be positive")
                if values["volume"] < 0:
                    raise ValueError("volume cannot be negative")
                if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
                    raise ValueError("inconsistent OHLC range")
                key = (symbol, timestamp, timeframe)
                if key in seen:
                    raise ValueError("duplicate symbol/timestamp/timeframe")
                quality = DataQualityStatus(row.get("data_quality") or "ok")
                bars.append(Bar(symbol=symbol, timestamp=timestamp, timeframe=timeframe, data_quality=quality, **values))
                seen.add(key)
            except (ValueError, TypeError, AttributeError, InvalidOperation) as exc:
                raise ValueError(f"dataset CSV row {row_number}: {exc}") from exc
    if not bars:
        raise ValueError("dataset CSV contains no bars")
    return sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))


def summarize_bars(bars: list[Bar]) -> DatasetSummary:
    return DatasetSummary(
        total_rows=len(bars),
        symbols=sorted({bar.symbol for bar in bars}),
        start_timestamp=min((bar.timestamp for bar in bars), default=None),
        end_timestamp=max((bar.timestamp for bar in bars), default=None),
        timeframe_counts=dict(Counter(bar.timeframe for bar in bars)),
    )
