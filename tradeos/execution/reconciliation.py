"""Broker reconciliation workflow.

This module will compare local state against broker state and block execution
when state is dirty or ambiguous.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from tradeos.domain.models import Position


class ReconciliationStatus(StrEnum):
    """State comparison outcome."""

    CLEAN = "clean"
    DIRTY = "dirty"


@dataclass(frozen=True)
class ReconciliationIssue:
    """One broker/local position mismatch."""

    symbol: str
    issue_type: str
    broker_quantity: Decimal
    local_quantity: Decimal
    detail: str


@dataclass(frozen=True)
class ReconciliationResult:
    """Result of broker/local position reconciliation."""

    status: ReconciliationStatus
    issues: list[ReconciliationIssue]
    execution_allowed: bool
    reason: str


class ReconciliationService:
    """Compare broker positions against local positions without side effects."""

    def reconcile(
        self,
        broker_positions: list[Position],
        local_positions: list[Position],
    ) -> ReconciliationResult:
        """Return CLEAN only when broker and local quantities match by symbol."""

        broker_by_symbol = _position_quantities(broker_positions)
        local_by_symbol = _position_quantities(local_positions)
        issues: list[ReconciliationIssue] = []

        for symbol in sorted(set(broker_by_symbol) | set(local_by_symbol)):
            broker_quantity = broker_by_symbol.get(symbol, Decimal("0"))
            local_quantity = local_by_symbol.get(symbol, Decimal("0"))

            if broker_quantity == Decimal("0") and local_quantity == Decimal("0"):
                continue

            if broker_quantity == local_quantity:
                continue

            if broker_quantity != Decimal("0") and local_quantity == Decimal("0"):
                issue_type = "broker_only_position"
                detail = "broker has position missing from local state"
            elif broker_quantity == Decimal("0") and local_quantity != Decimal("0"):
                issue_type = "local_only_position"
                detail = "local state has position missing from broker"
            else:
                issue_type = "quantity_mismatch"
                detail = "broker and local quantities differ"

            issues.append(
                ReconciliationIssue(
                    symbol=symbol,
                    issue_type=issue_type,
                    broker_quantity=broker_quantity,
                    local_quantity=local_quantity,
                    detail=detail,
                )
            )

        if issues:
            return ReconciliationResult(
                status=ReconciliationStatus.DIRTY,
                issues=issues,
                execution_allowed=False,
                reason="position_reconciliation_dirty",
            )

        return ReconciliationResult(
            status=ReconciliationStatus.CLEAN,
            issues=[],
            execution_allowed=True,
            reason="positions_match",
        )


def _position_quantities(positions: list[Position]) -> dict[str, Decimal]:
    quantities: dict[str, Decimal] = {}
    for position in positions:
        quantities[position.symbol] = quantities.get(position.symbol, Decimal("0")) + Decimal(
            position.quantity
        )
    return quantities
