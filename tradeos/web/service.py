"""Bounded offline research service. Never constructs a connected broker."""

from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json

from tradeos.backtest.runner import BacktestRunner
from tradeos.config.loader import load_config
from tradeos.config.schema import BacktestCostConfig
from tradeos.data.historical_dataset import load_bars_from_csv
from tradeos.data.sample_dataset import write_sample_dataset
from tradeos.domain.enums import StrategyMode
from tradeos.domain.models import PortfolioState
from tradeos.execution.broker import FakeBroker
from tradeos.execution.reconciliation import ReconciliationService
from tradeos.risk.risk_manager import RiskManager
from tradeos.strategy.mean_reversion import MeanReversionStrategy
from tradeos.strategy.momentum_breakout import MomentumBreakoutStrategy


def decimal_input(payload, name, default, minimum, maximum):
    try:
        value = Decimal(str(payload.get(name, default)))
    except InvalidOperation as exc:
        raise ValueError(f"{name}: enter a number") from exc
    if not value.is_finite() or not Decimal(minimum) <= value <= Decimal(maximum):
        raise ValueError(f"{name}: must be between {minimum} and {maximum}")
    return value


def run_research(payload: dict, config_path: Path) -> dict:
    allowed = {"strategy", "entry_percent", "exit_percent", "commission", "slippage_bps", "csv"}
    if payload.keys() - allowed:
        raise ValueError("Unsupported setting; only offline research parameters are accepted")
    if not isinstance(payload.get("strategy", "mean_reversion"), str):
        raise ValueError("Choose a supported strategy")
    mode = StrategyMode(payload.get("strategy", "mean_reversion"))
    entry = decimal_input(payload, "entry_percent", "0.5", "0.001", "100") / 100
    exit_threshold = decimal_input(payload, "exit_percent", "0.5", "0.001", "100") / 100
    commission = decimal_input(payload, "commission", "0", "0", "100")
    slippage = decimal_input(payload, "slippage_bps", "0", "0", "100")
    csv_text = payload.get("csv")
    if csv_text is not None and (not isinstance(csv_text, str) or not csv_text.strip()):
        raise ValueError("Choose a nonempty CSV file or use the synthetic sample")
    if csv_text and (len(csv_text.encode("utf-8")) > 2_000_000 or len(csv_text.splitlines()) > 10_001):
        raise ValueError("CSV limit: 2 MB and 10,000 bars")
    config = load_config(config_path)
    config = replace(config, strategy=replace(
        config.strategy, strategy_mode=mode, entry_threshold_pct=entry,
        exit_threshold_pct=exit_threshold, breakout_threshold_pct=entry,
        momentum_exit_threshold_pct=exit_threshold,
    ), backtest_costs=BacktestCostConfig(commission_per_trade=commission, slippage_bps=slippage))
    with TemporaryDirectory(prefix="tradeos-web-") as directory:
        path = Path(directory) / "bars.csv"
        if csv_text is None:
            write_sample_dataset(path)
        else:
            path.write_text(csv_text, encoding="utf-8")
        dataset_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        bars = load_bars_from_csv(path)
    if len(bars) > 10_000:
        raise ValueError("CSV limit: 10,000 bars")
    if any(bar.symbol not in config.symbols for bar in bars):
        raise ValueError("CSV symbols must match the configuration: " + ", ".join(config.symbols))
    if any(bar.timeframe != config.timeframe for bar in bars):
        raise ValueError(f"CSV timeframe must be {config.timeframe}")
    for symbol in config.symbols:
        if sum(bar.symbol == symbol for bar in bars) > 3_000:
            raise ValueError("CSV limit: 3,000 bars per symbol")
    account = PortfolioState(cash=Decimal("100000"), equity=Decimal("100000"),
                             buying_power=Decimal("100000"), positions=[], timestamp=bars[0].timestamp)
    strategy = MeanReversionStrategy() if mode == StrategyMode.MEAN_REVERSION else MomentumBreakoutStrategy()
    result = BacktestRunner(strategy, RiskManager(), FakeBroker(account), ReconciliationService()).run(
        bars=bars, strategy_config=config.strategy, risk_config=config.risk,
        local_positions=[], backtest_costs=config.backtest_costs, force_flatten_end=True,
    )
    warnings = ["Synthetic data demonstrates software behavior, not trading performance."] if csv_text is None else ["Uploaded data is user-provided; its market provenance has not been verified."]
    if result.closed_trades < 30:
        warnings.append(f"Only {result.closed_trades} closed trades; too few to establish a reliable trading edge.")
    if commission == 0 and slippage == 0:
        warnings.append("Transaction costs are set to zero. Adjust costs to explore their effect.")
    summary = {name: getattr(result, name) for name in (
        "total_pnl", "realized_pnl", "unrealized_pnl", "max_drawdown", "total_costs",
        "closed_trades", "win_rate", "order_intents_created", "total_cycles",
        "buy_signals", "sell_signals", "hold_signals", "approved_risk_decisions",
        "blocked_risk_decisions", "dirty_reconciliation_count",
    )}
    # Decimal strings preserve exact research values; never emit NaN/Infinity JSON numbers.
    return json.loads(json.dumps({
        "mode": "offline", "broker_orders_submitted": 0,
        "dataset": {"kind": "synthetic" if csv_text is None else "uploaded", "sha256": dataset_hash,
                    "bars": len(bars), "symbols": sorted({bar.symbol for bar in bars}),
                    "start": bars[0].timestamp, "end": bars[-1].timestamp},
        "configuration": asdict(config), "summary": summary,
        "curve": [{"timestamp": bar.timestamp, "symbol": bar.symbol, "pnl": pnl}
                  for bar, pnl in zip(bars, result.equity_curve)],
        "trades": [asdict(trade) for trade in result.closed_trade_details],
        "positions": [asdict(position) for position in result.positions_end],
        "risk_reasons": result.risk_reason_counts, "warnings": warnings,
    }, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value), allow_nan=False))
