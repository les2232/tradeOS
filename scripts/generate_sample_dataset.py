"""Compatibility entrypoint for the packaged synthetic dataset generator."""
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradeos.data.sample_dataset import (
    DEFAULT_BARS_PER_SYMBOL, DEFAULT_SEED, DEFAULT_OUTPUT_PATH, SYMBOLS, TIMEFRAME,
    generate_rows, write_sample_dataset, main,
)

if __name__ == "__main__":
    raise SystemExit(main())
