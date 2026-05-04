"""Desktop dashboard for paper-safe TradeOS operations."""

from __future__ import annotations

import contextlib
import io
import os
import re
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tradeos.main import main as tradeos_main


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = str(PROJECT_ROOT / "config" / "momentum_test.yaml")
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_AUTO_INTERVAL_MINUTES = "15"
MIN_AUTO_INTERVAL_MINUTES = 1
MAX_AUTO_INTERVAL_MINUTES = 390

BG = "#f4f7fb"
SURFACE = "#ffffff"
SURFACE_SOFT = "#f8fafc"
BORDER = "#e3e8ef"
INK = "#102a43"
MUTED = "#627d98"
PRIMARY = "#2563eb"
PRIMARY_DARK = "#1d4ed8"
SAFE = "#047857"
SAFE_BG = "#e7f7ef"
AMBER = "#b7791f"
AMBER_BG = "#fff7df"
DANGER = "#b42318"
DANGER_BG = "#fdecec"
NEUTRAL = "#486581"
NEUTRAL_BG = "#eef4fb"
LOG_BG = "#ffffff"
MODE_BG = "#e6f4ef"
WARNING_BG = "#fffaf0"
WARNING_BORDER = "#f6dc9f"

CYCLE_LINE = re.compile(
    r"^\s*(?P<symbol>[A-Z.]+): (?P<action>\w+) \| "
    r"bar (?P<bar>[^|]+) \| risk (?P<risk>\w+) \| "
    r"signal (?P<signal>[^|]+) \| rules (?P<rules>[^|]+) \| "
    r"(?:reconciliation (?P<reconciliation>\w+) \| "
    r"execution (?P<execution>[\w_]+) \| )?"
    r"(?P<reason>.+)$"
)


@dataclass(frozen=True)
class DecisionSummary:
    """Dashboard-ready interpretation of one paper loop output."""

    run_state: str
    data_gate: str
    reconciliation: str
    signal: str
    risk: str
    execution: str
    order_mode: str
    latest_title: str
    latest_detail: str


@dataclass(frozen=True)
class CycleDecision:
    """One symbol-level decision parsed from CLI output."""

    symbol: str
    action: str
    bar_time: str
    risk: str
    reconciliation: str
    execution: str
    signal: str
    rules: str
    reason: str


@dataclass(frozen=True)
class PaperAccountSnapshot:
    """Dashboard view of current paper broker exposure."""

    positions: list[tuple[str, str, str, str, str]]
    open_orders: list[tuple[str, str, str, str, str]]
    status: str


def command_args(config_path: str, *, submit_paper_orders: bool = False) -> list[str]:
    """Build CLI-equivalent args for a paper live pass."""

    args = ["--config", config_path, "--paper-live-once"]
    if submit_paper_orders:
        args.append("--submit-paper-orders")
    return args


def missing_credentials(api_key: str, secret_key: str) -> list[str]:
    """Return missing Alpaca credential variable names."""

    missing = []
    if not api_key.strip():
        missing.append("ALPACA_API_KEY")
    if not secret_key.strip():
        missing.append("ALPACA_SECRET_KEY")
    return missing


