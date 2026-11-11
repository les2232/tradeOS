from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradeos.config.schema import (
    BacktestCostConfig,
    BrokerConfig,
    DataConfig,
    RiskConfig,
    RuntimeConfig,
    StrategyConfig,
)
from tradeos.domain.enums import (
    DataQualityStatus,
    ExecutionMode,
    OrderSide,
    StrategyMode,
    TradeAction,
)
from tradeos.domain.models import Bar, OrderIntent, PortfolioState, Position, Signal
from tradeos.engine.cycle import TradingCycle
from tradeos.engine.trading_loop import PaperTradingLoop, TradingLoopSafetyError
from tradeos.execution.broker import FakeBroker
from tradeos.execution.reconciliation import ReconciliationService
from tradeos.risk.risk_manager import RiskManager


@dataclass(frozen=True)
class StaticSignalStrategy:
    name: str = "static"
    strategy_mode: StrategyMode = StrategyMode.MEAN_REVERSION
    action: TradeAction = TradeAction.HOLD

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
            reason="static_signal",
            confidence=Decimal("0.5"),
            metadata={},
        )


@dataclass
class CapturingStrategy:
    name: str = "capture"
    strategy_mode: StrategyMode = StrategyMode.MEAN_REVERSION
    histories: list[list[Bar] | None] = None

    def __post_init__(self) -> None:
        self.histories = []

    def generate_signal(
        self,
        bar: Bar,
        config: StrategyConfig,
        history: list[Bar] | None = None,
        current_position: Position | None = None,
    ) -> Signal:
        self.histories.append(history)
        return Signal(
            symbol=bar.symbol,
            action=TradeAction.HOLD,
            strategy_mode=self.strategy_mode,
            timestamp=bar.timestamp,
            reason="captured_history",
            confidence=Decimal("0"),
            metadata={},
        )


class StaticMarketData:
    def __init__(self, bars: list[Bar], warmup_bars: list[Bar] | None = None) -> None:
        self.bars = bars
        self.warmup_bars = warmup_bars or []
        self.requests: list[tuple[list[str], str]] = []
        self.warmup_requests: list[tuple[list[str], str, int]] = []

    def latest_bars(self, symbols: list[str], timeframe: str) -> list[Bar]:
        self.requests.append((symbols, timeframe))
        return list(self.bars)

    def recent_bars(self, symbols: list[str], timeframe: str, limit: int) -> list[Bar]:
        self.warmup_requests.append((symbols, timeframe, limit))
        return list(self.warmup_bars)


class SequentialMarketData(StaticMarketData):
    def __init__(self, batches: list[list[Bar]]) -> None:
        super().__init__([])
        self.batches = batches
        self.index = 0

    def latest_bars(self, symbols: list[str], timeframe: str) -> list[Bar]:
        self.requests.append((symbols, timeframe))
        batch = self.batches[min(self.index, len(self.batches) - 1)]
        self.index += 1
        return list(batch)


class OpenOrderBroker(FakeBroker):
    def get_open_orders(self) -> list[OrderIntent]:
        return [
            OrderIntent(
                symbol="AAPL",
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                order_type="limit",
                time_in_force="day",
                limit_price=Decimal("100"),
                reason="existing_order",
                metadata={},
            )
        ]


def bar(symbol: str = "AAPL", close: Decimal = Decimal("100")) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 4, 27, 14, 30, tzinfo=UTC),
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        timeframe="15Min",
        data_quality=DataQualityStatus.OK,
    )


def later_bar(symbol: str = "AAPL", close: Decimal = Decimal("101")) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 4, 27, 14, 45, tzinfo=UTC),
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        timeframe="15Min",
        data_quality=DataQualityStatus.OK,
    )


def much_later_bar(symbol: str = "NVDA", close: Decimal = Decimal("101")) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 4, 27, 15, 0, tzinfo=UTC),
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        timeframe="15Min",
        data_quality=DataQualityStatus.OK,
    )


def earlier_bar(
    symbol: str = "AAPL",
    close: Decimal = Decimal("99"),
    minute: int = 15,
) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 4, 27, 14, minute, tzinfo=UTC),
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
        timeframe="15Min",
        data_quality=DataQualityStatus.OK,
    )


def config(
    *,
    execution_mode: ExecutionMode = ExecutionMode.PAPER,
    broker_paper: bool = True,
    symbols: list[str] | None = None,
) -> RuntimeConfig:
    return RuntimeConfig(
        execution_mode=execution_mode,
        symbols=symbols or ["AAPL", "MSFT"],
        timeframe="15Min",
        strategy=StrategyConfig(
            strategy_mode=StrategyMode.MEAN_REVERSION,
            sma_bars=20,
            entry_threshold_pct=Decimal("1.0"),
            exit_threshold_pct=Decimal("0.3"),
            max_atr_percentile=Decimal("80"),
        ),
        risk=RiskConfig(
            max_position_value=Decimal("1000"),
            max_open_positions=3,
            max_daily_loss=Decimal("250"),
            no_entry_after="15:30",
            force_flatten_at="15:55",
        ),
        data=DataConfig(
            feed="iex",
            max_bar_age_seconds=120,
            require_same_session=True,
            alignment_mode="strict",
        ),
        broker=BrokerConfig(name="alpaca", paper=broker_paper),
        backtest_costs=BacktestCostConfig(),
    )


