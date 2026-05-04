from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradeos.domain.enums import OrderSide
from tradeos.domain.models import Fill
from tradeos.execution.fill_processor import FillProcessor


def fill(order_id: str = "order-1") -> Fill:
    return Fill(
        broker_order_id=order_id,
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        fill_price=Decimal("100"),
        filled_at=datetime(2026, 4, 25, 14, 30, tzinfo=UTC),
        raw_payload={},
    )


def test_first_fill_is_processed() -> None:
    processor = FillProcessor()

    result = processor.process_fill(fill())

    assert result.processed is True
    assert result.reason == "fill_processed"
    assert processor.processed_order_ids() == {"order-1"}


def test_duplicate_fill_is_ignored() -> None:
    processor = FillProcessor()
    first = fill("order-1")
    duplicate = fill("order-1")

    processor.process_fill(first)
    result = processor.process_fill(duplicate)

    assert result.processed is False
    assert result.reason == "duplicate_fill_ignored"
    assert processor.processed_order_ids() == {"order-1"}


def test_different_fills_are_processed() -> None:
    processor = FillProcessor()

    first = processor.process_fill(fill("order-1"))
    second = processor.process_fill(fill("order-2"))

    assert first.processed is True
    assert second.processed is True
    assert processor.processed_order_ids() == {"order-1", "order-2"}


def test_processed_order_ids_returns_copy() -> None:
    processor = FillProcessor()
    processor.process_fill(fill("order-1"))

    ids = processor.processed_order_ids()
    ids.add("mutated")

    assert processor.processed_order_ids() == {"order-1"}


def test_empty_broker_order_id_rejected() -> None:
    processor = FillProcessor()

    with pytest.raises(ValueError, match="broker_order_id cannot be empty"):
        processor.process_fill(fill(""))