def load_paper_credentials(
    *,
    env_file: Path = DEFAULT_ENV_FILE,
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Load Alpaca paper credentials from environment, then local .env."""

    source = environment if environment is not None else os.environ
    dotenv = _read_dotenv(env_file)
    secret_key = (
        source.get("ALPACA_SECRET_KEY", "")
        or source.get("ALPACA_API_SECRET", "")
        or dotenv.get("ALPACA_SECRET_KEY", "")
        or dotenv.get("ALPACA_API_SECRET", "")
    )
    return {
        "ALPACA_API_KEY": source.get("ALPACA_API_KEY", "") or dotenv.get("ALPACA_API_KEY", ""),
        "ALPACA_SECRET_KEY": secret_key,
    }


def credential_source(
    *,
    env_file: Path = DEFAULT_ENV_FILE,
    environment: dict[str, str] | None = None,
) -> str:
    """Return a dashboard-ready credential source label."""

    source = environment if environment is not None else os.environ
    if source.get("ALPACA_API_KEY", "") and source.get("ALPACA_SECRET_KEY", ""):
        return "Loaded from environment"
    credentials = load_paper_credentials(env_file=env_file, environment=source)
    if not missing_credentials(credentials["ALPACA_API_KEY"], credentials["ALPACA_SECRET_KEY"]):
        return f"Loaded from {env_file.name}"
    return f"Missing credentials in environment or {env_file.name}"


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("'\"")
        if name:
            values[name] = value
    return values


def auto_interval_seconds(value: str) -> int:
    """Return a validated auto-submit interval in seconds."""

    try:
        minutes = int(value.strip())
    except ValueError as exc:
        raise ValueError("Auto interval must be a whole number of minutes.") from exc
    if minutes < MIN_AUTO_INTERVAL_MINUTES:
        raise ValueError(f"Auto interval must be at least {MIN_AUTO_INTERVAL_MINUTES} minute.")
    if minutes > MAX_AUTO_INTERVAL_MINUTES:
        raise ValueError(f"Auto interval must be no more than {MAX_AUTO_INTERVAL_MINUTES} minutes.")
    return minutes * 60


def paper_account_snapshot(
    credentials: dict[str, str],
    *,
    broker_factory=None,
) -> PaperAccountSnapshot:
    """Fetch current paper positions and open orders for the dashboard."""

    missing = missing_credentials(
        credentials.get("ALPACA_API_KEY", ""),
        credentials.get("ALPACA_SECRET_KEY", ""),
    )
    if missing:
        return PaperAccountSnapshot([], [], "Credentials missing")

    try:
        if broker_factory is None:
            from tradeos.execution.broker import AlpacaPaperBroker

            broker_factory = lambda: AlpacaPaperBroker.from_env(paper=True, env=credentials)
        broker = broker_factory()
        positions = [_position_row(position) for position in broker.get_positions()]
        open_orders = [_order_row(order) for order in broker.get_open_orders()]
        return PaperAccountSnapshot(positions, open_orders, "Account refreshed")
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        return PaperAccountSnapshot([], [], f"Account refresh failed: {detail}")


def run_state(exit_code: int, output: str) -> str:
    """Classify command output for dashboard status display."""

    return dashboard_summary(exit_code, output).run_state


def cycle_decisions(output: str) -> list[CycleDecision]:
    """Parse all symbol-level cycle decisions from CLI output."""

    decisions: list[CycleDecision] = []
    for line in output.splitlines():
        parsed = _parse_cycle_line(line)
        if parsed is None:
            continue
        decisions.append(
            CycleDecision(
                symbol=parsed["symbol"],
                action=parsed["action"],
                bar_time=parsed["bar"].strip(),
                risk=_risk_detail(parsed["action"], parsed["risk"]),
                reconciliation=_display_reconciliation(parsed.get("reconciliation")),
                execution=_display_cycle_execution(parsed["action"], parsed.get("execution")),
                signal=parsed["signal"].strip(),
                rules=parsed["rules"].strip(),
                reason=parsed["reason"].strip(),
            )
        )
    return decisions


def dashboard_summary(exit_code: int, output: str) -> DecisionSummary:
    """Parse command output into operator-facing dashboard fields."""

    if exit_code != 0:
        return DecisionSummary(
            run_state="Failed",
            data_gate="Unknown",
            reconciliation="Unknown",
            signal="Review",
            risk="Review",
            execution="Blocked",
            order_mode=_order_mode(output),
            latest_title="Command failed",
            latest_detail=_warning_text(
                "FAILED",
                _first_nonempty_line(output) or "Review the run log.",
            ),
        )

    if "paper live loop blocked:" in output:
        detail = _line_containing(output, "paper live loop blocked:") or "Data gate blocked the run."
        return DecisionSummary(
            run_state="Blocked",
            data_gate="Blocked",
            reconciliation="Not evaluated",
            signal="Not evaluated",
            risk="Not evaluated",
            execution="Blocked",
            order_mode=_order_mode(output),
            latest_title="Data gate blocked evaluation",
            latest_detail=_warning_text(
                "BLOCKED",
                detail.replace("paper live loop blocked: ", ""),
            ),
        )

    cycle_lines = [_parse_cycle_line(line) for line in output.splitlines()]
    cycles = [line for line in cycle_lines if line is not None]
    important = _most_important_cycle(cycles)
    if important is not None:
        symbol = important["symbol"]
        action = important["action"]
        risk = important["risk"]
        signal = important["signal"].strip()
        reason = important["reason"].strip()
        rules = important["rules"].strip()
        order_mode = _order_mode(output)
        execution = _execution_status(cycles, order_mode)
        reconciliation = _reconciliation_status(cycles)
        detail_risk = _risk_detail(action, risk)
        if action == "buy" and risk == "approved":
            if order_mode == "Paper submit enabled":
                title = f"{symbol} buy signal approved for paper submit"
            else:
                title = f"{symbol} buy signal approved for review"
        elif action == "buy":
            title = f"{symbol} buy signal blocked"
        elif any(item["action"] != "hold" for item in cycles):
            title = f"{symbol} signal reviewed"
        else:
            title = "No trade signal"
        detail = (
            f"Signal: {_display_signal_reason(signal)}. Risk: {detail_risk}. Reconciliation: {reconciliation}. "
            f"Execution: {execution}. Rule: {rules}. Reason: {reason}."
        )
        return DecisionSummary(
            run_state=_run_state_for_cycle(action, risk, order_mode),
            data_gate="Aligned",
            reconciliation=reconciliation,
            signal=_signal_status(cycles),
            risk=_risk_status(cycles),
            execution=execution,
            order_mode=order_mode,
            latest_title=title,
            latest_detail=detail,
        )

    if "paper live loop complete" in output:
        return DecisionSummary(
            run_state="Complete",
            data_gate="Aligned",
            reconciliation="Not evaluated",
            signal="No signal",
            risk="No action",
            execution="Not evaluated",
            order_mode=_order_mode(output),
            latest_title="Run complete",
            latest_detail="No cycle detail was found in the output.",
        )

    return DecisionSummary(
        run_state="Complete",
        data_gate="Unknown",
        reconciliation="Unknown",
        signal="Unknown",
        risk="Unknown",
        execution="Unknown",
        order_mode=_order_mode(output),
        latest_title="Review run log",
        latest_detail="The command completed, but the dashboard could not classify the result.",
    )


class TradeOSDashboard:
    """Tkinter workstation around the tested paper live command."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("TradeOS Paper Workstation")
        self.root.minsize(1080, 720)

        self.config_path = tk.StringVar(value=DEFAULT_CONFIG)
        self.credential_status = tk.StringVar(value=credential_source())
        self.submit_ack = tk.BooleanVar(value=False)
        self.auto_interval = tk.StringVar(value=DEFAULT_AUTO_INTERVAL_MINUTES)
        self.auto_status = tk.StringVar(value="Auto off")
        self.account_status = tk.StringVar(value="Account not refreshed")
        self.run_state = tk.StringVar(value="Ready")
        self.data_gate = tk.StringVar(value="Not checked")
        self.reconciliation_state = tk.StringVar(value="Not evaluated")
        self.signal_state = tk.StringVar(value="Not checked")
        self.risk_state = tk.StringVar(value="Not checked")
        self.execution_state = tk.StringVar(value="Not evaluated")
        self.order_mode = tk.StringVar(value="No submit")
        self.latest_title = tk.StringVar(value="Ready for signal check")
        self.latest_detail = tk.StringVar(
            value="NO SUBMIT: Start with Check Signals. Paper order submission stays locked until you enable it."
        )
        self._worker: threading.Thread | None = None
        self._account_worker: threading.Thread | None = None
        self._auto_submit_active = False
        self._auto_after_id: str | None = None
        self._metric_frames: dict[str, tk.Frame] = {}
        self._metric_value_labels: dict[str, tk.Label] = {}
        self._metric_title_labels: dict[str, tk.Label] = {}

        self._configure_style()
        self._build()
        self.root.after(250, self._refresh_account_snapshot)

    def _configure_style(self) -> None:
        self.root.configure(bg=BG)
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("App.TFrame", background=BG)
        style.configure("Header.TLabel", font=("Segoe UI", 22, "bold"), foreground=INK, background=BG)
        style.configure("Body.TLabel", font=("Segoe UI", 10), foreground=INK, background=SURFACE)
        style.configure("Muted.TLabel", font=("Segoe UI", 9), foreground=MUTED, background=SURFACE)
        style.configure("PanelMuted.TLabel", font=("Segoe UI", 9), foreground=MUTED, background=SURFACE_SOFT)
        style.configure("SmallMuted.TLabel", font=("Segoe UI", 9), foreground=MUTED, background=BG)
        style.configure("PanelTitle.TLabel", font=("Segoe UI", 12, "bold"), foreground=INK, background=SURFACE)
        style.configure("SoftPanelTitle.TLabel", font=("Segoe UI", 11, "bold"), foreground=INK, background=SURFACE_SOFT)
        style.configure("MetricTitle.TLabel", font=("Segoe UI", 8, "bold"), foreground=MUTED, background=SURFACE)
        style.configure("MetricValue.TLabel", font=("Segoe UI", 12, "bold"), foreground=INK, background=SURFACE)
        style.configure("ModeBadge.TLabel", font=("Segoe UI", 9, "bold"), foreground=SAFE, background=MODE_BG)
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), foreground=PRIMARY_DARK, padding=(10, 7))
        style.configure("Submit.TButton", font=("Segoe UI", 10, "bold"), foreground=DANGER, padding=(10, 7))
        style.configure("Stop.TButton", font=("Segoe UI", 10, "bold"), foreground=INK, padding=(10, 7))
        style.configure("TEntry", fieldbackground=SURFACE)
        style.configure(
            "Decision.Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=INK,
            rowheight=30,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Decision.Treeview.Heading",
            background=NEUTRAL_BG,
            foreground=MUTED,
            font=("Segoe UI", 9, "bold"),
        )

    def _build(self) -> None:
        shell = ttk.Frame(self.root, style="App.TFrame")
        shell.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(shell, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        outer = ttk.Frame(canvas, style="App.TFrame", padding=18)
        window_id = canvas.create_window((0, 0), window=outer, anchor=tk.NW)

        def _sync_scroll_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        def _on_mousewheel(event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        outer.bind("<Configure>", _sync_scroll_region)
        canvas.bind("<Configure>", _sync_width)
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self._build_header(outer)
        self._build_status_strip(outer)
        self._refresh_status_cards()

        body = ttk.Frame(outer, style="App.TFrame")
        body.pack(fill=tk.X, pady=(0, 14))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)
        self._build_controls(body).grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self._build_decision(body).grid(row=0, column=1, sticky="nsew")

        self._build_log(outer)

    def _build_header(self, parent: tk.Widget) -> None:
        header = ttk.Frame(parent, style="App.TFrame")
        header.pack(fill=tk.X, pady=(0, 12))
        title = ttk.Frame(header, style="App.TFrame")
        title.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title, text="TradeOS Paper Workstation", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(
            title,
            text="Evaluate bounded paper-data signals with safety gates before any paper submission.",
            style="SmallMuted.TLabel",
        ).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(header, text="PAPER MODE", style="ModeBadge.TLabel", padding=(12, 6)).pack(side=tk.RIGHT)

        config = self._panel(parent, border=False)
        config.pack(fill=tk.X, pady=(0, 12))
        config.columnconfigure(1, weight=1)
        ttk.Label(config, text="Config", style="Muted.TLabel").grid(row=0, column=0, padx=(14, 10), pady=12)
        ttk.Entry(config, textvariable=self.config_path).grid(row=0, column=1, sticky=tk.EW, pady=12)
        ttk.Button(config, text="Browse", command=self._browse_config).grid(row=0, column=2, padx=14, pady=12)

    def _build_status_strip(self, parent: tk.Widget) -> None:
        strip = ttk.Frame(parent, style="App.TFrame")
        strip.pack(fill=tk.X, pady=(0, 14))
        metrics = [
            ("Run State", self.run_state),
            ("Data Gate", self.data_gate),
            ("Reconciliation", self.reconciliation_state),
            ("Signal", self.signal_state),
            ("Risk", self.risk_state),
            ("Execution", self.execution_state),
            ("Order Mode", self.order_mode),
        ]
        for index, (title, value) in enumerate(metrics):
            card = self._metric(strip, title, value)
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10 if index < len(metrics) - 1 else 0))

    def _build_controls(self, parent: tk.Widget) -> tk.Frame:
        panel = self._panel(parent)
        ttk.Label(panel, text="Run Controls", style="PanelTitle.TLabel").pack(anchor=tk.W, padx=18, pady=(16, 4))
        ttk.Label(
            panel,
            text="Use Check Signals first. Submit only after aligned data, clean reconciliation, and approved risk.",
            style="Muted.TLabel",
            wraplength=330,
        ).pack(anchor=tk.W, padx=18, pady=(0, 12))

        ttk.Label(panel, textvariable=self.credential_status, style="Muted.TLabel").pack(
            anchor=tk.W, padx=18, pady=(0, 12)
        )

        self.check_button = ttk.Button(
            panel,
            text="Check Signals",
            style="Primary.TButton",
            command=lambda: self._start_run(submit=False),
        )
        self.check_button.pack(fill=tk.X, padx=18, pady=(0, 8))

        self.submit_button = ttk.Button(
            panel,
            text="Submit Paper Order",
            style="Submit.TButton",
            command=lambda: self._start_run(submit=True),
        )
        self.submit_button.pack(fill=tk.X, padx=18, pady=(0, 8))
        self.submit_button.state(["disabled"])

        ttk.Checkbutton(
            panel,
            text="Enable paper-order submission",
            variable=self.submit_ack,
            command=self._sync_submit_button,
        ).pack(anchor=tk.W, padx=18, pady=(4, 12))

        auto = tk.Frame(panel, bg=SURFACE_SOFT, highlightbackground=BORDER, highlightthickness=1)
        auto.pack(fill=tk.X, padx=18, pady=(0, 14))
        auto.columnconfigure(0, weight=1)
        ttk.Label(auto, text="Auto Paper Submit", style="SoftPanelTitle.TLabel").grid(
            row=0, column=0, sticky=tk.W, padx=14, pady=(12, 4)
        )
        self.auto_badge = tk.Label(
            auto,
            textvariable=self.auto_status,
            bg=NEUTRAL_BG,
            fg=NEUTRAL,
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        self.auto_badge.grid(row=0, column=1, columnspan=2, sticky=tk.E, padx=14, pady=(12, 4))
        ttk.Label(auto, text="Every", style="Muted.TLabel").grid(
            row=1, column=0, sticky=tk.W, padx=(14, 6), pady=(8, 4)
        )
        ttk.Spinbox(
            auto,
            from_=MIN_AUTO_INTERVAL_MINUTES,
            to=MAX_AUTO_INTERVAL_MINUTES,
            textvariable=self.auto_interval,
            width=6,
        ).grid(row=1, column=1, sticky=tk.W, pady=(8, 4))
        ttk.Label(auto, text="minutes", style="Muted.TLabel").grid(
            row=1, column=2, sticky=tk.W, padx=(6, 14), pady=(8, 4)
        )
        self.start_auto_button = ttk.Button(
            auto,
            text="Start Auto Submit",
            style="Submit.TButton",
            command=self._start_auto_submit,
        )
        self.start_auto_button.grid(row=2, column=0, columnspan=3, sticky=tk.EW, padx=14, pady=(8, 6))
        self.stop_auto_button = ttk.Button(
            auto,
            text="Stop Auto",
            style="Stop.TButton",
            command=self._stop_auto_submit,
        )
        self.stop_auto_button.grid(row=3, column=0, columnspan=3, sticky=tk.EW, padx=14, pady=(0, 12))
        self.stop_auto_button.state(["disabled"])

        guard = tk.Frame(panel, bg=WARNING_BG, highlightbackground=WARNING_BORDER, highlightthickness=1)
        guard.pack(fill=tk.X, padx=18, pady=(0, 18))
        tk.Label(
            guard,
            text=(
                "Paper only. No real-money live trading command exists. Submission remains explicit."
            ),
            bg=WARNING_BG,
            fg=AMBER,
            font=("Segoe UI", 9),
            justify=tk.LEFT,
            wraplength=330,
            anchor=tk.W,
        ).pack(fill=tk.X, padx=12, pady=8)
        return panel

    def _build_decision(self, parent: tk.Widget) -> tk.Frame:
        panel = self._panel(parent)
        ttk.Label(panel, text="Latest Decision", style="PanelTitle.TLabel").pack(anchor=tk.W, padx=18, pady=(18, 6))
        tk.Label(
            panel,
            textvariable=self.latest_title,
            bg=SURFACE,
            fg=INK,
            font=("Segoe UI", 20, "bold"),
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=18, pady=(0, 8))
        tk.Label(
            panel,
            textvariable=self.latest_detail,
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 10),
            wraplength=680,
            anchor=tk.NW,
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=18, pady=(0, 14))

        ttk.Label(panel, text="Symbol Decisions", style="PanelTitle.TLabel").pack(
            anchor=tk.W, padx=18, pady=(0, 8)
        )
        self.decision_table = ttk.Treeview(
            panel,
            columns=("symbol", "signal", "risk", "reconciliation", "execution", "bar", "reason"),
            show="headings",
            height=5,
            style="Decision.Treeview",
        )
        for column, heading, width in [
            ("symbol", "Symbol", 80),
            ("signal", "Signal", 150),
            ("risk", "Risk", 75),
            ("reconciliation", "Reconciliation", 110),
            ("execution", "Execution", 95),
            ("bar", "Bar Time", 135),
            ("reason", "Final Reason", 190),
        ]:
            self.decision_table.heading(column, text=heading)
            self.decision_table.column(column, width=width, anchor=tk.W, stretch=True)
        self.decision_table.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        self._build_account_panel(panel)
        return panel

    def _build_account_panel(self, parent: tk.Widget) -> None:
        header = tk.Frame(parent, bg=SURFACE)
        header.pack(fill=tk.X, padx=18, pady=(0, 8))
        ttk.Label(header, text="Paper Account", style="PanelTitle.TLabel").pack(side=tk.LEFT)
        ttk.Button(header, text="Refresh", command=self._refresh_account_snapshot).pack(side=tk.RIGHT)
        ttk.Label(parent, textvariable=self.account_status, style="Muted.TLabel").pack(
            anchor=tk.W, padx=18, pady=(0, 8)
        )

        account = tk.Frame(parent, bg=SURFACE)
        account.pack(fill=tk.X, padx=18, pady=(0, 18))
        account.columnconfigure(0, weight=2)
        account.columnconfigure(1, weight=1)

        positions = tk.Frame(account, bg=SURFACE)
        positions.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 10))
        ttk.Label(positions, text="Positions", style="Muted.TLabel").pack(anchor=tk.W, pady=(0, 4))
        self.positions_table = ttk.Treeview(
            positions,
            columns=("symbol", "qty", "avg", "value", "pl"),
            show="headings",
            height=3,
            style="Decision.Treeview",
        )
        for column, heading, width in [
            ("symbol", "Symbol", 70),
            ("qty", "Qty", 95),
            ("avg", "Avg", 80),
            ("value", "Value", 90),
            ("pl", "P/L", 80),
        ]:
            self.positions_table.heading(column, text=heading)
            self.positions_table.column(column, width=width, anchor=tk.W, stretch=True)
        self.positions_table.pack(fill=tk.X)

        orders = tk.Frame(account, bg=SURFACE)
        orders.grid(row=0, column=1, sticky=tk.NSEW)
        ttk.Label(orders, text="Open Orders", style="Muted.TLabel").pack(anchor=tk.W, pady=(0, 4))
        self.orders_table = ttk.Treeview(
            orders,
            columns=("symbol", "side", "qty", "limit", "reason"),
            show="headings",
            height=3,
            style="Decision.Treeview",
        )
        for column, heading, width in [
            ("symbol", "Symbol", 70),
            ("side", "Side", 55),
            ("qty", "Qty", 85),
            ("limit", "Limit", 75),
            ("reason", "Reason", 100),
        ]:
            self.orders_table.heading(column, text=heading)
            self.orders_table.column(column, width=width, anchor=tk.W, stretch=True)
        self.orders_table.pack(fill=tk.X)
        self._update_account_tables(PaperAccountSnapshot([], [], "Account not refreshed"))

    def _build_log(self, parent: tk.Widget) -> None:
        row = ttk.Frame(parent, style="App.TFrame")
        row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row, text="Run Log", style="SmallMuted.TLabel").pack(side=tk.LEFT)
        ttk.Button(row, text="Clear Log", command=self._clear_output).pack(side=tk.RIGHT)

        log_panel = self._panel(parent)
        log_panel.pack(fill=tk.BOTH, expand=True)
        self.output = tk.Text(
            log_panel,
            wrap=tk.WORD,
            height=12,
            bg=LOG_BG,
            fg=INK,
            insertbackground=INK,
            relief=tk.FLAT,
            font=("Cascadia Mono", 10),
            padx=12,
            pady=12,
        )
        self.output.tag_configure("command", foreground=PRIMARY_DARK)
        self.output.tag_configure("blocked", foreground=AMBER)
        self.output.tag_configure("failed", foreground=DANGER)
        self.output.tag_configure("approved", foreground=SAFE)
        self.output.tag_configure("buy", foreground=PRIMARY)
        self.output.tag_configure("muted", foreground=MUTED)
        scrollbar = ttk.Scrollbar(log_panel, command=self.output.yview)
        self.output.configure(yscrollcommand=scrollbar.set)
        self.output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._append("NO SUBMIT: Ready. Check Signals runs a no-submit bounded paper pass.\n")

    def _panel(self, parent: tk.Widget, *, border: bool = True) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=SURFACE,
            highlightbackground=BORDER,
            highlightthickness=1 if border else 0,
        )

    def _metric(self, parent: tk.Widget, title: str, value: tk.StringVar) -> tk.Frame:
        frame = self._panel(parent)
        title_label = tk.Label(
            frame,
            text=title.upper(),
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8, "bold"),
            anchor=tk.W,
        )
        title_label.pack(anchor=tk.W, padx=13, pady=(11, 2))
        value_label = tk.Label(
            frame,
            textvariable=value,
            bg=SURFACE,
            fg=INK,
            font=("Segoe UI", 12, "bold"),
            anchor=tk.W,
        )
        value_label.pack(anchor=tk.W, padx=13, pady=(0, 11))
        self._metric_frames[title] = frame
        self._metric_value_labels[title] = value_label
        self._metric_title_labels[title] = title_label
        return frame

    def _browse_config(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select TradeOS config",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if selected:
            try:
                path = Path(selected).resolve().relative_to(Path.cwd().resolve())
                self.config_path.set(str(path))
            except ValueError:
                self.config_path.set(selected)

    def _sync_submit_button(self) -> None:
        if self.submit_ack.get() and not self._is_running() and not self._auto_submit_active:
            self.submit_button.state(["!disabled"])
        else:
            self.submit_button.state(["disabled"])
        if self._auto_submit_active:
            self.start_auto_button.state(["disabled"])
            self.stop_auto_button.state(["!disabled"])
            self._set_auto_badge(active=True)
        elif not self._is_running():
            self.start_auto_button.state(["!disabled"])
            self.stop_auto_button.state(["disabled"])
            self._set_auto_badge(active=False)
        else:
            self.start_auto_button.state(["disabled"])
            self.stop_auto_button.state(["disabled"])

    def _start_run(self, *, submit: bool, auto: bool = False) -> None:
        if self._is_running():
            return
        config_path = self.config_path.get().strip()
        if not config_path:
            messagebox.showerror("Missing config", "Choose a config file first.")
            return
        if submit and not self.submit_ack.get():
            messagebox.showwarning(
                "Paper submit disabled",
                "Enable paper-order submission before submitting.",
            )
            return
        credentials = load_paper_credentials()
        self.credential_status.set(credential_source())
        missing = missing_credentials(credentials["ALPACA_API_KEY"], credentials["ALPACA_SECRET_KEY"])
        if missing:
            message = "Missing Alpaca paper credentials: " + ", ".join(missing)
            self.run_state.set("Blocked")
            self.data_gate.set("Credentials")
            self.reconciliation_state.set("Not evaluated")
            self.signal_state.set("Not evaluated")
            self.risk_state.set("Not evaluated")
            self.execution_state.set("Blocked")
            self.latest_title.set("Credentials required")
            self.latest_detail.set(
                f"BLOCKED: Add ALPACA_API_KEY and ALPACA_SECRET_KEY to {DEFAULT_ENV_FILE}, "
                "or set them in your environment, then run Check Signals again."
            )
            self._append(f"\nBLOCKED: {message}\n")
            return

        self.run_state.set("Running")
        self.data_gate.set("Checking")
        self.reconciliation_state.set("Pending")
        self.signal_state.set("Pending")
        self.risk_state.set("Pending")
        self.execution_state.set("Pending")
        self.order_mode.set("Paper submit enabled" if submit else "No submit")
        self.latest_title.set("Running signal check")
        self.latest_detail.set(
            "Waiting for Alpaca data, reconciliation, strategy output, and risk gate result."
        )
        self._refresh_status_cards()
        self._set_buttons_running(True)
        if auto:
            self.auto_status.set("Auto running")
        self._append(
            "\n> "
            + " ".join(
                ["python", "-m", "tradeos.main", *command_args(config_path, submit_paper_orders=submit)]
            )
            + "\n"
        )
        self._worker = threading.Thread(
            target=self._run_command,
            args=(
                config_path,
                submit,
                credentials,
            ),
            daemon=True,
        )
        self._worker.start()

    def _run_command(self, config_path: str, submit: bool, credentials: dict[str, str]) -> None:
        buffer = io.StringIO()
        exit_code = 0
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                exit_code = tradeos_main(
                    command_args(config_path, submit_paper_orders=submit),
                    env=credentials,
                )
        except SystemExit as exc:
            exit_code = int(exc.code or 0)
        except Exception as exc:
            exit_code = 1
            buffer.write(f"{type(exc).__name__}: {exc}\n")
        self.root.after(0, self._finish_run, exit_code, buffer.getvalue())

    def _finish_run(self, exit_code: int, output: str) -> None:
        self._append(output if output else "(no output)\n")
        summary = dashboard_summary(exit_code, output)
        self.run_state.set(summary.run_state)
        self.data_gate.set(summary.data_gate)
        self.reconciliation_state.set(summary.reconciliation)
        self.signal_state.set(summary.signal)
        self.risk_state.set(summary.risk)
        self.execution_state.set(summary.execution)
        self.order_mode.set(summary.order_mode)
        self.latest_title.set(summary.latest_title)
        self.latest_detail.set(summary.latest_detail)
        self._update_decision_table(output)
        self._refresh_status_cards()
        self._set_buttons_running(False)
        self._refresh_account_snapshot()
        self._schedule_next_auto_submit()

    def _set_buttons_running(self, running: bool) -> None:
        if running:
            self.check_button.state(["disabled"])
            self.submit_button.state(["disabled"])
            self.start_auto_button.state(["disabled"])
            return
        self.check_button.state(["!disabled"])
        self._sync_submit_button()

    def _start_auto_submit(self) -> None:
        if self._is_running() or self._auto_submit_active:
            return
        try:
            auto_interval_seconds(self.auto_interval.get())
        except ValueError as exc:
            messagebox.showerror("Invalid auto interval", str(exc))
            return
        if not self.submit_ack.get():
            messagebox.showwarning(
                "Paper submit disabled",
                "Enable paper-order submission before starting auto submit.",
            )
            return
        self._auto_submit_active = True
        self.auto_status.set("Auto running")
        self._append(
            f"\nAUTO SUBMIT: Starting bounded paper passes every {self.auto_interval.get().strip()} minutes.\n"
        )
        self._sync_submit_button()
        self._start_run(submit=True, auto=True)

    def _stop_auto_submit(self) -> None:
        self._auto_submit_active = False
        if self._auto_after_id is not None:
            self.root.after_cancel(self._auto_after_id)
            self._auto_after_id = None
        self.auto_status.set("Auto off")
        self._append("\nAUTO SUBMIT: Stopped. Any in-flight paper pass will finish normally.\n")
        self._sync_submit_button()

    def _schedule_next_auto_submit(self) -> None:
        if not self._auto_submit_active:
            self.auto_status.set("Auto off")
            return
        try:
            delay_seconds = auto_interval_seconds(self.auto_interval.get())
        except ValueError as exc:
            self._auto_submit_active = False
            self.auto_status.set("Auto stopped")
            self._append(f"\nAUTO SUBMIT: Stopped because interval is invalid: {exc}\n")
            self._sync_submit_button()
            return
        self.auto_status.set(f"Next auto run in {delay_seconds // 60} min")
        self._auto_after_id = self.root.after(delay_seconds * 1000, self._run_scheduled_auto_submit)
        self._sync_submit_button()

    def _run_scheduled_auto_submit(self) -> None:
        self._auto_after_id = None
        if not self._auto_submit_active:
            return
        self._start_run(submit=True, auto=True)

    def _is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _append(self, text: str) -> None:
        for line in text.splitlines(keepends=True):
            self.output.insert(tk.END, line, _line_tag(line))
        self.output.see(tk.END)

    def _update_decision_table(self, output: str) -> None:
        self.decision_table.delete(*self.decision_table.get_children())
        decisions = cycle_decisions(output)
        if not decisions:
            self.decision_table.insert(
                "",
                tk.END,
                values=("-", "Not evaluated", "-", "Not evaluated", "Not evaluated", "-", "-"),
            )
            return
        for decision in decisions:
            self.decision_table.insert(
                "",
                tk.END,
                values=(
                    f"{decision.symbol} {decision.action}",
                    _short_text(_display_signal_reason(decision.signal), 30),
                    decision.risk,
                    decision.reconciliation,
                    decision.execution,
                    _display_bar_time(decision.bar_time),
                    _short_text(_display_signal_reason(decision.reason), 34),
                ),
            )

    def _clear_output(self) -> None:
        self.output.delete("1.0", tk.END)

    def _refresh_account_snapshot(self) -> None:
        if self._account_worker is not None and self._account_worker.is_alive():
            return
        credentials = load_paper_credentials()
        self.credential_status.set(credential_source())
        if missing_credentials(credentials["ALPACA_API_KEY"], credentials["ALPACA_SECRET_KEY"]):
            self._update_account_tables(PaperAccountSnapshot([], [], "Credentials missing"))
            return
        self.account_status.set("Refreshing account...")
        self._account_worker = threading.Thread(
            target=self._run_account_snapshot,
            args=(credentials,),
            daemon=True,
        )
        self._account_worker.start()

    def _run_account_snapshot(self, credentials: dict[str, str]) -> None:
        snapshot = paper_account_snapshot(credentials)
        self.root.after(0, self._update_account_tables, snapshot)

    def _update_account_tables(self, snapshot: PaperAccountSnapshot) -> None:
        self.account_status.set(snapshot.status)
        self.positions_table.delete(*self.positions_table.get_children())
        if snapshot.positions:
            for row in snapshot.positions:
                self.positions_table.insert("", tk.END, values=row)
        else:
            self.positions_table.insert("", tk.END, values=("-", "-", "-", "-", "-"))

        self.orders_table.delete(*self.orders_table.get_children())
        if snapshot.open_orders:
            for row in snapshot.open_orders:
                self.orders_table.insert("", tk.END, values=row)
        else:
            self.orders_table.insert("", tk.END, values=("-", "-", "-", "-", "-"))

    def _refresh_status_cards(self) -> None:
        values = {
            "Run State": self.run_state.get(),
            "Data Gate": self.data_gate.get(),
            "Reconciliation": self.reconciliation_state.get(),
            "Signal": self.signal_state.get(),
            "Risk": self.risk_state.get(),
            "Execution": self.execution_state.get(),
            "Order Mode": self.order_mode.get(),
        }
        for title, value in values.items():
            bg, fg = _status_colors(value)
            frame = self._metric_frames.get(title)
            value_label = self._metric_value_labels.get(title)
            title_label = self._metric_title_labels.get(title)
            if frame is not None:
                frame.configure(bg=bg, highlightbackground=bg)
            if value_label is not None:
                value_label.configure(bg=bg, fg=fg)
            if title_label is not None:
                title_label.configure(bg=bg)

    def _set_auto_badge(self, *, active: bool) -> None:
        if active:
            self.auto_badge.configure(bg=SAFE_BG, fg=SAFE)
        else:
            self.auto_badge.configure(bg=NEUTRAL_BG, fg=NEUTRAL)


def _parse_cycle_line(line: str) -> dict[str, str] | None:
    match = CYCLE_LINE.match(line)
    return match.groupdict() if match else None


def _short_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "..."


def _position_row(position) -> tuple[str, str, str, str, str]:
    return (
        str(position.symbol),
        _format_decimal(position.quantity),
        _money(position.avg_entry_price),
        _money(position.market_value),
        _signed_money(position.unrealized_pl),
    )


def _order_row(order) -> tuple[str, str, str, str, str]:
    side = getattr(order.side, "value", str(order.side))
    limit_price = order.limit_price if order.limit_price is not None else Decimal("0")
    return (
        str(order.symbol),
        str(side).upper(),
        _format_decimal(order.quantity),
        _money(limit_price),
        _short_text(_display_signal_reason(str(order.reason)), 18),
    )


def _format_decimal(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.0001")) if value != value.to_integral() else value
    return f"{normalized.normalize():f}"


def _money(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01')):,.2f}"


def _signed_money(value: Decimal) -> str:
    if value < Decimal("0"):
        return f"-{_money(abs(value))}"
    if value > Decimal("0"):
        return f"+{_money(value)}"
    return _money(value)


def _most_important_cycle(cycles: list[dict[str, str]]) -> dict[str, str] | None:
    for cycle in cycles:
        if cycle["action"] == "buy" and cycle["risk"] == "approved":
            return cycle
    for cycle in cycles:
        if cycle["action"] == "buy":
            return cycle
    for cycle in cycles:
        if cycle["action"] != "hold":
            return cycle
    return cycles[0] if cycles else None


def _signal_status(cycles: list[dict[str, str]]) -> str:
    if any(cycle["action"] == "buy" for cycle in cycles):
        return "Buy signal"
    if any(cycle["action"] == "sell" for cycle in cycles):
        return "Sell signal"
    return "No signal"


def _risk_status(cycles: list[dict[str, str]]) -> str:
    if any(cycle["risk"] == "approved" for cycle in cycles):
        return "Approved"
    if any(cycle["action"] != "hold" and cycle["risk"] == "blocked" for cycle in cycles):
        return "Blocked"
    return "No action"


def _run_state_for_cycle(action: str, risk: str, order_mode: str) -> str:
    if action == "buy" and risk == "approved":
        if order_mode == "Paper submit enabled":
            return "Executable Paper"
        return "Signal Approved"
    return "Complete"


def _reconciliation_status(cycles: list[dict[str, str]]) -> str:
    values = [
        _display_reconciliation(cycle.get("reconciliation"))
        for cycle in cycles
        if cycle.get("reconciliation")
    ]
    if not values:
        return "Unknown"
    if any(value == "Dirty / Blocked" for value in values):
        return "Dirty / Blocked"
    if all(value == "Clean" for value in values):
        return "Clean"
    return "Unknown"


def _execution_status(cycles: list[dict[str, str]], order_mode: str) -> str:
    if cycles and all(cycle["action"] == "hold" for cycle in cycles):
        return "No order"
    values = [
        _display_execution(cycle.get("execution"))
        for cycle in cycles
        if cycle.get("execution")
    ]
    if not values:
        return order_mode if order_mode != "No submit" else "Unknown"
    if any(value == "Filled" for value in values):
        return "Filled"
    if any(value == "Partially filled" for value in values):
        return "Partially filled"
    if any(value == "Submitted" for value in values):
        return "Submitted"
    if any(value == "Rejected" for value in values):
        return "Rejected"
    if any(value == "Canceled" for value in values):
        return "Canceled"
    if any(value == "Expired" for value in values):
        return "Expired"
    if any(value == "Blocked" for value in values):
        return "Blocked"
    if any(value == "No submit" for value in values):
        return "No submit"
    if any(value == "Not evaluated" for value in values):
        return "Not evaluated"
    return "Unknown"


def _risk_detail(action: str, risk: str) -> str:
    if action == "hold":
        return "No action"
    if risk == "approved":
        return "Approved"
    if risk == "blocked":
        return "Blocked"
    return risk


def _display_cycle_execution(action: str, value: str | None) -> str:
    if action == "hold" and value == "blocked":
        return "No order"
    return _display_execution(value)


def _display_reconciliation(value: str | None) -> str:
    if value == "clean":
        return "Clean"
    if value == "dirty":
        return "Dirty / Blocked"
    if value:
        return "Unknown"
    return "Unknown"


def _display_execution(value: str | None) -> str:
    if value == "filled":
        return "Filled"
    if value == "partially_filled":
        return "Partially filled"
    if value == "submitted":
        return "Submitted"
    if value == "rejected":
        return "Rejected"
    if value == "canceled":
        return "Canceled"
    if value == "expired":
        return "Expired"
    if value == "blocked":
        return "Blocked"
    if value == "no_submit":
        return "No submit"
    if value == "not_evaluated":
        return "Not evaluated"
    if value:
        return "Unknown"
    return "Unknown"


def _display_signal_reason(value: str) -> str:
    labels = {
        "momentum_no_signal": "No signal",
        "momentum_breakout_entry": "Breakout entry",
        "momentum_stop_loss_triggered": "Stop loss",
        "hold_signal_no_order": "Hold, no order",
        "dry_run_order_not_submitted": "Review only",
        "order_submitted": "Order submitted",
        "order_filled": "Order filled",
        "order_rejected": "Order rejected",
        "order_canceled": "Order canceled",
        "order_expired": "Order expired",
        "order_partially_filled": "Partially filled",
        "buy_signal_approved": "Buy approved",
        "sell_signal_approved": "Sell approved",
        "force_flatten_at_triggered": "Force flatten",
        "max_daily_loss_triggered": "Daily loss exit",
        "buy_blocked_outside_entry_window": "Outside entry window",
        "buy_blocked_after_no_entry_time": "After no-entry time",
        "buy_blocked_daily_loss_limit": "Daily loss limit",
        "position_reconciliation_dirty": "Position mismatch",
    }
    return labels.get(value, value)


def _display_bar_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return _short_text(value, 18)
    return parsed.strftime("%b %d %H:%M")


def _status_colors(value: str) -> tuple[str, str]:
    lowered = value.lower()
    if any(term in lowered for term in ["failed", "dirty", "credentials", "rejected", "expired", "canceled"]):
        return DANGER_BG, DANGER
    if any(term in lowered for term in ["blocked", "review", "checking", "pending"]):
        return AMBER_BG, AMBER
    if any(term in lowered for term in ["complete", "aligned", "clean", "approved", "submitted", "enabled"]):
        return SAFE_BG, SAFE
    return NEUTRAL_BG, NEUTRAL


def _order_mode(output: str) -> str:
    if "paper order submission: yes" in output:
        return "Paper submit enabled"
    return "No submit"


def _warning_text(prefix: str, detail: str) -> str:
    return f"{prefix}: {detail}"


def _line_containing(output: str, needle: str) -> str | None:
    for line in output.splitlines():
        if needle in line:
            return line
    return None


def _first_nonempty_line(output: str) -> str | None:
    for line in output.splitlines():
        if line.strip():
            return line.strip()
    return None


def _line_tag(line: str) -> str:
    lowered = line.lower()
    if line.startswith(">"):
        return "command"
    if "failed" in lowered or "error" in lowered:
        return "failed"
    if "blocked" in lowered:
        return "blocked"
    if "approved" in lowered or "complete" in lowered:
        return "approved"
    if " buy | " in lowered or "momentum_breakout_entry" in lowered:
        return "buy"
    if not line.strip():
        return "muted"
    return ""


def main() -> int:
    root = tk.Tk()
    TradeOSDashboard(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
