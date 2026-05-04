"""Safety rule definitions.

This module will contain explicit rules for blocking unsafe behavior such as
dirty state, accidental shorts, missing reconciliation, or untested live paths.
"""

from decimal import Decimal

from tradeos.domain.models import PortfolioState, Position


def find_position(portfolio: PortfolioState, symbol: str) -> Position | None:
    """Return the portfolio position for a symbol, if present."""

    for position in portfolio.positions:
        if position.symbol == symbol:
            return position
    return None


def has_long_position(portfolio: PortfolioState, symbol: str) -> bool:
    """Return true when the portfolio has a positive long quantity."""

    position = find_position(portfolio, symbol)
    return position is not None and position.quantity > Decimal("0")


def has_existing_position(portfolio: PortfolioState, symbol: str) -> bool:
    """Return true when the portfolio already holds a positive quantity."""

    return has_long_position(portfolio, symbol)


def would_exceed_max_positions(
    portfolio: PortfolioState,
    symbol: str,
    max_open_positions: int,
) -> bool:
    """Return true when opening a new symbol would exceed the position limit."""

    if has_long_position(portfolio, symbol):
        return False

    open_positions = sum(
        1 for position in portfolio.positions if position.quantity > Decimal("0")
    )
    return open_positions >= max_open_positions


def has_sufficient_buying_power(
    portfolio: PortfolioState,
    required_buying_power: Decimal,
) -> bool:
    """Return true when buying power covers the requested budget."""

    return portfolio.buying_power >= required_buying_power
