from decimal import Decimal

from tradeos.domain.models import Position
from tradeos.execution.reconciliation import (
    ReconciliationService,
    ReconciliationStatus,
)


def position(symbol: str = "AAPL", quantity: Decimal = Decimal("10")) -> Position:
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=Decimal("100"),
        market_value=quantity * Decimal("100"),
        unrealized_pl=Decimal("0"),
        source="test",
    )


def test_matching_positions_returns_clean() -> None:
    result = ReconciliationService().reconcile(
        broker_positions=[position("AAPL", Decimal("10"))],
        local_positions=[position("AAPL", Decimal("10"))],
    )

    assert result.status == ReconciliationStatus.CLEAN
    assert result.execution_allowed is True
    assert result.issues == []
    assert result.reason == "positions_match"


def test_broker_only_position_returns_dirty() -> None:
    result = ReconciliationService().reconcile(
        broker_positions=[position("AAPL", Decimal("10"))],
        local_positions=[],
    )

    assert result.status == ReconciliationStatus.DIRTY
    assert result.issues[0].issue_type == "broker_only_position"


def test_local_only_position_returns_dirty() -> None:
    result = ReconciliationService().reconcile(
        broker_positions=[],
        local_positions=[position("AAPL", Decimal("10"))],
    )

    assert result.status == ReconciliationStatus.DIRTY
    assert result.issues[0].issue_type == "local_only_position"


def test_quantity_mismatch_returns_dirty() -> None:
    result = ReconciliationService().reconcile(
        broker_positions=[position("AAPL", Decimal("10"))],
        local_positions=[position("AAPL", Decimal("9"))],
    )

    assert result.status == ReconciliationStatus.DIRTY
    assert result.issues[0].issue_type == "quantity_mismatch"


def test_both_empty_returns_clean() -> None:
    result = ReconciliationService().reconcile(
        broker_positions=[],
        local_positions=[],
    )

    assert result.status == ReconciliationStatus.CLEAN
    assert result.execution_allowed is True


def test_zero_quantity_positions_ignored_when_both_sides_are_zero() -> None:
    result = ReconciliationService().reconcile(
        broker_positions=[position("AAPL", Decimal("0"))],
        local_positions=[position("AAPL", Decimal("0"))],
    )

    assert result.status == ReconciliationStatus.CLEAN
    assert result.issues == []


def test_dirty_result_blocks_execution() -> None:
    result = ReconciliationService().reconcile(
        broker_positions=[position("AAPL", Decimal("1"))],
        local_positions=[],
    )

    assert result.status == ReconciliationStatus.DIRTY
    assert result.execution_allowed is False
    assert result.reason == "position_reconciliation_dirty"


def test_issue_details_include_symbol_and_quantities() -> None:
    result = ReconciliationService().reconcile(
        broker_positions=[position("AAPL", Decimal("10"))],
        local_positions=[position("AAPL", Decimal("5"))],
    )

    issue = result.issues[0]
    assert issue.symbol == "AAPL"
    assert issue.broker_quantity == Decimal("10")
    assert issue.local_quantity == Decimal("5")
    assert issue.detail == "broker and local quantities differ"
