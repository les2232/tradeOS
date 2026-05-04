from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradeos.config.schema import StrategyConfig
from tradeos.domain.enums import DataQualityStatus, StrategyMode, TradeAction
from tradeos.domain.models import Bar, Position
from tradeos.strategy.momentum_breakout import MomentumBreakoutStrategy


def config(
    *,
    breakout_sma_bars: int = 3,
    breakout_threshold_pct: Decimal = Decimal("0.01"),
    momentum_exit_threshold_pct: Decimal = Decimal("0.001"),
    momentum_stop_loss_pct: Decimal = Decimal("0.01"),
    require_positive_sma_slope: bool = True,
) -> StrategyConfig:
    return StrategyConfig(
        strategy_mode=StrategyMode.MOMENTUM_BREAKOUT,
        sma_bars=20,
        entry_threshold_pct=Decimal("0.01"),
        exit_threshold_pct=Decimal("0.01"),
        max_atr_percentile=Decimal("80"),
        breakout_sma_bars=breakout_sma_bars,
        breakout_threshold_pct=breakout_threshold_pct,
        momentum_exit_threshold_pct=momentum_exit_threshold_pct,
        momentum_stop_loss_pct=momentum_stop_loss_pct,
        require_positive_sma_slope=require_positive_sma_slope,
    )


def bar(close: Decimal = Decimal("105"), data_quality: DataQualityStatus = DataQualityStatus.OK) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 14, 30, tzinfo=UTC),
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        timeframe="15Min",
        data_quality=data_quality,
    )


def history(closes: list[Decimal]) -> list[Bar]:
    start = datetime(2026, 4, 24, 13, 0, tzinfo=UTC)
    return [
        Bar(
            symbol="AAPL",
            timestamp=start + timedelta(minutes=15 * index),
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=Decimal("1000"),
            timeframe="15Min",
            data_quality=DataQualityStatus.OK,
        )
        for index, close in enumerate(closes)
    ]


def position(avg_entry_price: Decimal = Decimal("100")) -> Position:
    return Position(
        symbol="AAPL",
        quantity=Decimal("1"),
        avg_entry_price=avg_entry_price,
        market_value=avg_entry_price,
        unrealized_pl=Decimal("0"),
        source="test",
    )


def test_insufficient_history_returns_hold() -> None:
    signal = MomentumBreakoutStrategy().generate_signal(
        bar(),
        config(breakout_sma_bars=3),
        history=history([Decimal("100"), Decimal("101"), Decimal("102")]),
    )

    assert signal.action == TradeAction.HOLD
    assert signal.reason == "insufficient_history_for_breakout_sma"


def test_buy_when_price_breaks_above_sma_and_slope_positive() -> None:
    signal = MomentumBreakoutStrategy().generate_signal(
        bar(close=Decimal("105")),
        config(breakout_sma_bars=3, breakout_threshold_pct=Decimal("0.01")),
        history=history([Decimal("99"), Decimal("100"), Decimal("101"), Decimal("102")]),
    )

    assert signal.action == TradeAction.BUY
    assert signal.reason == "momentum_breakout_entry"


def test_no_buy_when_slope_negative_and_required() -> None:
    signal = MomentumBreakoutStrategy().generate_signal(
        bar(close=Decimal("105")),
        config(breakout_sma_bars=3, breakout_threshold_pct=Decimal("0.01")),
        history=history([Decimal("105"), Decimal("104"), Decimal("103"), Decimal("102")]),
    )

    assert signal.action == TradeAction.HOLD
    assert signal.reason == "momentum_no_signal"


def test_sell_when_momentum_exit_threshold_crossed() -> None:
    signal = MomentumBreakoutStrategy().generate_signal(
        bar(close=Decimal("100")),
        config(breakout_sma_bars=3, momentum_exit_threshold_pct=Decimal("0.001")),
        history=history([Decimal("99"), Decimal("100"), Decimal("101"), Decimal("102")]),
        current_position=position(avg_entry_price=Decimal("95")),
    )

    assert signal.action == TradeAction.SELL
    assert signal.reason == "momentum_exit_threshold_crossed"


def test_sell_when_stop_loss_triggered() -> None:
    signal = MomentumBreakoutStrategy().generate_signal(
        bar(close=Decimal("98")),
        config(breakout_sma_bars=3, momentum_stop_loss_pct=Decimal("0.01")),
        history=history([Decimal("99"), Decimal("100"), Decimal("101"), Decimal("102")]),
        current_position=position(avg_entry_price=Decimal("100")),
    )

    assert signal.action == TradeAction.SELL
    assert signal.reason == "momentum_stop_loss_triggered"


def test_no_buy_when_already_holding() -> None:
    signal = MomentumBreakoutStrategy().generate_signal(
        bar(close=Decimal("105")),
        config(breakout_sma_bars=3, breakout_threshold_pct=Decimal("0.01")),
        history=history([Decimal("99"), Decimal("100"), Decimal("101"), Decimal("102")]),
        current_position=position(),
    )

    assert signal.action != TradeAction.BUY


def test_metadata_includes_sma_slope_deviation() -> None:
    signal = MomentumBreakoutStrategy().generate_signal(
        bar(close=Decimal("105")),
        config(breakout_sma_bars=3, breakout_threshold_pct=Decimal("0.01")),
        history=history([Decimal("99"), Decimal("100"), Decimal("101"), Decimal("102")]),
    )

    assert signal.metadata["sma"] == "101"
    assert signal.metadata["previous_sma"] == "100"
    assert signal.metadata["sma_slope"] == "1"
    assert signal.metadata["deviation_pct"] == str(Decimal("4") / Decimal("101"))
    assert signal.metadata["has_position"] == "false"


def test_non_ok_data_quality_returns_hold() -> None:
    signal = MomentumBreakoutStrategy().generate_signal(
        bar(data_quality=DataQualityStatus.STALE),
        config(),
        history=history([Decimal("99"), Decimal("100"), Decimal("101"), Decimal("102")]),
    )

    assert signal.action == TradeAction.HOLD
    assert signal.reason == "data_quality_stale_hold"
