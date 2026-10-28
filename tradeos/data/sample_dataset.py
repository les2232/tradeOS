"""Generate a deterministic synthetic multi-symbol dataset for TradeOS V3.

The dataset is offline-only test data. It does not use Alpaca or any external
market data source.
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


DEFAULT_OUTPUT_PATH = Path("data/sample_multisymbol_bars.csv")
SYMBOLS = ("AAPL", "MSFT", "NVDA")
TIMEFRAME = "15Min"
DEFAULT_BARS_PER_SYMBOL = 500
DEFAULT_SEED = 42


def main() -> int:
    """Generate the sample dataset CSV."""

    parser = argparse.ArgumentParser(description="Generate TradeOS V3 sample bars.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output CSV path.",
    )
    parser.add_argument(
        "--bars-per-symbol",
        type=_positive_int,
        default=DEFAULT_BARS_PER_SYMBOL,
        help="Number of bars to generate for each symbol.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Deterministic pseudo-random seed.",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    write_sample_dataset(
        output_path,
        bars_per_symbol=args.bars_per_symbol,
        seed=args.seed,
    )
    print(f"wrote sample dataset: {output_path}")
    return 0


def write_sample_dataset(
    path: Path,
    *,
    bars_per_symbol: int = DEFAULT_BARS_PER_SYMBOL,
    seed: int = DEFAULT_SEED,
) -> None:
    """Write deterministic synthetic bars to a CSV file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = generate_rows(bars_per_symbol=bars_per_symbol, seed=seed)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "symbol",
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "timeframe",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def generate_rows(
    *,
    bars_per_symbol: int = DEFAULT_BARS_PER_SYMBOL,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, str]]:
    """Return rows ordered by timestamp, then symbol."""

    if bars_per_symbol <= 0:
        raise ValueError("bars_per_symbol must be positive")

    rng = random.Random(seed)
    start = datetime(2026, 4, 20, 13, 30, tzinfo=UTC)
    base_prices = {
        "AAPL": Decimal("185.00"),
        "MSFT": Decimal("420.00"),
        "NVDA": Decimal("880.00"),
    }
    prices = dict(base_prices)
    rows: list[dict[str, str]] = []

    for index in range(bars_per_symbol):
        timestamp = start + timedelta(minutes=15 * index)
        for symbol_index, symbol in enumerate(SYMBOLS):
            previous_close = prices[symbol]
            anchor = base_prices[symbol] + Decimal(index) * Decimal("0.015") * (
                Decimal(symbol_index) + Decimal("1")
            )
            reversion = (anchor - previous_close) * Decimal("0.08")
            noise = Decimal(str(rng.uniform(-0.45, 0.45)))
            open_price = previous_close
            close_price = _money(previous_close + reversion + noise)
            high_price = _money(
                max(open_price, close_price) + Decimal(str(rng.uniform(0.05, 0.75)))
            )
            low_price = _money(
                min(open_price, close_price) - Decimal(str(rng.uniform(0.05, 0.75)))
            )
            volume = 1000 + symbol_index * 250 + index * 3 + rng.randint(0, 500)
            prices[symbol] = close_price
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": timestamp.isoformat(),
                    "open": str(_money(open_price)),
                    "high": str(high_price),
                    "low": str(low_price),
                    "close": str(close_price),
                    "volume": str(volume),
                    "timeframe": TIMEFRAME,
                }
            )

    return rows


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--bars-per-symbol must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