def account() -> PortfolioState:
    return PortfolioState(
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        buying_power=Decimal("10000"),
        positions=[],
        timestamp=datetime(2026, 4, 27, 14, 30, tzinfo=UTC),
    )


def cycle(strategy) -> TradingCycle:
    broker = FakeBroker(account())
    return TradingCycle(
        strategy=strategy,
        risk_manager=RiskManager(),
        broker=broker,
        reconciliation_service=ReconciliationService(),
        execute_orders=False,
    )


def test_paper_loop_runs_one_cycle_per_latest_bar() -> None:
    broker = FakeBroker(account())
    data = StaticMarketData([bar("AAPL"), bar("MSFT", Decimal("200"))])
    loop = PaperTradingLoop(
        config=config(),
        broker=broker,
        market_data=data,
        cycle=cycle(StaticSignalStrategy()),
    )

    result = loop.run_once()

    assert result.cycles_requested == 2
    assert result.bars_received == 2
    assert result.cycles_completed == 2
    assert result.warmup_bars_loaded == 0
    assert data.requests == [(["AAPL", "MSFT"], "15Min")]


def test_paper_loop_rejects_live_execution_mode() -> None:
    with pytest.raises(TradingLoopSafetyError, match="execution_mode: paper"):
        PaperTradingLoop(
            config=config(execution_mode=ExecutionMode.LIVE),
            broker=FakeBroker(account()),
            market_data=StaticMarketData([]),
            cycle=cycle(StaticSignalStrategy()),
        )


def test_paper_loop_rejects_non_paper_broker() -> None:
    with pytest.raises(TradingLoopSafetyError, match="broker.paper: true"):
        PaperTradingLoop(
            config=config(broker_paper=False),
            broker=FakeBroker(account()),
            market_data=StaticMarketData([]),
            cycle=cycle(StaticSignalStrategy()),
        )


def test_paper_loop_blocks_submission_when_open_orders_exist() -> None:
    broker = OpenOrderBroker(account())
    loop = PaperTradingLoop(
        config=config(),
        broker=broker,
        market_data=StaticMarketData([bar("AAPL")]),
        cycle=cycle(StaticSignalStrategy(action=TradeAction.BUY)),
        allow_order_submission=True,
    )

    with pytest.raises(TradingLoopSafetyError, match="open orders"):
        loop.run_once()


def test_paper_loop_blocks_submission_when_unmanaged_positions_exist() -> None:
    position = Position(
        symbol="COP",
        quantity=Decimal("8"),
        avg_entry_price=Decimal("119.13"),
        market_value=Decimal("953.04"),
        unrealized_pl=Decimal("-20.16"),
        source="alpaca_paper_short",
    )
    broker = FakeBroker(
        PortfolioState(
            cash=Decimal("10000"),
            equity=Decimal("10953.04"),
            buying_power=Decimal("10000"),
            positions=[position],
            timestamp=datetime(2026, 4, 27, 14, 30, tzinfo=UTC),
        )
    )
    loop = PaperTradingLoop(
        config=config(),
        broker=broker,
        market_data=StaticMarketData([bar("AAPL"), bar("MSFT")]),
        cycle=cycle(StaticSignalStrategy(action=TradeAction.BUY)),
        allow_order_submission=True,
    )

    with pytest.raises(
        TradingLoopSafetyError,
        match=r"paper order submission blocked by unmanaged positions: COP\(outside_config,short\)",
    ):
        loop.run_once()


def test_paper_loop_keeps_symbol_history_between_passes() -> None:
    strategy = CapturingStrategy()
    loop = PaperTradingLoop(
        config=config(symbols=["AAPL"]),
        broker=FakeBroker(account()),
        market_data=SequentialMarketData([[bar("AAPL")], [later_bar("AAPL")]]),
        cycle=cycle(strategy),
    )

    loop.run_once()
    loop.run_once()

    assert strategy.histories[0] == []
    assert strategy.histories[1] == [bar("AAPL")]


def test_paper_loop_loads_warmup_before_first_cycle() -> None:
    strategy = CapturingStrategy()
    warmup = [earlier_bar("AAPL"), earlier_bar("AAPL", close=Decimal("100"), minute=20)]
    data = StaticMarketData([bar("AAPL")], warmup_bars=warmup)
    loop = PaperTradingLoop(
        config=config(symbols=["AAPL"]),
        broker=FakeBroker(account()),
        market_data=data,
        cycle=cycle(strategy),
    )

    result = loop.run_once()

    assert result.warmup_bars_loaded == 2
    assert data.warmup_requests == [(["AAPL"], "15Min", 50)]
    assert strategy.histories[0] == warmup


def test_paper_loop_blocks_unaligned_latest_bars_in_strict_mode() -> None:
    loop = PaperTradingLoop(
        config=config(symbols=["AAPL"]),
        broker=FakeBroker(account()),
        market_data=StaticMarketData([bar("AAPL"), much_later_bar("MSFT")]),
        cycle=cycle(StaticSignalStrategy()),
    )

    with pytest.raises(TradingLoopSafetyError, match="unaligned latest bars"):
        loop.run_once()
