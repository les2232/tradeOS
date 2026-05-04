import csv
from pathlib import Path
from uuid import uuid4

from scripts.generate_sample_dataset import (
    DEFAULT_BARS_PER_SYMBOL,
    DEFAULT_SEED,
    SYMBOLS,
    TIMEFRAME,
    generate_rows,
    write_sample_dataset,
)


def test_sample_dataset_generator_output_shape() -> None:
    output_path = Path("tests/fixtures") / f"sample_multisymbol_{uuid4().hex}.csv"

    try:
        write_sample_dataset(output_path, bars_per_symbol=12, seed=99)

        with output_path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))

        assert len(rows) == 12 * len(SYMBOLS)
        assert set(rows[0]) == {
            "symbol",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "timeframe",
        }
        assert {row["symbol"] for row in rows} == set(SYMBOLS)
        assert {row["timeframe"] for row in rows} == {TIMEFRAME}
        assert all("+" in row["timestamp"] for row in rows)
        assert all(float(row["volume"]) > 0 for row in rows)
        assert all(
            float(row["high"]) >= max(float(row["open"]), float(row["close"]))
            for row in rows
        )
        assert all(
            float(row["low"]) <= min(float(row["open"]), float(row["close"]))
            for row in rows
        )
    finally:
        output_path.unlink(missing_ok=True)


def test_sample_dataset_defaults_are_500_bars_and_seed_42() -> None:
    rows = generate_rows()

    assert len(rows) == DEFAULT_BARS_PER_SYMBOL * len(SYMBOLS)
    assert DEFAULT_BARS_PER_SYMBOL == 500
    assert DEFAULT_SEED == 42


def test_sample_dataset_seed_is_deterministic() -> None:
    first = generate_rows(bars_per_symbol=3, seed=7)
    second = generate_rows(bars_per_symbol=3, seed=7)
    different = generate_rows(bars_per_symbol=3, seed=8)

    assert first == second
    assert first != different
