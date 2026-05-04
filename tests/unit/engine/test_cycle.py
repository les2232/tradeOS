from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from tradeos.config.schema import RiskConfig, StrategyConfig
from tradeos.domain.enums import (
    DataQualityStatus,
    OrderSide,
    OrderStatus,
    StrategyMode,
    TradeAction,
)
from tradeos.domain.models import Bar, OrderIntent, OrderSubmission, PortfolioState, Position, Signal
from tradeos.engine.cycle import TradingCycle
from tradeos.execution.broker import FakeBroker
from tradeos.execution.reconciliation import ReconciliationService
from tradeos.risk.risk_manager import RiskManager
from tradeos.storage.sqlite import SQLiteEventStore
from tradeos.strategy.mean_reversion import MeanReversionStrategy


@dataclass(frozen=True)
class StaticSignalStrategy:
    name: str
    strategy_mode: StrategyMode
    action: TradeAction
    symbol: str = "AAPL"

    def generate_signal(
        self,
        bar: Bar,
        config: StrategyConfig,
        history: list[Bar] | None = None,
        current_position: Position | None = None,
    ) -> Signal:
        return Signal(
            symbol=self.symbol,
            action=self.action,
            strategy_mode=self.strategy_mode,
            timestamp=bar.timestamp,
            reason=f"{self.action.value}_test_signal",
            confidence=Decimal("0.5"),
            metadata={},
        )


@dataclass
class HistoryCapturingStrategy:
    name: str = "history_capture"
    strategy_mode: StrategyMode = StrategyMode.MEAN_REVERSION
    captured_history: list[Bar] | None = None

    def generate_signal(
        self,
        bar: Bar,
        config: StrategyConfig,
        history: list[Bar] | None = None,
        current_position: Position | None = None,
    ) -> Signal:
        self.captured_history = history
        return Signal(
            symbol=bar.symbol,
            action=TradeAction.HOLD,
            strategy_mode=self.strategy_mode,
            timestamp=bar.timestamp,
            reason="captured_history",
            confidence=Decimal("0"),
            metadata={},
        )


class AcceptedOrderBroker(FakeBroker):
    def submit_order(self, intent: OrderIntent) -> OrderSubmission:
        return OrderSubmission(
            broker_order_id="accepted-order-000001",
            symbol=intent.symbol,
            side=intent.side,
            requested_quantity=intent.quantity,
            status=OrderStatus.SUBMITTED,
            submitted_at=datetime(2026, 4, 25, 14, 30, tzinfo=UTC),
            limit_price=intent.limit_price,
            fill=None,
            raw_payload={"status": "accepted", "accepted_quantity": str(intent.quantity)},
        )


def bar(symbol: str = "AAPL", close: Decimal = Decimal("100")) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 4, 25, 14, 30, tzinfo=UTC),
        open=Decimal("99"),
        high=Decimal("101"),
        low=Decimal("98"),
        close=close,
        volume=Decimal("1000"),
        timeframe="15Min",
        data_quality=DataQualityStatus.OK,
    )


def strategy_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_mode=StrategyMode.MEAN_REVERSION,
        sma_bars=20,
        entry_threshold_pct=Decimal("1.0"),
        exit_threshold_pct=Decimal("0.3"),
        trend_filter_enabled=True,
        max_atr_percentile=Decimal("80"),
    )


def risk_config(
    max_position_value: Decimal = Decimal("1"),
    *,
    max_daily_loss: Decimal = Decimal("250"),
    no_entry_after: str = "15:30",
    force_flatten_at: str = "15:55",
) -> RiskConfig:
    return RiskConfig(
        max_position_value=max_position_value,
        max_open_positions=3,
        max_daily_loss=max_daily_loss,
        allow_shorting=False,
        no_entry_after=no_entry_after,
        force_flatten_at=force_flatten_at,
    )


def position(
    symbol: str = "AAPL",
    quantity: Decimal = Decimal("2"),
    unrealized_pl: Decimal = Decimal("0"),
) -> Position:
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=Decimal("100"),
        market_value=quantity * Decimal("100"),
        unrealized_pl=unrealized_pl,
        source="test",
    )


def account(positions: list[Position] | None = None) -> PortfolioState:
    positions = positions or []
    position_value = sum(
        (item.market_value for item in positions),
        Decimal("0"),
    )
    cash = Decimal("10000")
    return PortfolioState(
        cash=cash,
        equity=cash + position_value,
        buying_power=cash,
        positions=positions,
        timestamp=datetime(2026, 4, 25, 14, 30, tzinfo=UTC),
    )


