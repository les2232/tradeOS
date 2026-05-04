"""Fill processing workflow.

This module will apply broker fills idempotently by broker order_id and update
portfolio state through explicit accounting interfaces.
"""

from dataclasses import dataclass

from tradeos.domain.models import Fill


@dataclass(frozen=True)
class FillProcessResult:
    """Result of checking and recording a broker fill for idempotency."""

    fill: Fill
    processed: bool
    reason: str


class FillProcessor:
    """Idempotency guard for broker fills keyed by broker_order_id."""

    def __init__(self) -> None:
        self._processed_order_ids: set[str] = set()

    def process_fill(self, fill: Fill) -> FillProcessResult:
        """Process a fill once and ignore duplicate broker order IDs."""

        if not fill.broker_order_id:
            raise ValueError("broker_order_id cannot be empty")

        if fill.broker_order_id in self._processed_order_ids:
            return FillProcessResult(
                fill=fill,
                processed=False,
                reason="duplicate_fill_ignored",
            )

        self._processed_order_ids.add(fill.broker_order_id)
        return FillProcessResult(
            fill=fill,
            processed=True,
            reason="fill_processed",
        )

    def processed_order_ids(self) -> set[str]:
        """Return a copy of processed broker order IDs."""

        return set(self._processed_order_ids)
