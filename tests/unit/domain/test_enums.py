from tradeos.domain.enums import (
    DataQualityStatus,
    ExecutionMode,
    OrderSide,
    OrderStatus,
    RiskDecisionStatus,
    StrategyMode,
    TradeAction,
)


def test_enum_string_values() -> None:
    assert TradeAction.BUY == "buy"
    assert TradeAction.SELL == "sell"
    assert TradeAction.HOLD == "hold"
    assert OrderSide.BUY == "buy"
    assert OrderSide.SELL == "sell"
    assert OrderStatus.PARTIALLY_FILLED == "partially_filled"
    assert StrategyMode.MEAN_REVERSION == "mean_reversion"
    assert RiskDecisionStatus.APPROVED == "approved"
    assert RiskDecisionStatus.BLOCKED == "blocked"
    assert ExecutionMode.DRY_RUN == "dry_run"
    assert DataQualityStatus.OK == "ok"
    assert DataQualityStatus.STALE == "stale"
    assert DataQualityStatus.MISSING == "missing"
    assert DataQualityStatus.INVALID == "invalid"
