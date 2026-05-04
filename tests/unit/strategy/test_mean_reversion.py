from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradeos.config.schema import StrategyConfig
from tradeos.domain.enums import DataQualityStatus, StrategyMode, TradeAction
from tradeos.domain.models import Bar
from tradeos.strategy.mean_reversion import MeanReversionStrategy


def strategy_config(
    entry_threshold_pct: Decimal = Decimal("0.01"),
    exit_threshold_pct: Decimal = Decimal("0.01"),
    sma_bars: int = 3,
    long_sma_bars: int = 3,
    trend_filter_enabled: bool = True,
    max_below_long_sma_pct: Decimal = Decimal("0.02"),
    stop_loss_enabled: bool = True,
    stop_loss_pct: Decimal = Decimal("0.01"),
) -> StrategyConfig:
    return StrategyConfig(
        strategy_mode=StrategyMode.MEAN_REVERSION,
        sma_bars=sma_bars,
        long_sma_bars=long_sma_bars,
        entry_threshold_pct=entry_threshold_pct,
        exit_threshold_pct=exit_threshold_pct,
        trend_filter_enabled=trend_filter_enabled,
        max_below_long_sma_pct=max_below_long_sma_pct,
        stop_loss_enabled=stop_loss_enabled,
        stop_loss_pct=stop_loss_pct,
        max_atr_percentile=Decimal("80"),
    )


def bar(
    data_quality: DataQualityStatus = DataQualityStatus.OK,
    open_price: Decimal = Decimal("100.00"),
    close_price: Decimal = Decimal("100.50"),
    timestamp: datetime = datetime(2026, 4, 24, 14, 30, tzinfo=UTC),
) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=timestamp,
        open=open_price,
        high=Decimal("101.00"),
        low=Decimal("99.50"),
        close=close_price,
        volume=Decimal("1000"),
        timeframe="15Min",
        data_quality=data_quality,
    )


def history_from_closes(closes: list[Decimal]) -> list[Bar]:
    start = datetime(2026, 4, 24, 13, 0, tzinfo=UTC)
    return [
        bar(
            open_price=close,
            close_price=close,
            timestamp=start + timedelta(minutes=15 * index),
        )
        for index, close in enumerate(closes)
    ]


def position(avg_entry_price: Decimal = Decimal("100"), quantity: Decimal = Decimal("1")):
    from tradeos.domain.models import Position

    return Position(
        symbol="AAPL",
        quantity=quantity,
        avg_entry_price=avg_entry_price,
        market_value=quantity * avg_entry_price,
        unrealized_pl=Decimal("0"),
        source="test",
    )


def test_mean_reversion_strategy_identity() -> None:
    strategy = MeanReversionStrategy()

    assert strategy.name == "mean_reversion"
    assert strategy.strategy_mode == StrategyMode.MEAN_REVERSION


def test_close_below_entry_threshold_returns_buy() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(open_price=Decimal("50"), close_price=Decimal("98.99")),
        strategy_config(entry_threshold_pct=Decimal("0.01")),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
    )

    assert signal.action == TradeAction.BUY
    assert signal.reason == "mean_reversion_entry_threshold_crossed"


def test_close_above_exit_threshold_returns_sell() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(open_price=Decimal("50"), close_price=Decimal("101.00")),
        strategy_config(exit_threshold_pct=Decimal("0.01")),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
    )

    assert signal.action == TradeAction.SELL
    assert signal.reason == "mean_reversion_exit_threshold_crossed"


def test_close_inside_thresholds_returns_hold() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(open_price=Decimal("50"), close_price=Decimal("100.50")),
        strategy_config(
            entry_threshold_pct=Decimal("0.01"),
            exit_threshold_pct=Decimal("0.01"),
        ),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
    )

    assert signal.action == TradeAction.HOLD
    assert signal.reason == "mean_reversion_no_signal"


@pytest.mark.parametrize(
    ("data_quality", "expected_reason"),
    [
        (DataQualityStatus.INVALID, "data_quality_invalid_hold"),
        (DataQualityStatus.STALE, "data_quality_stale_hold"),
        (DataQualityStatus.MISSING, "data_quality_missing_hold"),
    ],
)
def test_invalid_stale_missing_bar_returns_hold_with_reason(
    data_quality: DataQualityStatus,
    expected_reason: str,
) -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(data_quality),
        strategy_config(),
    )

    assert signal.action == TradeAction.HOLD
    assert signal.reason == expected_reason


def test_generated_signal_uses_bar_symbol_and_timestamp() -> None:
    input_bar = bar()
    signal = MeanReversionStrategy().generate_signal(
        input_bar,
        strategy_config(),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
    )

    assert signal.symbol == input_bar.symbol
    assert signal.timestamp == input_bar.timestamp


def test_generated_signal_confidence_is_within_range() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(),
        strategy_config(),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
    )

    assert Decimal("0") <= signal.confidence <= Decimal("1")


def test_metadata_includes_deviation_pct() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(open_price=Decimal("50"), close_price=Decimal("99")),
        strategy_config(),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
    )

    assert signal.metadata["reference_price"] == "100"
    assert signal.metadata["sma"] == "100"
    assert signal.metadata["deviation_pct"] == "-0.01"
    assert signal.metadata["entry_threshold_pct"] == "0.01"
    assert signal.metadata["exit_threshold_pct"] == "0.01"
    assert signal.metadata["sma_bars"] == "3"
    assert signal.metadata["history_count"] == "3"


