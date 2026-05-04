from datetime import UTC, datetime
from decimal import Decimal

from tradeos.config.schema import RiskConfig
from tradeos.domain.enums import RiskDecisionStatus, StrategyMode, TradeAction
from tradeos.domain.models import PortfolioState, Position, Signal
from tradeos.risk.risk_manager import RiskManager


def risk_config(
    *,
    max_position_value: Decimal = Decimal("1000"),
    max_open_positions: int = 3,
    max_daily_loss: Decimal = Decimal("250"),
    allow_shorting: bool = False,
    no_entry_after: str = "15:30",
    force_flatten_at: str = "15:55",
    allowed_symbols: list[str] | None = None,
    refined_entry_hours: list[str] | None = None,
) -> RiskConfig:
    return RiskConfig(
        max_position_value=max_position_value,
        max_open_positions=max_open_positions,
        max_daily_loss=max_daily_loss,
        allow_shorting=allow_shorting,
        no_entry_after=no_entry_after,
        force_flatten_at=force_flatten_at,
        allowed_symbols=allowed_symbols,
        refined_entry_hours=refined_entry_hours,
    )


def signal(
    action: TradeAction,
    *,
    symbol: str = "AAPL",
    requested_quantity: Decimal | None = None,
    timestamp: datetime = datetime(2026, 4, 24, 14, 30, tzinfo=UTC),
) -> Signal:
    metadata = {}
    if requested_quantity is not None:
        metadata["requested_quantity"] = str(requested_quantity)
    return Signal(
        symbol=symbol,
        action=action,
        strategy_mode=StrategyMode.MEAN_REVERSION,
        timestamp=timestamp,
        reason="unit-test",
        confidence=Decimal("0.5"),
        metadata=metadata,
    )


def position(
    symbol: str = "AAPL",
    quantity: Decimal = Decimal("10"),
    unrealized_pl: Decimal = Decimal("0"),
) -> Position:
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=Decimal("100"),
        market_value=Decimal("1000"),
        unrealized_pl=unrealized_pl,
        source="broker",
    )


def portfolio(
    *,
    buying_power: Decimal = Decimal("2000"),
    positions: list[Position] | None = None,
) -> PortfolioState:
    return PortfolioState(
        cash=Decimal("2000"),
        equity=Decimal("5000"),
        buying_power=buying_power,
        positions=positions or [],
        timestamp=datetime(2026, 4, 24, 14, 30, tzinfo=UTC),
    )


def test_hold_signal_produces_no_approved_quantity() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.HOLD),
        portfolio(),
        risk_config(),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.approved_quantity == Decimal("0")
    assert decision.reason == "hold_signal_no_order"


def test_buy_approved_when_buying_power_is_sufficient() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY),
        portfolio(buying_power=Decimal("1000")),
        risk_config(max_position_value=Decimal("1000")),
    )

    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.approved_quantity == Decimal("1000")
    assert decision.blocked_rules == []


def test_buy_blocked_when_buying_power_is_insufficient() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY),
        portfolio(buying_power=Decimal("999")),
        risk_config(max_position_value=Decimal("1000")),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.approved_quantity == Decimal("0")
    assert "insufficient_buying_power" in decision.blocked_rules


def test_buy_blocked_when_max_open_positions_reached() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY, symbol="NVDA"),
        portfolio(positions=[position("AAPL"), position("MSFT")]),
        risk_config(max_open_positions=2),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert "max_open_positions_reached" in decision.blocked_rules


def test_buy_blocked_when_already_holding_same_symbol() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY, symbol="AAPL"),
        portfolio(positions=[position("AAPL", Decimal("10"))]),
        risk_config(),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.reason == "buy_blocked_existing_position"
    assert decision.blocked_rules == ["existing_position"]


def test_buy_allowed_when_holding_different_symbol() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY, symbol="MSFT"),
        portfolio(positions=[position("AAPL", Decimal("10"))]),
        risk_config(max_position_value=Decimal("1000"), max_open_positions=3),
    )

    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.reason == "buy_signal_approved"


def test_buy_allowed_when_same_symbol_quantity_is_zero() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY, symbol="AAPL"),
        portfolio(positions=[position("AAPL", Decimal("0"))]),
        risk_config(max_position_value=Decimal("1000")),
    )

    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.blocked_rules == []


def test_buy_before_entry_window_blocked() -> None:
    decision = RiskManager().approve(
        signal(
            TradeAction.BUY,
            timestamp=datetime(2026, 4, 24, 13, 59, tzinfo=UTC),
        ),
        portfolio(),
        risk_config(),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.reason == "buy_blocked_outside_entry_window"
    assert decision.blocked_rules == ["entry_time_window"]


def test_buy_inside_entry_window_approved() -> None:
    decision = RiskManager().approve(
        signal(
            TradeAction.BUY,
            timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=UTC),
        ),
        portfolio(),
        risk_config(max_position_value=Decimal("1000")),
    )

    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.reason == "buy_signal_approved"


