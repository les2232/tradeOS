"""Backtest runner.

This module will coordinate historical data, strategy signals, risk checks, and
simulated execution for reproducible backtests.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradeos.config.schema import BacktestCostConfig, RiskConfig, StrategyConfig
from tradeos.domain.enums import RiskDecisionStatus, TradeAction
from tradeos.domain.models import Bar, Fill, OrderIntent, PortfolioState, Position, Signal
from tradeos.engine.cycle import TradingCycle
from tradeos.execution.broker import Broker, FakeBroker
from tradeos.execution.reconciliation import ReconciliationService, ReconciliationStatus
from tradeos.observability.events import CycleSummary, summarize_latest_cycle
from tradeos.observability.reports import summarize_event_counts
from tradeos.research.experiment import (
    ExperimentResult,
    ExperimentSpec,
    make_experiment_id,
)
from tradeos.research.scorer import generate_warnings, score_result
from tradeos.risk.risk_manager import RiskManager
from tradeos.storage.sqlite import SQLiteEventStore
from tradeos.strategy.base import Strategy


FORCED_FLATTEN_REASON = "forced_end_of_window_flatten"


@dataclass(frozen=True)
class ClosedTrade:
    """Diagnostics for one simulated closed trade."""

    symbol: str
    entry_time: datetime
    entry_price: Decimal
    exit_time: datetime
    exit_price: Decimal
    quantity: Decimal
    pnl: Decimal
    entry_reason: str
    exit_reason: str
    bars_held: int
    max_favorable_excursion: Decimal
    max_adverse_excursion: Decimal


@dataclass(frozen=True)
class BacktestResult:
    """Simple offline backtest summary."""

    total_bars: int
    total_cycles: int
    signals: list[Signal]
    orders_created: list[OrderIntent]
    fills: list[Fill]
    event_counts: dict[str, int]
    signal_reason_counts: dict[str, int]
    risk_reason_counts: dict[str, int]
    latest_cycle: CycleSummary | None
    buy_signals: int
    sell_signals: int
    hold_signals: int
    blocked_risk_decisions: int
    approved_risk_decisions: int
    dirty_reconciliation_count: int
    order_intents_created: int
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal
    positions_end: list[Position]
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal
    expectancy: Decimal
    equity_curve: list[Decimal]
    max_drawdown: Decimal
    total_costs: Decimal
    closed_trade_details: list[ClosedTrade]
    pnl_by_symbol: dict[str, Decimal]
    closed_trades_by_symbol: dict[str, int]
    pnl_by_entry_hour: dict[str, Decimal]
    closed_trades_by_entry_hour: dict[str, int]
    pnl_by_exit_reason: dict[str, Decimal]
    closed_trades_by_exit_reason: dict[str, int]
    forced_flatten_trades: int
    closed_trade_pnl_total: Decimal
    attribution_pnl_total: Decimal
    unrealized_pnl_explains_total_difference: bool


@dataclass
class _SimulatedPosition:
    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal


@dataclass
class _OpenTrade:
    symbol: str
    entry_time: datetime
    entry_price: Decimal
    quantity: Decimal
    entry_reason: str
    entry_cost: Decimal = Decimal("0")
    bars_held: int = 0
    max_favorable_excursion: Decimal = Decimal("0")
    max_adverse_excursion: Decimal = Decimal("0")


class BacktestRunner:
    """Run a deterministic dry-run TradingCycle over historical bars."""

    def __init__(
        self,
        strategy: Strategy,
        risk_manager: RiskManager,
        broker: Broker,
        reconciliation_service: ReconciliationService,
    ) -> None:
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.broker = broker
        self.reconciliation_service = reconciliation_service

    def run(
        self,
        bars: list[Bar],
        strategy_config: StrategyConfig,
        risk_config: RiskConfig,
        local_positions: list[Position],
        backtest_costs: BacktestCostConfig | None = None,
        initial_history: dict[str, list[Bar]] | None = None,
        force_flatten_end: bool = False,
    ) -> BacktestResult:
        """Run bars through TradingCycle with execute_orders disabled."""

        costs = backtest_costs or BacktestCostConfig()
        event_store = SQLiteEventStore(":memory:")
        initial_account = self.broker.get_account()
        simulated_positions = _positions_by_symbol(self.broker.get_positions())
        use_simulated_local_positions = False
        cash = initial_account.cash

        signals: list[Signal] = []
        orders_created: list[OrderIntent] = []
        fills: list[Fill] = []
        buy_signals = 0
        sell_signals = 0
        hold_signals = 0
        blocked_risk_decisions = 0
        approved_risk_decisions = 0
        dirty_reconciliation_count = 0
        realized_pnl = Decimal("0")
        trade_pnls: list[Decimal] = []
        last_prices: dict[str, Decimal] = {}
        last_bars_by_symbol: dict[str, Bar] = {}
        equity_curve: list[Decimal] = []
        total_costs = Decimal("0")
        history_by_symbol: dict[str, list[Bar]] = {
            symbol: list(symbol_history)
            for symbol, symbol_history in (initial_history or {}).items()
        }
        signal_reason_counts: dict[str, int] = {}
        risk_reason_counts: dict[str, int] = {}
        open_trades: dict[str, _OpenTrade] = {}
        closed_trade_details: list[ClosedTrade] = []

        for bar in bars:
            history = list(history_by_symbol.get(bar.symbol, []))
            last_prices[bar.symbol] = bar.close
            last_bars_by_symbol[bar.symbol] = bar
            cycle_positions = _positions_from_simulated(simulated_positions)
            cycle_account = _portfolio_state(
                template=initial_account,
                cash=cash,
                positions=cycle_positions,
            )
            local_positions_for_cycle = (
                cycle_positions if use_simulated_local_positions else local_positions
            )
            cycle = TradingCycle(
                strategy=self.strategy,
                risk_manager=self.risk_manager,
                broker=FakeBroker(cycle_account),
                reconciliation_service=self.reconciliation_service,
                execute_orders=False,
                event_store=event_store,
            )
            result = cycle.run(
                bar=bar,
                strategy_config=strategy_config,
                risk_config=risk_config,
                local_positions=local_positions_for_cycle,
                history=history,
            )
            signals.append(result.signal)
            signal_reason_counts[result.signal.reason] = (
                signal_reason_counts.get(result.signal.reason, 0) + 1
            )
            risk_reason_counts[result.risk_decision.reason] = (
                risk_reason_counts.get(result.risk_decision.reason, 0) + 1
            )
            if result.signal.action == TradeAction.BUY:
                buy_signals += 1
            elif result.signal.action == TradeAction.SELL:
                sell_signals += 1
            elif result.signal.action == TradeAction.HOLD:
                hold_signals += 1

            if result.risk_decision.status == RiskDecisionStatus.BLOCKED:
                blocked_risk_decisions += 1
            elif result.risk_decision.status == RiskDecisionStatus.APPROVED:
                approved_risk_decisions += 1

            if result.reconciliation_result.status == ReconciliationStatus.DIRTY:
                dirty_reconciliation_count += 1

            _update_open_trade_excursion(open_trades.get(bar.symbol), bar.close)

            if result.order_intent is not None:
                orders_created.append(result.order_intent)
                if result.order_intent.side.value == "buy":
                    entry_price = _execution_price(bar.close, "buy", costs)
                    _open_trade_if_needed(
                        open_trades=open_trades,
                        symbol=bar.symbol,
                        entry_time=bar.timestamp,
                        entry_price=entry_price,
                        quantity=(
                            result.order_intent.quantity / entry_price
                            if entry_price > Decimal("0")
                            else Decimal("0")
                        ),
                        entry_reason=result.signal.reason,
                        entry_cost=costs.commission_per_trade,
                    )
                cash, realized_pnl, trade_pnl, order_cost = _apply_order_intent(
                    positions=simulated_positions,
                    order_intent=result.order_intent,
                    price=bar.close,
                    cash=cash,
                    realized_pnl=realized_pnl,
                    costs=costs,
                )
                total_costs += order_cost
                if trade_pnl is not None:
                    closed_trade = _close_trade(
                        open_trades=open_trades,
                        symbol=bar.symbol,
                        exit_time=bar.timestamp,
                        exit_price=_execution_price(bar.close, "sell", costs),
                        quantity=result.order_intent.quantity,
                        pnl=trade_pnl,
                        exit_reason=result.signal.reason,
                    )
                    if closed_trade is not None:
                        closed_trade_details.append(closed_trade)
                        trade_pnls.append(closed_trade.pnl)
                    else:
                        trade_pnls.append(trade_pnl)
                use_simulated_local_positions = True
            if result.fill is not None:
                fills.append(result.fill)
            equity_curve.append(realized_pnl + _unrealized_pnl(simulated_positions, last_prices))
            history_by_symbol.setdefault(bar.symbol, []).append(bar)

        if force_flatten_end:
            cash, realized_pnl, forced_trade_pnls, forced_costs, forced_trades = (
                _force_flatten_positions(
                    positions=simulated_positions,
                    open_trades=open_trades,
                    last_bars_by_symbol=last_bars_by_symbol,
                    cash=cash,
                    realized_pnl=realized_pnl,
                    costs=costs,
                )
            )
            total_costs += forced_costs
            trade_pnls.extend(forced_trade_pnls)
            closed_trade_details.extend(forced_trades)
            if equity_curve:
                equity_curve[-1] = realized_pnl + _unrealized_pnl(
                    simulated_positions,
                    last_prices,
                )

        events = event_store.list_events()
        positions_end = _positions_from_simulated(simulated_positions, last_prices)
        unrealized_pnl = _unrealized_pnl(simulated_positions, last_prices)
        trade_metrics = _trade_metrics(trade_pnls, realized_pnl)
        max_drawdown = _max_drawdown(equity_curve)
        attribution = _trade_attribution(closed_trade_details)
        consistency = _pnl_consistency(
            closed_trade_details=closed_trade_details,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            pnl_by_symbol=attribution["pnl_by_symbol"],
            pnl_by_exit_reason=attribution["pnl_by_exit_reason"],
        )
        return BacktestResult(
            total_bars=len(bars),
            total_cycles=len(bars),
            signals=signals,
            orders_created=orders_created,
            fills=fills,
            event_counts=summarize_event_counts(events),
            signal_reason_counts=signal_reason_counts,
            risk_reason_counts=risk_reason_counts,
            latest_cycle=summarize_latest_cycle(events),
            buy_signals=buy_signals,
            sell_signals=sell_signals,
            hold_signals=hold_signals,
            blocked_risk_decisions=blocked_risk_decisions,
            approved_risk_decisions=approved_risk_decisions,
            dirty_reconciliation_count=dirty_reconciliation_count,
            order_intents_created=len(orders_created),
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_pnl=realized_pnl + unrealized_pnl,
            positions_end=positions_end,
            closed_trades=trade_metrics["closed_trades"],
            winning_trades=trade_metrics["winning_trades"],
            losing_trades=trade_metrics["losing_trades"],
            win_rate=trade_metrics["win_rate"],
            gross_profit=trade_metrics["gross_profit"],
            gross_loss=trade_metrics["gross_loss"],
            profit_factor=trade_metrics["profit_factor"],
            expectancy=trade_metrics["expectancy"],
            equity_curve=equity_curve,
            max_drawdown=max_drawdown,
            total_costs=total_costs,
            closed_trade_details=closed_trade_details,
            pnl_by_symbol=attribution["pnl_by_symbol"],
            closed_trades_by_symbol=attribution["closed_trades_by_symbol"],
            pnl_by_entry_hour=attribution["pnl_by_entry_hour"],
            closed_trades_by_entry_hour=attribution["closed_trades_by_entry_hour"],
            pnl_by_exit_reason=attribution["pnl_by_exit_reason"],
            closed_trades_by_exit_reason=attribution["closed_trades_by_exit_reason"],
            forced_flatten_trades=attribution["closed_trades_by_exit_reason"].get(
                FORCED_FLATTEN_REASON,
                0,
            ),
            closed_trade_pnl_total=consistency["closed_trade_pnl_total"],
            attribution_pnl_total=consistency["attribution_pnl_total"],
            unrealized_pnl_explains_total_difference=consistency[
                "unrealized_pnl_explains_total_difference"
            ],
        )

    def to_experiment_result(
        self,
        backtest_result: BacktestResult,
        experiment_spec: ExperimentSpec,
    ) -> ExperimentResult:
        """Convert a backtest result into a scored research experiment result."""

        unscored = ExperimentResult(
            experiment_id=make_experiment_id(experiment_spec),
            spec=experiment_spec,
            profit_factor=backtest_result.profit_factor,
            max_drawdown=backtest_result.max_drawdown,
            win_rate=backtest_result.win_rate,
            num_trades=backtest_result.closed_trades,
            expectancy=backtest_result.expectancy,
            net_pnl=backtest_result.total_pnl,
            score=Decimal("0"),
            warnings=[],
        )
        return ExperimentResult(
            experiment_id=unscored.experiment_id,
            spec=unscored.spec,
            profit_factor=unscored.profit_factor,
            max_drawdown=unscored.max_drawdown,
            win_rate=unscored.win_rate,
            num_trades=unscored.num_trades,
            expectancy=unscored.expectancy,
            net_pnl=unscored.net_pnl,
            score=score_result(unscored),
            warnings=generate_warnings(unscored),
        )


def _positions_by_symbol(positions: list[Position]) -> dict[str, _SimulatedPosition]:
    return {
        position.symbol: _SimulatedPosition(
            symbol=position.symbol,
            quantity=position.quantity,
            avg_entry_price=position.avg_entry_price,
        )
        for position in positions
        if position.quantity > Decimal("0")
    }


def _positions_from_simulated(
    positions: dict[str, _SimulatedPosition],
    last_prices: dict[str, Decimal] | None = None,
) -> list[Position]:
    prices = last_prices or {}
    output: list[Position] = []
    for symbol in sorted(positions):
        position = positions[symbol]
        if position.quantity <= Decimal("0"):
            continue
        market_price = prices.get(symbol, position.avg_entry_price)
        output.append(
            Position(
                symbol=symbol,
                quantity=position.quantity,
                avg_entry_price=position.avg_entry_price,
                market_value=position.quantity * market_price,
                unrealized_pl=(market_price - position.avg_entry_price)
                * position.quantity,
                source="backtest_sim",
            )
        )
    return output


def _portfolio_state(
    *,
    template: PortfolioState,
    cash: Decimal,
    positions: list[Position],
) -> PortfolioState:
    market_value = sum((position.market_value for position in positions), Decimal("0"))
    return PortfolioState(
        cash=cash,
        equity=cash + market_value,
        buying_power=cash,
        positions=positions,
        timestamp=template.timestamp,
    )


def _apply_order_intent(
    *,
    positions: dict[str, _SimulatedPosition],
    order_intent: OrderIntent,
    price: Decimal,
    cash: Decimal,
    realized_pnl: Decimal,
    costs: BacktestCostConfig,
) -> tuple[Decimal, Decimal, Decimal | None, Decimal]:
    commission = costs.commission_per_trade
    if order_intent.side.value == "buy":
        budget = order_intent.quantity
        execution_price = _execution_price(price, "buy", costs)
        if execution_price <= Decimal("0"):
            return cash, realized_pnl, None, Decimal("0")
        quantity = budget / execution_price
        position = positions.get(order_intent.symbol)
        if position is None:
            positions[order_intent.symbol] = _SimulatedPosition(
                symbol=order_intent.symbol,
                quantity=quantity,
                avg_entry_price=execution_price,
            )
        else:
            new_quantity = position.quantity + quantity
            total_cost = (
                position.avg_entry_price * position.quantity
                + execution_price * quantity
            )
            position.quantity = new_quantity
            position.avg_entry_price = total_cost / new_quantity
        return cash - budget - commission, realized_pnl - commission, None, commission

    if order_intent.side.value == "sell":
        position = positions.get(order_intent.symbol)
        if position is None or position.quantity <= Decimal("0"):
            return cash, realized_pnl, None, Decimal("0")
        execution_price = _execution_price(price, "sell", costs)
        quantity_sold = min(order_intent.quantity, position.quantity)
        trade_pnl = (
            (execution_price - position.avg_entry_price) * quantity_sold
            - commission
        )
        realized_pnl += trade_pnl
        position.quantity -= quantity_sold
        if position.quantity <= Decimal("0"):
            del positions[order_intent.symbol]
        return cash + execution_price * quantity_sold - commission, realized_pnl, trade_pnl, commission

    return cash, realized_pnl, None, Decimal("0")


def _execution_price(price: Decimal, side: str, costs: BacktestCostConfig) -> Decimal:
    slippage = costs.slippage_bps / Decimal("10000")
    if side == "buy":
        return price * (Decimal("1") + slippage)
    return price * (Decimal("1") - slippage)


def _open_trade_if_needed(
    *,
    open_trades: dict[str, _OpenTrade],
    symbol: str,
    entry_time: datetime,
    entry_price: Decimal,
    quantity: Decimal,
    entry_reason: str,
    entry_cost: Decimal = Decimal("0"),
) -> None:
    if symbol in open_trades or quantity <= Decimal("0"):
        return
    open_trades[symbol] = _OpenTrade(
        symbol=symbol,
        entry_time=entry_time,
        entry_price=entry_price,
        quantity=quantity,
        entry_reason=entry_reason,
        entry_cost=entry_cost,
    )


def _update_open_trade_excursion(
    trade: _OpenTrade | None,
    close_price: Decimal,
) -> None:
    if trade is None:
        return
    trade.bars_held += 1
    move = (close_price - trade.entry_price) * trade.quantity
    if move > trade.max_favorable_excursion:
        trade.max_favorable_excursion = move
    if move < trade.max_adverse_excursion:
        trade.max_adverse_excursion = move


def _close_trade(
    *,
    open_trades: dict[str, _OpenTrade],
    symbol: str,
    exit_time: datetime,
    exit_price: Decimal,
    quantity: Decimal,
    pnl: Decimal,
    exit_reason: str,
) -> ClosedTrade | None:
    trade = open_trades.get(symbol)
    if trade is None:
        return None
    closed_quantity = min(quantity, trade.quantity)
    cost_fraction = (
        closed_quantity / trade.quantity if trade.quantity > Decimal("0") else Decimal("0")
    )
    allocated_entry_cost = trade.entry_cost * cost_fraction
    closed_trade = ClosedTrade(
        symbol=symbol,
        entry_time=trade.entry_time,
        entry_price=trade.entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        quantity=closed_quantity,
        pnl=pnl - allocated_entry_cost,
        entry_reason=trade.entry_reason,
        exit_reason=exit_reason,
        bars_held=trade.bars_held,
        max_favorable_excursion=trade.max_favorable_excursion,
        max_adverse_excursion=trade.max_adverse_excursion,
    )
    trade.quantity -= closed_quantity
    trade.entry_cost -= allocated_entry_cost
    if trade.quantity <= Decimal("0"):
        del open_trades[symbol]
    return closed_trade


def _force_flatten_positions(
    *,
    positions: dict[str, _SimulatedPosition],
    open_trades: dict[str, _OpenTrade],
    last_bars_by_symbol: dict[str, Bar],
    cash: Decimal,
    realized_pnl: Decimal,
    costs: BacktestCostConfig,
) -> tuple[Decimal, Decimal, list[Decimal], Decimal, list[ClosedTrade]]:
    trade_pnls: list[Decimal] = []
    closed_trades: list[ClosedTrade] = []
    total_costs = Decimal("0")

    for symbol in sorted(list(positions)):
        position = positions.get(symbol)
        final_bar = last_bars_by_symbol.get(symbol)
        if position is None or position.quantity <= Decimal("0") or final_bar is None:
            continue

        execution_price = _execution_price(final_bar.close, "sell", costs)
        quantity_sold = position.quantity
        commission = costs.commission_per_trade
        trade_pnl = (
            (execution_price - position.avg_entry_price) * quantity_sold
            - commission
        )
        realized_pnl += trade_pnl
        cash += execution_price * quantity_sold - commission
        total_costs += commission

        closed_trade = _close_trade(
            open_trades=open_trades,
            symbol=symbol,
            exit_time=final_bar.timestamp,
            exit_price=execution_price,
            quantity=quantity_sold,
            pnl=trade_pnl,
            exit_reason=FORCED_FLATTEN_REASON,
        )
        if closed_trade is None:
            closed_trade = ClosedTrade(
                symbol=symbol,
                entry_time=final_bar.timestamp,
                entry_price=position.avg_entry_price,
                exit_time=final_bar.timestamp,
                exit_price=execution_price,
                quantity=quantity_sold,
                pnl=trade_pnl,
                entry_reason="existing_position_at_backtest_start",
                exit_reason=FORCED_FLATTEN_REASON,
                bars_held=0,
                max_favorable_excursion=Decimal("0"),
                max_adverse_excursion=Decimal("0"),
            )
        closed_trades.append(closed_trade)
        trade_pnls.append(closed_trade.pnl)
        del positions[symbol]

    return cash, realized_pnl, trade_pnls, total_costs, closed_trades


def _unrealized_pnl(
    positions: dict[str, _SimulatedPosition],
    last_prices: dict[str, Decimal],
) -> Decimal:
    total = Decimal("0")
    for symbol, position in positions.items():
        if position.quantity <= Decimal("0"):
            continue
        price = last_prices.get(symbol, position.avg_entry_price)
        total += (price - position.avg_entry_price) * position.quantity
    return total


def _max_drawdown(equity_curve: list[Decimal]) -> Decimal:
    if not equity_curve:
        return Decimal("0")
    peak = equity_curve[0]
    max_drawdown = Decimal("0")
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return max_drawdown


def _trade_metrics(trade_pnls: list[Decimal], realized_pnl: Decimal) -> dict[str, Decimal | int]:
    closed_trades = len(trade_pnls)
    winning_trades = sum(1 for pnl in trade_pnls if pnl > Decimal("0"))
    losing_trades = sum(1 for pnl in trade_pnls if pnl < Decimal("0"))
    gross_profit = sum((pnl for pnl in trade_pnls if pnl > Decimal("0")), Decimal("0"))
    gross_loss = abs(sum((pnl for pnl in trade_pnls if pnl < Decimal("0")), Decimal("0")))

    if closed_trades > 0:
        win_rate = Decimal(winning_trades) / Decimal(closed_trades)
        expectancy = realized_pnl / Decimal(closed_trades)
    else:
        win_rate = Decimal("0")
        expectancy = Decimal("0")

    if gross_loss > Decimal("0"):
        profit_factor = gross_profit / gross_loss
    elif gross_profit > Decimal("0"):
        profit_factor = Decimal("Infinity")
    else:
        profit_factor = Decimal("0")

    return {
        "closed_trades": closed_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
    }


def _trade_attribution(closed_trades: list[ClosedTrade]) -> dict[str, dict]:
    pnl_by_symbol: dict[str, Decimal] = {}
    closed_trades_by_symbol: dict[str, int] = {}
    pnl_by_entry_hour: dict[str, Decimal] = {}
    closed_trades_by_entry_hour: dict[str, int] = {}
    pnl_by_exit_reason: dict[str, Decimal] = {}
    closed_trades_by_exit_reason: dict[str, int] = {}

    for trade in closed_trades:
        entry_hour = f"{trade.entry_time.hour:02d}:00"
        _add_decimal(pnl_by_symbol, trade.symbol, trade.pnl)
        _add_count(closed_trades_by_symbol, trade.symbol)
        _add_decimal(pnl_by_entry_hour, entry_hour, trade.pnl)
        _add_count(closed_trades_by_entry_hour, entry_hour)
        _add_decimal(pnl_by_exit_reason, trade.exit_reason, trade.pnl)
        _add_count(closed_trades_by_exit_reason, trade.exit_reason)

    return {
        "pnl_by_symbol": pnl_by_symbol,
        "closed_trades_by_symbol": closed_trades_by_symbol,
        "pnl_by_entry_hour": pnl_by_entry_hour,
        "closed_trades_by_entry_hour": closed_trades_by_entry_hour,
        "pnl_by_exit_reason": pnl_by_exit_reason,
        "closed_trades_by_exit_reason": closed_trades_by_exit_reason,
    }


def _add_decimal(target: dict[str, Decimal], key: str, value: Decimal) -> None:
    target[key] = target.get(key, Decimal("0")) + value


def _add_count(target: dict[str, int], key: str) -> None:
    target[key] = target.get(key, 0) + 1


def _pnl_consistency(
    *,
    closed_trade_details: list[ClosedTrade],
    realized_pnl: Decimal,
    unrealized_pnl: Decimal,
    pnl_by_symbol: dict[str, Decimal],
    pnl_by_exit_reason: dict[str, Decimal],
) -> dict[str, Decimal | bool]:
    closed_trade_pnl_total = sum(
        (trade.pnl for trade in closed_trade_details),
        Decimal("0"),
    )
    attribution_pnl_total = sum(pnl_by_symbol.values(), Decimal("0"))
    exit_reason_pnl_total = sum(pnl_by_exit_reason.values(), Decimal("0"))

    if not _decimal_close(closed_trade_pnl_total, realized_pnl):
        raise ValueError("closed trade PnL total must equal realized PnL")
    if not _decimal_close(attribution_pnl_total, closed_trade_pnl_total):
        raise ValueError("symbol attribution PnL must equal closed trade PnL total")
    if not _decimal_close(exit_reason_pnl_total, closed_trade_pnl_total):
        raise ValueError("exit reason attribution PnL must equal closed trade PnL total")

    return {
        "closed_trade_pnl_total": closed_trade_pnl_total,
        "attribution_pnl_total": attribution_pnl_total,
        "unrealized_pnl_explains_total_difference": unrealized_pnl != Decimal("0"),
    }


def _decimal_close(
    left: Decimal,
    right: Decimal,
    tolerance: Decimal = Decimal("0.000001"),
) -> bool:
    return abs(left - right) <= tolerance