def cycle(
    action: TradeAction,
    *,
    broker: FakeBroker | None = None,
    execute_orders: bool = False,
    event_store: SQLiteEventStore | None = None,
) -> TradingCycle:
    return TradingCycle(
        strategy=StaticSignalStrategy(
            name="static",
            strategy_mode=StrategyMode.MEAN_REVERSION,
            action=action,
        ),
        risk_manager=RiskManager(),
        broker=broker or FakeBroker(account()),
        reconciliation_service=ReconciliationService(),
        execute_orders=execute_orders,
        event_store=event_store,
    )


def test_hold_signal_produces_no_order() -> None:
    result = TradingCycle(
        strategy=MeanReversionStrategy(),
        risk_manager=RiskManager(),
        broker=FakeBroker(account()),
        reconciliation_service=ReconciliationService(),
    ).run(bar(), strategy_config(), risk_config(), local_positions=[])

    assert result.signal.action == TradeAction.HOLD
    assert result.order_intent is None
    assert result.fill is None
    assert result.reason == "hold_signal_no_order"


def test_force_flatten_overrides_hold_signal_to_sell_existing_position() -> None:
    existing_position = position(quantity=Decimal("2"))
    broker = FakeBroker(account([existing_position]))

    result = TradingCycle(
        strategy=MeanReversionStrategy(),
        risk_manager=RiskManager(),
        broker=broker,
        reconciliation_service=ReconciliationService(),
    ).run(
        bar(),
        strategy_config(),
        risk_config(force_flatten_at="14:00", no_entry_after="13:59"),
        local_positions=[existing_position],
    )

    assert result.signal.action == TradeAction.SELL
    assert result.signal.reason == "force_flatten_at_triggered"
    assert result.order_intent is not None
    assert result.order_intent.side == OrderSide.SELL
    assert result.order_intent.quantity == Decimal("2")


def test_daily_loss_limit_overrides_hold_signal_to_sell_existing_position() -> None:
    existing_position = position(quantity=Decimal("2"), unrealized_pl=Decimal("-251"))
    broker = FakeBroker(account([existing_position]))

    result = TradingCycle(
        strategy=MeanReversionStrategy(),
        risk_manager=RiskManager(),
        broker=broker,
        reconciliation_service=ReconciliationService(),
    ).run(
        bar(),
        strategy_config(),
        risk_config(max_daily_loss=Decimal("250")),
        local_positions=[existing_position],
    )

    assert result.signal.action == TradeAction.SELL
    assert result.signal.reason == "max_daily_loss_triggered"
    assert result.order_intent is not None
    assert result.order_intent.side == OrderSide.SELL


def test_dirty_reconciliation_blocks_execution() -> None:
    broker = FakeBroker(account([position("AAPL", Decimal("1"))]))

    result = cycle(TradeAction.BUY, broker=broker, execute_orders=True).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.execution_allowed is False
    assert result.order_intent is None
    assert result.fill is None
    assert result.reason == "reconciliation_blocked_execution"


def test_risk_blocked_signal_produces_no_order() -> None:
    result = cycle(TradeAction.SELL).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.execution_allowed is False
    assert result.order_intent is None
    assert result.fill is None
    assert result.reason == "sell_signal_blocked_by_risk"


def test_execute_orders_false_produces_order_intent_but_no_fill_when_risk_approved() -> None:
    result = cycle(TradeAction.BUY, execute_orders=False).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.execution_allowed is True
    assert result.order_intent is not None
    assert result.order_submission is None
    assert result.fill is None
    assert result.reason == "dry_run_order_not_submitted"


def test_execute_orders_true_submits_order_and_returns_fill() -> None:
    broker = FakeBroker(account())

    result = cycle(TradeAction.BUY, broker=broker, execute_orders=True).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.order_intent is not None
    assert result.fill is not None
    assert result.fill.broker_order_id == "fake-order-000001"
    assert broker.fills == [result.fill]
    assert result.reason == "order_filled"


