"use strict";
const byId = id => document.getElementById(id);
const money = value => new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(Number(value));
let latestRun = null;
let fileText = null;
let uploadVersion = 0;
let busy = false;
let stale = false;
function status(message, kind = "") { byId("status").textContent = message; byId("status").className = kind; }
function invalidate() {
  stale = !!latestRun;
  if (stale) byId("run-label").textContent = "PREVIOUS RUN · SETTINGS CHANGED";
  byId("download-json").disabled = stale || !latestRun;
  byId("download-csv").disabled = stale || !latestRun;
}
byId("experiment").addEventListener("input", invalidate);
byId("source").addEventListener("change", () => {
  const upload = byId("source").value === "csv";
  byId("upload-fields").hidden = !upload;
  byId("source-hint").hidden = upload;
  invalidate();
});
byId("csv-file").addEventListener("change", async () => {
  const version = ++uploadVersion;
  fileText = null;
  invalidate();
  const file = byId("csv-file").files[0];
  if (!file) return;
  if (file.size > 2000000) { status("Choose a CSV smaller than 2 MB.", "error"); return; }
  try {
    const text = await file.text();
    if (version !== uploadVersion) return;
    fileText = text;
    status(`${file.name} is ready. Run the backtest to validate its contents.`);
  } catch { status("Could not read the selected file. Please choose it again.", "error"); }
});
byId("experiment").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  const payload = {strategy: byId("strategy").value, entry_percent: byId("entry").value,
    exit_percent: byId("exit").value, commission: byId("commission").value, slippage_bps: byId("slippage").value};
  if (byId("source").value === "csv") {
    if (!fileText) { status("Choose a nonempty CSV and wait for it to finish loading.", "error"); return; }
    payload.csv = fileText;
  }
  busy = true;
  const controls = [...byId("experiment").querySelectorAll("input,select,button")];
  controls.forEach(control => { control.disabled = true; });
  byId("download-json").disabled = true; byId("download-csv").disabled = true;
  byId("run").textContent = "Running simulation…";
  byId("run-label").textContent = "RUNNING";
  status("Processing bars through strategy, risk checks, and simulated accounting…");
  try {
    const response = await fetch("api/backtest", {method: "POST", headers: {"Content-Type": "application/json", "X-TradeOS-Request": "research"}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The run could not complete.");
    latestRun = data; stale = false;
    render(data);
    status(`Complete: ${data.summary.total_cycles.toLocaleString()} cycles. No broker orders submitted.`, "success");
    byId("download-json").disabled = false; byId("download-csv").disabled = false;
  } catch (error) {
    stale = true;
    byId("run-label").textContent = latestRun ? "PREVIOUS RUN · NEW RUN FAILED" : "RUN FAILED";
    status(error.message || "Connection failed. Check that the server is running.", "error");
  } finally {
    busy = false; controls.forEach(control => { control.disabled = false; });
    byId("run").textContent = "Run backtest →";
  }
});
function render(data) {
  const s = data.summary;
  byId("run-label").textContent = `${data.configuration.strategy.strategy_mode.replaceAll("_", " ").toUpperCase()} · COMPLETE`;
  byId("pnl").textContent = money(s.total_pnl);
  byId("pnl").className = Number(s.total_pnl) < 0 ? "negative" : "positive";
  byId("drawdown").textContent = money(s.max_drawdown);
  byId("trade-count").textContent = s.closed_trades;
  byId("win-rate").textContent = s.closed_trades ? `${(Number(s.win_rate) * 100).toFixed(1)}% win rate` : "No closed trades";
  byId("costs").textContent = money(s.total_costs);
  byId("dataset-description").textContent = `${data.dataset.kind === "synthetic" ? "Synthetic sample" : "Uploaded dataset"} · ${data.dataset.symbols.join(" / ")} · ${data.dataset.bars.toLocaleString()} bars`;
  byId("range").textContent = `${data.dataset.start.slice(0, 10)} — ${data.dataset.end.slice(0, 10)} / UTC`;
  byId("cycles").textContent = `${s.total_cycles.toLocaleString()} cycles`;
  byId("warnings").replaceChildren();
  const heading = document.createElement("strong"); heading.textContent = "Read the results in context."; byId("warnings").append(heading);
  data.warnings.forEach(warning => { const p = document.createElement("p"); p.textContent = warning; byId("warnings").append(p); });
  const rows = byId("trade-rows"); rows.replaceChildren();
  if (!data.trades.length) {
    const row = rows.insertRow(); const cell = row.insertCell(); cell.colSpan = 7; cell.className = "table-empty";
    cell.textContent = "No trades closed in this run. Inspect risk decisions or try different thresholds.";
  }
  data.trades.forEach(trade => {
    const row = rows.insertRow();
    [trade.symbol, trade.entry_time.replace("T", " ").slice(0, 16), trade.exit_time.replace("T", " ").slice(0, 16),
      Number(trade.quantity).toFixed(4), `${money(trade.entry_price)} / ${money(trade.exit_price)}`, money(trade.pnl), trade.exit_reason.replaceAll("_", " ")]
      .forEach((value, index) => { const cell = row.insertCell(); cell.textContent = value; if (index === 5) cell.className = Number(trade.pnl) < 0 ? "negative" : "positive"; });
  });
  byId("risk-reasons").replaceChildren();
  Object.entries(data.risk_reasons).sort((a, b) => b[1] - a[1]).forEach(([reason, count]) => {
    const row = document.createElement("div"); row.className = "risk-row";
    const label = document.createElement("span"); label.textContent = reason.replaceAll("_", " ");
    const number = document.createElement("strong"); number.textContent = count.toLocaleString();
    row.append(label, number); byId("risk-reasons").append(row);
  });
  byId("reconciliation").textContent = `${s.dirty_reconciliation_count} dirty reconciliations`;
  drawChart(data.curve);
}
function drawChart(curve) {
  const svg = byId("chart"); svg.replaceChildren(); svg.removeAttribute("hidden"); byId("chart-empty").hidden = true;
  const values = [0, ...curve.map(point => Number(point.pnl))];
  let low = Math.min(...values), high = Math.max(...values);
  if (high === low) { low -= 1; high += 1; }
  const pad = (high - low) * 0.12; low -= pad; high += pad;
  const x = index => 65 + index / Math.max(1, values.length - 1) * 710;
  const y = value => 220 - (value - low) / (high - low) * 195;
  function node(tag, attrs, text) {
    const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => item.setAttribute(key, value));
    if (text !== undefined) item.textContent = text; svg.append(item); return item;
  }
  for (let i = 0; i < 5; i++) {
    const value = low + (high - low) * i / 4;
    node("line", {x1: 65, x2: 775, y1: y(value), y2: y(value), class: "grid"});
    node("text", {x: 55, y: y(value) + 4, "text-anchor": "end"}, money(value));
  }
  node("line", {x1: 65, x2: 775, y1: y(0), y2: y(0), class: "zero"});
  node("polyline", {points: values.map((value, index) => `${x(index)},${y(value)}`).join(" "), class: "line"});
  node("text", {x: 65, y: 246}, "Start");
  node("text", {x: 775, y: 246, "text-anchor": "end"}, `${curve.length.toLocaleString()} bars processed`);
}
function download(contents, type, filename) {
  const url = URL.createObjectURL(new Blob([contents], {type}));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
byId("download-json").addEventListener("click", () => {
  if (latestRun && !stale) download(JSON.stringify(latestRun, null, 2), "application/json", "tradeos-offline-run.json");
});
byId("download-csv").addEventListener("click", () => {
  if (!latestRun || stale) return;
  const fields = ["symbol", "entry_time", "exit_time", "quantity", "entry_price", "exit_price", "pnl", "exit_reason"];
  const escape = value => `"${String(value).replaceAll('"', '""')}"`;
  const rows = [fields, ...latestRun.trades.map(trade => fields.map(field => trade[field]))];
  download(rows.map(row => row.map(escape).join(",")).join("\r\n"), "text/csv", "tradeos-simulated-trades.csv");
});

// Paper state is displayed as text, including broker-provided symbols/reasons.
async function refreshPaper() {
  const response = await fetch('/api/paper');
  const state = await response.json();
  document.getElementById('paper-state').textContent = JSON.stringify(state, null, 2);
  const account = state.account || {};
  document.getElementById('paper-summary').textContent = [
    'PAPER · ' + state.state, 'Connection: ' + (state.connection || 'unchecked'),
    'Market: ' + (state.market || 'unchecked'), 'Data: ' + (state.data || 'unchecked'),
    'Strategy: ' + (state.strategy || 'configured on server'),
    'Equity: ' + (account.equity || '—'), 'Daily change: ' + (state.daily_equity_change || '—'),
    'Unrealized P&L: ' + (state.unrealized_pnl || '—'), state.error || ''
  ].filter(Boolean).join(' | ');
  const rows = document.getElementById('paper-decisions');
  rows.replaceChildren();
  for (const decision of state.decisions || []) {
    const row = document.createElement('tr');
    for (const value of [decision.signal.symbol, decision.signal.action === 'hold' ? 'no trade' : decision.signal.action,
      decision.signal.reason, decision.reason + ' / ' + decision.risk_decision.reason + ' ' + decision.risk_decision.blocked_rules.join(', ')]) {
      const cell = document.createElement('td'); cell.textContent = value.replaceAll('_', ' '); row.append(cell);
    }
    rows.append(row);
  }
}
document.querySelectorAll('[data-paper]').forEach(button => {
  button.addEventListener('click', async () => {
    const action = button.dataset.paper;
    const message = document.getElementById('paper-message');
    if (action === 'start' && !document.getElementById('paper-confirm').checked) {
      message.textContent = 'Confirm PAPER order submission before starting.'; return;
    }
    button.disabled = true;
    try {
      const response = await fetch('/api/paper/' + action, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-TradeOS-Request': 'paper'},
        body: JSON.stringify({interval: Number(document.getElementById('paper-interval').value), cycles: Number(document.getElementById('paper-cycles').value), confirm_paper_orders: document.getElementById('paper-confirm').checked})
      });
      const result = await response.json();
      message.textContent = result.error || ('Paper ' + action + ' requested.');
      if (action === 'start') document.getElementById('paper-confirm').checked = false;
      await refreshPaper();
    } catch (_) { message.textContent = 'Connection error. Check server status; an in-flight paper order may still complete.'; }
    finally { button.disabled = false; }
  });
});
setInterval(() => refreshPaper().catch(() => {}), 3000);
refreshPaper().catch(() => {});
