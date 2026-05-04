"""Strategy interface definitions.

This module will define the contracts that all strategies implement to transform
validated market and portfolio context into signals.
"""

from typing import Protocol

from tradeos.config.schema import StrategyConfig
from tradeos.domain.enums import StrategyMode
from tradeos.domain.models import Bar, Position, Signal


class Strategy(Protocol):
    """Contract for signal-only strategy implementations.

    Strategies only return signals. They must not create orders, access broker
    state, or perform risk checks.
    """

    name: str
    strategy_mode: StrategyMode

    def generate_signal(
        self,
        bar: Bar,
        config: StrategyConfig,
        history: list[Bar] | None = None,
        current_position: Position | None = None,
    ) -> Signal:
        """Generate a signal for one validated bar."""