def test_accepted_zero_quantity_order_is_not_treated_as_fill() -> None:
    event_store = SQLiteEventStore(":memory:")
    broker = AcceptedOrderBroker(account())

    result = cycle(
        TradeAction.BUY,
        broker=broker,
        execute_orders=True,
        event_store=event_store,
    ).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.order_intent is not None
    assert result.fill is None
    assert result.reason == "order_submitted"
    event_types = [event.event_type for event in event_store.list_events()]
    assert "order_submitted" in event_types
    assert "fill_received" not in event_types
    completed = event_store.list_events()[-1]
    assert completed.payload["fill_received"] is False


def test_buy_uses_bar_close_as_limit_price() -> None:
    result = cycle(TradeAction.BUY).run(
        bar(close=Decimal("123.45")),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.order_intent is not None
    assert result.order_intent.side == OrderSide.BUY
    assert result.order_intent.limit_price == Decimal("123.45")


def test_sell_uses_bar_close_as_limit_price() -> None:
    existing_position = position(quantity=Decimal("2"))
    broker = FakeBroker(account([existing_position]))

    result = cycle(TradeAction.SELL, broker=broker).run(
        bar(close=Decimal("98.76")),
        strategy_config(),
        risk_config(),
        local_positions=[existing_position],
    )

    assert result.order_intent is not None
    assert result.order_intent.side == OrderSide.SELL
    assert result.order_intent.limit_price == Decimal("98.76")


def test_cycle_result_includes_clear_reason() -> None:
    result = cycle(TradeAction.BUY).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.reason == "dry_run_order_not_submitted"


def test_cycle_runs_without_event_store() -> None:
    result = cycle(TradeAction.BUY).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert result.reason == "dry_run_order_not_submitted"


def test_cycle_with_event_store_writes_events() -> None:
    event_store = SQLiteEventStore(":memory:")

    cycle(TradeAction.BUY, event_store=event_store).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    assert [event.event_type for event in event_store.list_events()] == [
        "cycle_started",
        "reconciliation_result",
        "signal_generated",
        "risk_decision",
        "order_intent_created",
        "cycle_completed",
    ]


def test_hold_cycle_writes_signal_risk_reconciliation_completed_events() -> None:
    event_store = SQLiteEventStore(":memory:")

    TradingCycle(
        strategy=MeanReversionStrategy(),
        risk_manager=RiskManager(),
        broker=FakeBroker(account()),
        reconciliation_service=ReconciliationService(),
        event_store=event_store,
    ).run(bar(), strategy_config(), risk_config(), local_positions=[])

    event_types = [event.event_type for event in event_store.list_events()]
    assert "signal_generated" in event_types
    assert "risk_decision" in event_types
    assert "reconciliation_result" in event_types
    assert "cycle_completed" in event_types
    assert "order_intent_created" not in event_types
    assert "fill_received" not in event_types


def test_approved_dry_run_writes_order_intent_created_but_not_fill_received() -> None:
    event_store = SQLiteEventStore(":memory:")

    cycle(TradeAction.BUY, event_store=event_store, execute_orders=False).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    event_types = [event.event_type for event in event_store.list_events()]
    assert "order_intent_created" in event_types
    assert "fill_received" not in event_types


def test_execute_orders_true_writes_fill_received() -> None:
    event_store = SQLiteEventStore(":memory:")

    cycle(TradeAction.BUY, event_store=event_store, execute_orders=True).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    event_types = [event.event_type for event in event_store.list_events()]
    assert "fill_received" in event_types


def test_event_payloads_include_useful_status_reason_data() -> None:
    event_store = SQLiteEventStore(":memory:")

    cycle(TradeAction.BUY, event_store=event_store).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
    )

    events = event_store.list_events()
    risk_event = next(event for event in events if event.event_type == "risk_decision")
    completed_event = next(event for event in events if event.event_type == "cycle_completed")

    assert risk_event.symbol == "AAPL"
    assert risk_event.payload["status"] == "approved"
    assert risk_event.payload["reason"] == "buy_signal_approved"
    assert completed_event.payload["reason"] == "dry_run_order_not_submitted"
    assert completed_event.payload["execution_allowed"] is True


def test_cycle_passes_history_into_strategy() -> None:
    strategy = HistoryCapturingStrategy()
    prior_history = [bar(close=Decimal("99")), bar(close=Decimal("100"))]

    TradingCycle(
        strategy=strategy,
        risk_manager=RiskManager(),
        broker=FakeBroker(account()),
        reconciliation_service=ReconciliationService(),
    ).run(
        bar(),
        strategy_config(),
        risk_config(),
        local_positions=[],
        history=prior_history,
    )

    assert strategy.captured_history == prior_history
