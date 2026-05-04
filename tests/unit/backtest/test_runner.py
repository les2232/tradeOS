from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tradeos.backtest.runner import BacktestRunner
from tradeos.config.schema import BacktestCostConfig, RiskConfig, StrategyConfig
from tradeos.domain.enums import DataQualityStatus, StrategyMode, TradeAction
from tradeos.domain.models import Bar, PortfolioState, Position, Signal
from tradeos.execution.broker import FakeBroker
from tradeos.execution.reconciliation import ReconciliationService
from tradeos.research.experiment import ExperimentSpec, make_experiment_id
from tradeos.risk.risk_manager import RiskManager
from tradeos.strategy.mean_reversion import MeanReversionStrategy


@dataclass(frozen=True)
class StaticSignalStrategy:
    name: str
    strategy_mode: StrategyMode
    action: TradeAction

    def generate_signal(
        self,
        bar: Bar,
        config: StrategyConfig,
        history: list[Bar] | None = None,
        current_position: Position | None = None,
    ) -> Signal:
        return Signal(
            symbol=bar.symbol,
            action=self.action,
            strategy_mode=self.strategy_mode,
            timestamp=bar.timestamp,
            reason=f"{self.action.value}_test_signal",
            confidence=Decimal("0.5"),
            metadata={},
        )


@dataclass
class SequenceSignalStrategy:
    name: str
    strategy_mode: StrategyMode
    actions: list[TradeAction]

    def generate_signal(
        self,
        bar: Bar,
        config: StrategyConfig,
        history: list[Bar] | None = None,
        current_position: Position | None = None,
    ) -> Signal:
        action = self.actions.pop(0)
        return Signal(
            symbol=bar.symbol,
            action=action,
            strategy_mode=self.strategy_mode,
            timestamp=bar.timestamp,
            reason=f"{action.value}_test_signal",
            confidence=Decimal("0.5"),
            metadata={},
        )


def bars(count: int = 3) -> list[Bar]:
    start = datetime(2026, 4, 25, 14, 30, tzinfo=UTC)
    return [
        Bar(
            symbol="AAPL",
            timestamp=start + timedelta(minutes=15 * index),
            open=Decimal("100") + Decimal(index),
            high=Decimal("101") + Decimal(index),
            low=Decimal("99") + Decimal(index),
            close=Decimal("100") + Decimal(index),
            volume=Decimal("1000"),
            timeframe="15Min",
            data_quality=DataQualityStatus.OK,
        )
        for index in range(count)
    ]


def bars_from_prices(prices: list[Decimal]) -> list[Bar]:
    start = datetime(2026, 4, 25, 14, 30, tzinfo=UTC)
    return [
        Bar(
            symbol="AAPL",
            timestamp=start + timedelta(minutes=15 * index),
            open=price,
            high=price + Decimal("1"),
            low=price - Decimal("1"),
            close=price,
            volume=Decimal("1000"),
            timeframe="15Min",
            data_quality=DataQualityStatus.OK,
        )
        for index, price in enumerate(prices)
    ]


def multi_symbol_bars(symbol_prices: list[tuple[str, Decimal]]) -> list[Bar]:
    start = datetime(2026, 4, 25, 14, 30, tzinfo=UTC)
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(minutes=15 * index),
            open=price,
            high=price + Decimal("1"),
            low=price - Decimal("1"),
            close=price,
            volume=Decimal("1000"),
            timeframe="15Min",
            data_quality=DataQualityStatus.OK,
        )
        for index, (symbol, price) in enumerate(symbol_prices)
    ]


def strategy_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_mode=StrategyMode.MEAN_REVERSION,
        sma_bars=20,
        entry_threshold_pct=Decimal("1.0"),
        exit_threshold_pct=Decimal("0.3"),
        trend_filter_enabled=True,
        max_atr_percentile=Decimal("80"),
    )


def sma_strategy_config(
    *,
    sma_bars: int = 2,
    entry_threshold_pct: Decimal = Decimal("0.01"),
    exit_threshold_pct: Decimal = Decimal("0.01"),
) -> StrategyConfig:
    return StrategyConfig(
        strategy_mode=StrategyMode.MEAN_REVERSION,
        sma_bars=sma_bars,
        long_sma_bars=sma_bars,
        entry_threshold_pct=entry_threshold_pct,
        exit_threshold_pct=exit_threshold_pct,
        trend_filter_enabled=True,
        max_below_long_sma_pct=Decimal("0.02"),
        max_atr_percentile=Decimal("80"),
    )