def test_buy_after_entry_window_blocked() -> None:
    decision = RiskManager().approve(
        signal(
            TradeAction.BUY,
            timestamp=datetime(2026, 4, 24, 19, 31, tzinfo=UTC),
        ),
        portfolio(),
        risk_config(),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.reason == "buy_blocked_outside_entry_window"
    assert "entry_time_window" in decision.blocked_rules


def test_buy_allowed_for_allowed_symbol() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY, symbol="AAPL"),
        portfolio(),
        risk_config(allowed_symbols=["AAPL", "MSFT"]),
    )

    assert decision.status == RiskDecisionStatus.APPROVED


def test_buy_blocked_for_excluded_symbol() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY, symbol="NVDA"),
        portfolio(),
        risk_config(allowed_symbols=["AAPL", "MSFT"]),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.reason == "buy_blocked_symbol_filter"
    assert decision.blocked_rules == ["symbol_filter"]


def test_buy_allowed_during_refined_entry_hour() -> None:
    decision = RiskManager().approve(
        signal(
            TradeAction.BUY,
            timestamp=datetime(2026, 4, 24, 15, 30, tzinfo=UTC),
        ),
        portfolio(),
        risk_config(refined_entry_hours=["14", "15"]),
    )

    assert decision.status == RiskDecisionStatus.APPROVED


def test_buy_blocked_after_no_entry_time_even_inside_entry_window() -> None:
    decision = RiskManager().approve(
        signal(
            TradeAction.BUY,
            timestamp=datetime(2026, 4, 24, 15, 31, tzinfo=UTC),
        ),
        portfolio(),
        risk_config(no_entry_after="15:30"),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.reason == "buy_blocked_after_no_entry_time"
    assert decision.blocked_rules == ["no_entry_after"]


def test_buy_blocked_when_daily_loss_limit_breached() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY, symbol="MSFT"),
        portfolio(positions=[position(unrealized_pl=Decimal("-251"))]),
        risk_config(max_daily_loss=Decimal("250")),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.reason == "buy_blocked_daily_loss_limit"
    assert decision.blocked_rules == ["max_daily_loss"]


def test_buy_blocked_outside_refined_entry_hour() -> None:
    decision = RiskManager().approve(
        signal(
            TradeAction.BUY,
            timestamp=datetime(2026, 4, 24, 16, 0, tzinfo=UTC),
        ),
        portfolio(),
        risk_config(refined_entry_hours=["14", "15"]),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.reason == "buy_blocked_refined_time_filter"
    assert decision.blocked_rules == ["refined_time_filter"]


def test_sell_not_blocked_by_entry_window() -> None:
    decision = RiskManager().approve(
        signal(
            TradeAction.SELL,
            timestamp=datetime(2026, 4, 24, 20, 0, tzinfo=UTC),
        ),
        portfolio(positions=[position("AAPL", Decimal("10"))]),
        risk_config(),
    )

    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.reason == "sell_signal_approved"


def test_sell_approved_when_existing_long_position_exists() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.SELL),
        portfolio(positions=[position(quantity=Decimal("10"))]),
        risk_config(allow_shorting=False),
    )

    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.approved_quantity == Decimal("10")
    assert decision.blocked_rules == []


def test_sell_blocked_when_no_position_exists() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.SELL),
        portfolio(),
        risk_config(),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert "no_long_position" in decision.blocked_rules


def test_sell_blocked_when_position_quantity_is_zero() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.SELL),
        portfolio(positions=[position(quantity=Decimal("0"))]),
        risk_config(),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert "zero_position_quantity" in decision.blocked_rules


def test_sell_blocked_when_shorting_disabled_and_sell_exceeds_long_quantity() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.SELL, requested_quantity=Decimal("11")),
        portfolio(positions=[position(quantity=Decimal("10"))]),
        risk_config(allow_shorting=False),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert "shorting_disabled" in decision.blocked_rules
    assert "sell_would_create_short" in decision.blocked_rules


def test_risk_decision_includes_clear_blocked_rules() -> None:
    decision = RiskManager().approve(
        signal(TradeAction.BUY, symbol="NVDA"),
        portfolio(
            buying_power=Decimal("100"),
            positions=[position("AAPL"), position("MSFT")],
        ),
        risk_config(max_position_value=Decimal("1000"), max_open_positions=2),
    )

    assert decision.status == RiskDecisionStatus.BLOCKED
    assert decision.blocked_rules == [
        "max_open_positions_reached",
        "insufficient_buying_power",
    ]
