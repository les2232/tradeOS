"""Trading loop orchestration.

This module owns the first paper-safe live runtime boundary. It intentionally
keeps order submission behind explicit construction-time approval and reuses
the tested single-cycle engine for every bar.
"""

from dataclasses import dataclass, field
from typing import Protocol

from tradeos.config.schema import RuntimeConfig
from tradeos.domain.enums import ExecutionMode, StrategyMode
from tradeos.domain.models import Bar
from tradeos.engine.cycle import TradingCycle, TradingCycleResult
from tradeos.execution.broker import Broker


class MarketDataFeed(Protocol):
    """Live market data boundary used by the trading loop."""

    def latest_bars(self, symbols: list[str], timeframe: str) -> list[Bar]:
        """Return the latest validated bars for the requested symbols."""

    def recent_bars(
        self,
        symbols: list[str],
        timeframe: str,
        limit: int,
    ) -> list[Bar]:
        """Return recent validated bars for strategy warmup."""


class TradingLoopSafetyError(ValueError):
    """Raised when live-loop construction would violate runtime safety rules."""


@dataclass(frozen=True)
class PaperLiveLoopResult:
    """Summary of one paper live loop pass."""

    cycles_requested: int
    cycles_completed: int
    bars_received: int
    warmup_bars_loaded: int
    unmanaged_positions: list[str] = field(default_factory=list)
    results: list[TradingCycleResult] = field(default_factory=list)


class PaperTradingLoop:
    """Run live market bars through the tested TradingCycle in paper mode."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        broker: Broker,
        market_data: MarketDataFeed,
        cycle: TradingCycle,
        allow_order_submission: bool = False,
    ) -> None:
        _validate_paper_runtime(config, allow_order_submission)
        self.config = config
        self.broker = broker
        self.market_data = market_data
        self.cycle = cycle
        self.allow_order_submission = allow_order_submission
        self.history_by_symbol: dict[str, list[Bar]] = {}

    def run_once(self) -> PaperLiveLoopResult:
        """Run one bounded live-data pass for all configured symbols."""

        if self.allow_order_submission and self.broker.get_open_orders():
            raise TradingLoopSafetyError(
                "paper order submission blocked while broker has open orders"
            )

        bars = self.market_data.latest_bars(
            symbols=self.config.symbols,
            timeframe=self.config.timeframe,
        )
        _validate_latest_bars(bars, self.config)
        warmup_bars = self._load_missing_warmup(bars)
        broker_positions = self.broker.get_positions()
        unmanaged_positions = _unmanaged_positions(broker_positions, self.config)
        if self.allow_order_submission and unmanaged_positions:
            raise TradingLoopSafetyError(
                "paper order submission blocked by unmanaged positions: "
                + ", ".join(unmanaged_positions)
            )
        results: list[TradingCycleResult] = []

        for bar in bars:
            history = [
                history_bar
                for history_bar in self.history_by_symbol.get(bar.symbol, [])
                if history_bar.timestamp < bar.timestamp
            ]
            result = self.cycle.run(
                bar=bar,
                strategy_config=self.config.strategy,
                risk_config=self.config.risk,
                local_positions=broker_positions,
                history=history,
            )
            self.history_by_symbol.setdefault(bar.symbol, []).append(bar)
            results.append(result)

        return PaperLiveLoopResult(
            cycles_requested=len(self.config.symbols),
            cycles_completed=len(results),
            bars_received=len(bars),
            warmup_bars_loaded=warmup_bars,
            unmanaged_positions=unmanaged_positions,
            results=results,
        )

    def _load_missing_warmup(self, latest_bars: list[Bar]) -> int:
        missing_symbols = [
            bar.symbol
            for bar in latest_bars
            if not self.history_by_symbol.get(bar.symbol)
        ]
        if not missing_symbols:
            return 0

        required_history = _required_history_count(self.config)
        recent_bars = self.market_data.recent_bars(
            symbols=missing_symbols,
            timeframe=self.config.timeframe,
            limit=required_history,
        )
        latest_by_symbol = {bar.symbol: bar for bar in latest_bars}
        loaded = 0
        for recent_bar in sorted(recent_bars, key=lambda item: item.timestamp):
            latest_bar = latest_by_symbol.get(recent_bar.symbol)
            if latest_bar is not None and recent_bar.timestamp >= latest_bar.timestamp:
                continue
            self.history_by_symbol.setdefault(recent_bar.symbol, []).append(recent_bar)
            loaded += 1
        return loaded


def _validate_paper_runtime(
    config: RuntimeConfig,
    allow_order_submission: bool,
) -> None:
    if config.execution_mode != ExecutionMode.PAPER:
        raise TradingLoopSafetyError("--paper-live-once requires execution_mode: paper")
    if not config.broker.paper:
        raise TradingLoopSafetyError("--paper-live-once requires broker.paper: true")
    if allow_order_submission and config.broker.name.lower() != "alpaca":
        raise TradingLoopSafetyError("paper order submission currently requires broker.name: alpaca")


def _validate_latest_bars(bars: list[Bar], config: RuntimeConfig) -> None:
    if config.data.alignment_mode.lower() != "strict":
        return

    bars_by_symbol = {bar.symbol: bar for bar in bars}
    missing_symbols = [
        symbol for symbol in config.symbols if symbol not in bars_by_symbol
    ]
    if missing_symbols:
        raise TradingLoopSafetyError(
            "paper live loop blocked by missing latest bars: "
            + ", ".join(missing_symbols)
        )

    newest_timestamp = max((bar.timestamp for bar in bars), default=None)
    if newest_timestamp is None:
        raise TradingLoopSafetyError("paper live loop blocked because no bars were received")

    stale_symbols = [
        f"{bar.symbol} age={int((newest_timestamp - bar.timestamp).total_seconds())}s"
        for bar in bars
        if (newest_timestamp - bar.timestamp).total_seconds()
        > config.data.max_bar_age_seconds
    ]
    if stale_symbols:
        raise TradingLoopSafetyError(
            "paper live loop blocked by unaligned latest bars: "
            + ", ".join(stale_symbols)
        )


def _required_history_count(config: RuntimeConfig) -> int:
    if config.strategy.strategy_mode == StrategyMode.MOMENTUM_BREAKOUT:
        return config.strategy.breakout_sma_bars + 1
    if config.strategy.strategy_mode == StrategyMode.MEAN_REVERSION:
        if config.strategy.trend_filter_enabled:
            return max(config.strategy.sma_bars, config.strategy.long_sma_bars)
        return config.strategy.sma_bars
    return max(
        config.strategy.sma_bars,
        config.strategy.long_sma_bars,
        config.strategy.breakout_sma_bars + 1,
    )


def _unmanaged_positions(positions: list, config: RuntimeConfig) -> list[str]:
    configured_symbols = {symbol.upper() for symbol in config.symbols}
    unmanaged: list[str] = []
    for position in positions:
        symbol = str(getattr(position, "symbol", "") or "").upper()
        source = str(getattr(position, "source", "") or "").lower()
        if not symbol:
            continue
        reasons: list[str] = []
        if symbol not in configured_symbols:
            reasons.append("outside_config")
        if "short" in source:
            reasons.append("short")
        if reasons:
            unmanaged.append(f"{symbol}({','.join(reasons)})")
    return unmanaged