def risk_config(max_position_value: Decimal = Decimal("1")) -> RiskConfig:
    return RiskConfig(
        max_position_value=max_position_value,
        max_open_positions=3,
        max_daily_loss=Decimal("250"),
        allow_shorting=False,
        no_entry_after="15:30",
        force_flatten_at="15:55",
    )


def account() -> PortfolioState:
    return PortfolioState(
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        buying_power=Decimal("10000"),
        positions=[],
        timestamp=datetime(2026, 4, 25, 14, 30, tzinfo=UTC),
    )


def position(symbol: str = "AAPL", quantity: Decimal = Decimal("1")) -> Position:
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=Decimal("100"),
        market_value=quantity * Decimal("100"),
        unrealized_pl=Decimal("0"),
        source="test",
    )


def runner(strategy) -> BacktestRunner:
    return BacktestRunner(
        strategy=strategy,
        risk_manager=RiskManager(),
        broker=FakeBroker(account()),
        reconciliation_service=ReconciliationService(),
    )


def experiment_spec() -> ExperimentSpec:
    return ExperimentSpec(
        strategy_mode=StrategyMode.MEAN_REVERSION,
        symbols=["AAPL"],
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
        timeframe="15Min",
        parameters={"entry_threshold_pct": "1.0"},
        dataset_id="unit-dataset",
    )


def test_runs_over_multiple_bars() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars(3),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.total_cycles == 3


def test_preserves_total_bars_and_total_cycles() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars(4),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.total_bars == 4
    assert result.total_cycles == 4


def test_counts_signals() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars(3),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert len(result.signals) == 3
    assert [signal.action for signal in result.signals] == [TradeAction.HOLD] * 3


def test_counts_signal_reasons() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars(3),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.signal_reason_counts == {"insufficient_history_for_sma": 3}


def test_counts_risk_reasons() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars(3),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.risk_reason_counts == {"hold_signal_no_order": 3}