def test_zero_sma_reference_safely_returns_hold() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(open_price=Decimal("100"), close_price=Decimal("100")),
        strategy_config(),
        history=history_from_closes([Decimal("0"), Decimal("0"), Decimal("0")]),
    )

    assert signal.action == TradeAction.HOLD
    assert signal.reason == "invalid_sma_reference"


def test_insufficient_history_returns_hold() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("99")),
        strategy_config(sma_bars=3),
        history=history_from_closes([Decimal("100"), Decimal("100")]),
    )

    assert signal.action == TradeAction.HOLD
    assert signal.reason == "insufficient_history_for_sma"
    assert signal.metadata["history_count"] == "2"


def test_exact_enough_history_calculates_sma() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("100")),
        strategy_config(sma_bars=3),
        history=history_from_closes([Decimal("99"), Decimal("100"), Decimal("101")]),
    )

    assert signal.reason == "mean_reversion_no_signal"
    assert signal.metadata["sma"] == "100"
    assert signal.metadata["history_count"] == "3"


def test_current_bar_is_not_required_in_history() -> None:
    current_bar = bar(close_price=Decimal("98.50"))
    signal = MeanReversionStrategy().generate_signal(
        current_bar,
        strategy_config(sma_bars=2, long_sma_bars=2, entry_threshold_pct=Decimal("0.01")),
        history=history_from_closes([Decimal("100"), Decimal("100")]),
    )

    assert all(item.timestamp != current_bar.timestamp for item in history_from_closes([Decimal("100"), Decimal("100")]))
    assert signal.action == TradeAction.BUY
    assert signal.metadata["sma"] == "100"


def test_buy_allowed_when_price_near_long_sma() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("98.50")),
        strategy_config(
            sma_bars=3,
            long_sma_bars=5,
            entry_threshold_pct=Decimal("0.01"),
            max_below_long_sma_pct=Decimal("0.02"),
        ),
        history=history_from_closes(
            [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100")]
        ),
    )

    assert signal.action == TradeAction.BUY
    assert signal.reason == "mean_reversion_entry_threshold_crossed"
    assert signal.metadata["long_sma"] == "100"


def test_buy_blocked_when_price_too_far_below_long_sma() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("97.50")),
        strategy_config(
            sma_bars=3,
            long_sma_bars=5,
            entry_threshold_pct=Decimal("0.01"),
            max_below_long_sma_pct=Decimal("0.02"),
        ),
        history=history_from_closes(
            [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100")]
        ),
    )

    assert signal.action == TradeAction.HOLD
    assert signal.reason == "trend_filter_rejected_buy"
    assert signal.metadata["long_sma"] == "100"


def test_insufficient_long_sma_history_blocks_buy_safely() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("98.50")),
        strategy_config(sma_bars=3, long_sma_bars=5, entry_threshold_pct=Decimal("0.01")),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
    )

    assert signal.action == TradeAction.HOLD
    assert signal.reason == "trend_filter_rejected_buy"


def test_sell_is_not_blocked_by_trend_filter() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("101.50")),
        strategy_config(sma_bars=3, long_sma_bars=5, exit_threshold_pct=Decimal("0.01")),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
    )

    assert signal.action == TradeAction.SELL
    assert signal.reason == "mean_reversion_exit_threshold_crossed"


def test_metadata_includes_long_sma_trend_filter_fields() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("100.50")),
        strategy_config(sma_bars=3, long_sma_bars=5),
        history=history_from_closes(
            [Decimal("99"), Decimal("100"), Decimal("101"), Decimal("100"), Decimal("100")]
        ),
    )

    assert signal.metadata["long_sma"] == "100"
    assert signal.metadata["trend_filter_enabled"] == "true"
    assert signal.metadata["max_below_long_sma_pct"] == "0.02"


def test_stop_loss_returns_sell_when_drawdown_exceeds_stop_loss_pct() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("98.50")),
        strategy_config(sma_bars=3, long_sma_bars=3, stop_loss_pct=Decimal("0.01")),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
        current_position=position(avg_entry_price=Decimal("100")),
    )

    assert signal.action == TradeAction.SELL
    assert signal.reason == "stop_loss_triggered"


def test_stop_loss_does_not_trigger_when_loss_is_smaller() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("99.50")),
        strategy_config(sma_bars=3, long_sma_bars=3, stop_loss_pct=Decimal("0.01")),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
        current_position=position(avg_entry_price=Decimal("100")),
    )

    assert signal.reason != "stop_loss_triggered"


def test_stop_loss_disabled_does_not_trigger() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("98.50")),
        strategy_config(
            sma_bars=3,
            long_sma_bars=3,
            stop_loss_enabled=False,
            stop_loss_pct=Decimal("0.01"),
        ),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
        current_position=position(avg_entry_price=Decimal("100")),
    )

    assert signal.reason != "stop_loss_triggered"


def test_metadata_includes_position_return_stop_loss_fields() -> None:
    signal = MeanReversionStrategy().generate_signal(
        bar(close_price=Decimal("99.50")),
        strategy_config(sma_bars=3, long_sma_bars=3, stop_loss_pct=Decimal("0.01")),
        history=history_from_closes([Decimal("100"), Decimal("100"), Decimal("100")]),
        current_position=position(avg_entry_price=Decimal("100")),
    )

    assert signal.metadata["position_return"] == "-0.005"
    assert signal.metadata["stop_loss_pct"] == "0.01"
    assert signal.metadata["stop_loss_enabled"] == "true"