def test_counts_order_intents_when_strategy_approves_later() -> None:
    result = runner(
        StaticSignalStrategy(
            name="static",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            action=TradeAction.BUY,
        )
    ).run(
        multi_symbol_bars([("AAPL", Decimal("100")), ("MSFT", Decimal("101"))]),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert len(result.orders_created) == 2
    assert result.fills == []


def test_returns_event_counts() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars(2),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.event_counts["cycle_started"] == 2
    assert result.event_counts["signal_generated"] == 2
    assert result.event_counts["cycle_completed"] == 2


def test_returns_latest_cycle_summary() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars(2),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.latest_cycle is not None
    assert result.latest_cycle.symbol == "AAPL"
    assert result.latest_cycle.signal_action == "hold"
    assert result.latest_cycle.cycle_reason == "hold_signal_no_order"


def test_counts_buy_sell_hold_signals() -> None:
    buy_result = runner(
        StaticSignalStrategy(
            name="static",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            action=TradeAction.BUY,
        )
    ).run(bars(2), strategy_config(), risk_config(), local_positions=[])
    sell_result = runner(
        StaticSignalStrategy(
            name="static",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            action=TradeAction.SELL,
        )
    ).run(
        bars(1),
        strategy_config(),
        risk_config(),
        local_positions=[position()],
    )
    hold_result = runner(MeanReversionStrategy()).run(
        bars(3),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert buy_result.buy_signals == 2
    assert sell_result.sell_signals == 1
    assert hold_result.hold_signals == 3


def test_counts_blocked_vs_approved_risk_decisions() -> None:
    blocked = runner(MeanReversionStrategy()).run(
        bars(2),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )
    approved = runner(
        StaticSignalStrategy(
            name="static",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            action=TradeAction.BUY,
        )
    ).run(
        multi_symbol_bars([("AAPL", Decimal("100")), ("MSFT", Decimal("101"))]),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert blocked.blocked_risk_decisions == 2
    assert blocked.approved_risk_decisions == 0
    assert approved.blocked_risk_decisions == 0
    assert approved.approved_risk_decisions == 2


def test_counts_dirty_reconciliation_results() -> None:
    dirty_runner = BacktestRunner(
        strategy=StaticSignalStrategy(
            name="static",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            action=TradeAction.BUY,
        ),
        risk_manager=RiskManager(),
        broker=FakeBroker(account()),
        reconciliation_service=ReconciliationService(),
    )

    result = dirty_runner.run(
        bars(2),
        strategy_config(),
        risk_config(),
        local_positions=[position("AAPL", Decimal("1"))],
    )

    assert result.dirty_reconciliation_count == 2


def test_counts_order_intents_created() -> None:
    result = runner(
        StaticSignalStrategy(
            name="static",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            action=TradeAction.BUY,
        )
    ).run(bars(2), strategy_config(), risk_config(), local_positions=[])

    assert result.order_intents_created == 1
    assert result.risk_reason_counts["buy_blocked_existing_position"] == 1


def test_buy_then_sell_produces_positive_pnl_when_price_increases() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.realized_pnl == Decimal("10")
    assert result.unrealized_pnl == Decimal("0")
    assert result.total_pnl == Decimal("10")


def test_closed_trade_detail_created_on_buy_then_sell() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert len(result.closed_trade_details) == 1
    trade = result.closed_trade_details[0]
    assert trade.symbol == "AAPL"
    assert trade.entry_price == Decimal("100")
    assert trade.exit_price == Decimal("110")
    assert trade.quantity == Decimal("1")
    assert trade.pnl == Decimal("10")
    assert trade.entry_reason == "buy_test_signal"
    assert trade.exit_reason == "sell_test_signal"


def test_buy_then_sell_produces_negative_pnl_when_price_decreases() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("90")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.realized_pnl == Decimal("-10")
    assert result.unrealized_pnl == Decimal("0")
    assert result.total_pnl == Decimal("-10")


def test_losing_trade_records_negative_pnl() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("90")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.closed_trade_details[0].pnl == Decimal("-10")


def test_closed_trade_bars_held_is_tracked() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("105"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.closed_trade_details[0].bars_held == 2


def test_closed_trade_tracks_max_favorable_and_adverse_excursion() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD, TradeAction.HOLD, TradeAction.SELL],
        )
    ).run(
        bars_from_prices(
            [
                Decimal("100"),
                Decimal("95"),
                Decimal("108"),
                Decimal("102"),
            ]
        ),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    trade = result.closed_trade_details[0]
    assert trade.max_favorable_excursion == Decimal("8")
    assert trade.max_adverse_excursion == Decimal("-5")


def test_attribution_by_symbol_is_calculated() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[
                TradeAction.BUY,
                TradeAction.SELL,
                TradeAction.BUY,
                TradeAction.SELL,
            ],
        )
    ).run(
        multi_symbol_bars(
            [
                ("AAPL", Decimal("100")),
                ("AAPL", Decimal("110")),
                ("MSFT", Decimal("200")),
                ("MSFT", Decimal("190")),
            ]
        ),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.pnl_by_symbol["AAPL"] == Decimal("10")
    assert result.pnl_by_symbol["MSFT"] == Decimal("-5.0")
    assert result.closed_trades_by_symbol == {"AAPL": 1, "MSFT": 1}


def test_attribution_by_entry_hour_is_calculated() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.pnl_by_entry_hour == {"14:00": Decimal("10")}
    assert result.closed_trades_by_entry_hour == {"14:00": 1}


def test_attribution_by_exit_reason_is_calculated() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.pnl_by_exit_reason == {"sell_test_signal": Decimal("10")}
    assert result.closed_trades_by_exit_reason == {"sell_test_signal": 1}


def test_closed_trade_attribution_sums_to_realized_pnl() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.closed_trade_pnl_total == result.realized_pnl
    assert result.attribution_pnl_total == result.closed_trade_pnl_total
    assert sum(result.pnl_by_symbol.values(), Decimal("0")) == result.closed_trade_pnl_total
    assert sum(result.pnl_by_exit_reason.values(), Decimal("0")) == result.closed_trade_pnl_total


def test_total_pnl_can_differ_from_closed_trade_attribution_with_unrealized_pnl() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("120")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.closed_trade_pnl_total == Decimal("0")
    assert result.attribution_pnl_total == Decimal("0")
    assert result.unrealized_pnl == Decimal("20")
    assert result.total_pnl == Decimal("20")
    assert result.unrealized_pnl_explains_total_difference is True


def test_open_position_is_closed_at_end_when_forced_flatten_enabled() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("120")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
        force_flatten_end=True,
    )

    assert result.positions_end == []
    assert result.forced_flatten_trades == 1
    assert result.closed_trade_details[-1].exit_reason == "forced_end_of_window_flatten"


def test_realized_pnl_includes_forced_close() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("120")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
        force_flatten_end=True,
    )

    assert result.realized_pnl == Decimal("20")
    assert result.total_pnl == Decimal("20")


def test_attribution_includes_forced_close() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("120")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
        force_flatten_end=True,
    )

    assert result.pnl_by_exit_reason["forced_end_of_window_flatten"] == Decimal("20")
    assert result.closed_trades_by_exit_reason["forced_end_of_window_flatten"] == 1
    assert result.closed_trade_pnl_total == result.realized_pnl
    assert result.attribution_pnl_total == result.closed_trade_pnl_total


def test_no_unrealized_pnl_remains_after_forced_flatten() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("120")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
        force_flatten_end=True,
    )

    assert result.unrealized_pnl == Decimal("0")
    assert result.unrealized_pnl_explains_total_difference is False


def test_existing_unrealized_behavior_stays_when_forced_flatten_disabled() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("120")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
        force_flatten_end=False,
    )

    assert len(result.positions_end) == 1
    assert result.realized_pnl == Decimal("0")
    assert result.unrealized_pnl == Decimal("20")
    assert result.forced_flatten_trades == 0


def test_positive_total_negative_closed_trade_attribution_explained_by_unrealized_pnl() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[
                TradeAction.BUY,
                TradeAction.SELL,
                TradeAction.BUY,
                TradeAction.HOLD,
            ],
        )
    ).run(
        bars_from_prices(
            [
                Decimal("100"),
                Decimal("90"),
                Decimal("50"),
                Decimal("70"),
            ]
        ),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.closed_trade_pnl_total == Decimal("-10")
    assert result.attribution_pnl_total == Decimal("-10")
    assert result.unrealized_pnl == Decimal("40")
    assert result.total_pnl == Decimal("30")
    assert result.unrealized_pnl_explains_total_difference is True


def test_multiple_buys_update_average_entry_price_correctly() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.BUY],
        )
    ).run(
        bars_from_prices([Decimal("60"), Decimal("120")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("120")),
        local_positions=[],
    )

    assert len(result.positions_end) == 1
    assert result.positions_end[0].quantity == Decimal("2")
    assert result.positions_end[0].avg_entry_price == Decimal("60")
    assert result.risk_reason_counts["buy_blocked_existing_position"] == 1


def test_unrealized_pnl_is_calculated_at_end() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("125")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.realized_pnl == Decimal("0")
    assert result.unrealized_pnl == Decimal("25")


def test_total_pnl_equals_realized_plus_unrealized() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("125")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.total_pnl == result.realized_pnl + result.unrealized_pnl


def test_empty_positions_produce_zero_pnl() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars(2),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.positions_end == []
    assert result.realized_pnl == Decimal("0")
    assert result.unrealized_pnl == Decimal("0")
    assert result.total_pnl == Decimal("0")


def test_winning_trade_counted_correctly() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.closed_trades == 1
    assert result.winning_trades == 1
    assert result.losing_trades == 0
    assert result.gross_profit == Decimal("10")
    assert result.gross_loss == Decimal("0")


def test_losing_trade_counted_correctly() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("90")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.closed_trades == 1
    assert result.winning_trades == 0
    assert result.losing_trades == 1
    assert result.gross_profit == Decimal("0")
    assert result.gross_loss == Decimal("10")


def test_win_rate_calculated() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL, TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110"), Decimal("100"), Decimal("90")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.closed_trades == 2
    assert result.win_rate == Decimal("0.5")


def test_profit_factor_calculated() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL, TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("120"), Decimal("100"), Decimal("90")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.gross_profit == Decimal("20")
    assert result.gross_loss == Decimal("10")
    assert result.profit_factor == Decimal("2")


def test_expectancy_calculated() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL, TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("120"), Decimal("100"), Decimal("90")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.realized_pnl == Decimal("10")
    assert result.expectancy == Decimal("5")


def test_no_closed_trades_gives_safe_zeros() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars(2),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.closed_trades == 0
    assert result.winning_trades == 0
    assert result.losing_trades == 0
    assert result.win_rate == Decimal("0")
    assert result.gross_profit == Decimal("0")
    assert result.gross_loss == Decimal("0")
    assert result.profit_factor == Decimal("0")
    assert result.expectancy == Decimal("0")


def test_buy_slippage_worsens_entry_price() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY],
        )
    ).run(
        bars_from_prices([Decimal("100")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("101")),
        local_positions=[],
        backtest_costs=BacktestCostConfig(slippage_bps=Decimal("100")),
    )

    assert result.positions_end[0].avg_entry_price == Decimal("101.00")


def test_sell_slippage_worsens_exit_price() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("101")),
        local_positions=[],
        backtest_costs=BacktestCostConfig(slippage_bps=Decimal("100")),
    )

    assert result.realized_pnl == Decimal("7.9000")


def test_commission_reduces_realized_pnl() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
        backtest_costs=BacktestCostConfig(commission_per_trade=Decimal("1")),
    )

    assert result.realized_pnl == Decimal("8")


def test_total_costs_are_tracked() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
        backtest_costs=BacktestCostConfig(commission_per_trade=Decimal("1")),
    )

    assert result.total_costs == Decimal("2")


def test_converts_backtest_result_to_experiment_result() -> None:
    backtest_runner = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    )
    backtest_result = backtest_runner.run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    experiment_result = backtest_runner.to_experiment_result(
        backtest_result,
        experiment_spec(),
    )

    assert experiment_result.spec == experiment_spec()


def test_experiment_score_is_populated() -> None:
    backtest_runner = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    )
    backtest_result = backtest_runner.run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    experiment_result = backtest_runner.to_experiment_result(
        backtest_result,
        experiment_spec(),
    )

    assert experiment_result.score != Decimal("0")


def test_experiment_warnings_populated_for_low_trade_count() -> None:
    backtest_runner = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    )
    backtest_result = backtest_runner.run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    experiment_result = backtest_runner.to_experiment_result(
        backtest_result,
        experiment_spec(),
    )

    assert "low_trade_count" in experiment_result.warnings


def test_experiment_metrics_map_correctly() -> None:
    backtest_runner = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    )
    backtest_result = backtest_runner.run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    experiment_result = backtest_runner.to_experiment_result(
        backtest_result,
        experiment_spec(),
    )

    assert experiment_result.profit_factor == backtest_result.profit_factor
    assert experiment_result.max_drawdown == backtest_result.max_drawdown
    assert experiment_result.win_rate == backtest_result.win_rate
    assert experiment_result.num_trades == backtest_result.closed_trades
    assert experiment_result.expectancy == backtest_result.expectancy
    assert experiment_result.net_pnl == backtest_result.total_pnl


def test_experiment_id_comes_from_experiment_spec() -> None:
    backtest_runner = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.SELL],
        )
    )
    spec = experiment_spec()
    backtest_result = backtest_runner.run(
        bars_from_prices([Decimal("100"), Decimal("110")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    experiment_result = backtest_runner.to_experiment_result(backtest_result, spec)

    assert experiment_result.experiment_id == make_experiment_id(spec)


def test_equity_curve_has_one_value_per_bar() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110"), Decimal("120")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert len(result.equity_curve) == 3


def test_max_drawdown_zero_for_rising_equity() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("110"), Decimal("120")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.equity_curve == [Decimal("0"), Decimal("10"), Decimal("20")]
    assert result.max_drawdown == Decimal("0")


def test_max_drawdown_captures_peak_to_trough_decline() -> None:
    result = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD, TradeAction.HOLD],
        )
    ).run(
        bars_from_prices([Decimal("100"), Decimal("120"), Decimal("90")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.equity_curve == [Decimal("0"), Decimal("20"), Decimal("-10")]
    assert result.max_drawdown == Decimal("30")


def test_experiment_result_receives_max_drawdown() -> None:
    backtest_runner = runner(
        SequenceSignalStrategy(
            name="sequence",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            actions=[TradeAction.BUY, TradeAction.HOLD, TradeAction.HOLD],
        )
    )
    backtest_result = backtest_runner.run(
        bars_from_prices([Decimal("100"), Decimal("120"), Decimal("90")]),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    experiment_result = backtest_runner.to_experiment_result(
        backtest_result,
        experiment_spec(),
    )

    assert experiment_result.max_drawdown == Decimal("30")


def test_first_bars_hold_due_to_insufficient_history() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars_from_prices([Decimal("100"), Decimal("100"), Decimal("100")]),
        sma_strategy_config(sma_bars=2),
        risk_config(),
        local_positions=[],
    )

    assert [signal.action for signal in result.signals[:2]] == [
        TradeAction.HOLD,
        TradeAction.HOLD,
    ]
    assert [signal.reason for signal in result.signals[:2]] == [
        "insufficient_history_for_sma",
        "insufficient_history_for_sma",
    ]


def test_later_bars_can_generate_buy_sell_after_enough_history() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars_from_prices(
            [
                Decimal("100"),
                Decimal("100"),
                Decimal("98"),
                Decimal("102"),
            ]
        ),
        sma_strategy_config(sma_bars=2),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert [signal.action for signal in result.signals] == [
        TradeAction.HOLD,
        TradeAction.HOLD,
        TradeAction.BUY,
        TradeAction.SELL,
    ]


def test_backtest_stop_loss_uses_current_position_context() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars_from_prices(
            [
                Decimal("100"),
                Decimal("100"),
                Decimal("98.50"),
                Decimal("97"),
            ]
        ),
        sma_strategy_config(
            sma_bars=2,
            entry_threshold_pct=Decimal("0.01"),
            exit_threshold_pct=Decimal("0.5"),
        ),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.signals[2].action == TradeAction.BUY
    assert result.signals[3].action == TradeAction.SELL
    assert result.signals[3].reason == "stop_loss_triggered"
    assert result.closed_trades == 1


def test_backtest_history_does_not_include_current_bar_in_sma() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars_from_prices([Decimal("100"), Decimal("200")]),
        sma_strategy_config(sma_bars=1, exit_threshold_pct=Decimal("0.5")),
        risk_config(),
        local_positions=[],
    )

    assert result.signals[1].action == TradeAction.SELL
    assert result.signals[1].metadata["sma"] == "100"
    assert result.signals[1].metadata["history_count"] == "1"


def test_initial_history_prevents_cold_start_insufficient_sma() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars_from_prices([Decimal("98.50")]),
        sma_strategy_config(sma_bars=2, entry_threshold_pct=Decimal("0.01")),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
        initial_history={"AAPL": bars_from_prices([Decimal("100"), Decimal("100")])},
    )

    assert result.signals[0].reason != "insufficient_history_for_sma"
    assert result.signals[0].action == TradeAction.BUY
    assert result.signals[0].metadata["history_count"] == "2"


def test_initial_history_bars_not_counted_as_total_bars() -> None:
    result = runner(MeanReversionStrategy()).run(
        bars_from_prices([Decimal("100")]),
        sma_strategy_config(sma_bars=2),
        risk_config(),
        local_positions=[],
        initial_history={"AAPL": bars_from_prices([Decimal("100"), Decimal("100")])},
    )

    assert result.total_bars == 1
    assert result.total_cycles == 1
    assert len(result.equity_curve) == 1


def test_histories_are_separate_per_symbol() -> None:
    result = runner(MeanReversionStrategy()).run(
        multi_symbol_bars(
            [
                ("AAPL", Decimal("100")),
                ("MSFT", Decimal("200")),
                ("AAPL", Decimal("99")),
                ("MSFT", Decimal("198")),
            ]
        ),
        sma_strategy_config(sma_bars=1, entry_threshold_pct=Decimal("0.005")),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert [signal.reason for signal in result.signals[:2]] == [
        "insufficient_history_for_sma",
        "insufficient_history_for_sma",
    ]
    assert result.signals[2].action == TradeAction.BUY
    assert result.signals[2].metadata["sma"] == "100"
    assert result.signals[3].action == TradeAction.BUY
    assert result.signals[3].metadata["sma"] == "200"


def test_metrics_aggregate_across_symbols() -> None:
    result = runner(
        StaticSignalStrategy(
            name="static",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            action=TradeAction.BUY,
        )
    ).run(
        multi_symbol_bars(
            [
                ("AAPL", Decimal("100")),
                ("MSFT", Decimal("200")),
            ]
        ),
        strategy_config(),
        risk_config(max_position_value=Decimal("100")),
        local_positions=[],
    )

    assert result.total_bars == 2
    assert result.total_cycles == 2
    assert result.buy_signals == 2
    assert result.order_intents_created == 2
    assert {position.symbol for position in result.positions_end} == {"AAPL", "MSFT"}
