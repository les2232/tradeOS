"use strict";
// Session analyst panel. All server and model text is inserted with textContent, never as HTML.
(() => {
  const $ = id => document.getElementById(id);
  let busy = false;

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function status(message, kind = "") { $("analyst-status").textContent = message; $("analyst-status").className = kind; }
  function refs(list) { return list && list.length ? el("span", list.join(", "), "analyst-refs") : null; }
  function list(title, items, render) {
    if (!items || !items.length) return null;
    const wrap = el("div");
    wrap.append(el("h4", title));
    const ul = el("ul");
    items.forEach(item => ul.append(render(item)));
    wrap.append(ul);
    return wrap;
  }

  function syncFields() {
    const kind = $("analyst-kind").value;
    $("analyst-symbol-field").hidden = kind !== "symbol";
    $("analyst-experiment-field").hidden = kind !== "comparison";
  }

  async function loadSessions() {
    try {
      const response = await fetch("api/analyst/sessions");
      const data = await response.json();
      const select = $("analyst-session");
      const previous = select.value;
      select.replaceChildren();
      data.sessions.forEach(session => {
        const label = `${(session.started_at || "").slice(0, 16).replace("T", " ")} UTC · ${session.strategy || "?"} · ${session.end_state || (session.recorded_end ? "ended" : "no end recorded")}`;
        const option = el("option", label);
        option.value = session.session_id;
        select.append(option);
      });
      const other = el("option", "Activity outside sessions (manual checks)");
      other.value = "unsessioned";
      select.append(other);
      if ([...select.options].some(option => option.value === previous)) select.value = previous;
      const experiments = $("analyst-experiment");
      const previousExperiment = experiments.value;
      experiments.replaceChildren();
      data.research.forEach(result => {
        const option = el("option", `${result.experiment_id} · ${result.strategy_mode} · ${result.num_trades} trades · ${result.dataset_id}`);
        option.value = result.experiment_id;
        experiments.append(option);
      });
      if (!data.research.length) { const none = el("option", "No saved research results"); none.value = ""; experiments.append(none); }
      else if (previousExperiment) experiments.value = previousExperiment;
      $("analyst-claude").disabled = !data.claude_installed;
      if (!data.claude_installed) $("analyst-claude").title = 'Install the analyst extra: pip install -e ".[analyst]"';
      status(data.sessions.length ? `${data.sessions.length} recorded session(s).` : "No PAPER sessions recorded yet. Manual checks can still be explained.");
    } catch {
      status("Could not load sessions. Check that the server is running.", "error");
    }
  }

  function renderAnswer(label, answer) {
    const box = el("div", null, "analyst-answer");
    const e = answer.explanation;
    box.append(el("h3", answer.model ? `${label} · ${answer.model}${answer.fallback_used ? " (fallback model)" : ""}` : label));
    box.append(el("p", e.summary));
    const findings = list("Findings", e.findings, item => { const li = el("li", item.statement); const r = refs(item.refs); if (r) li.append(r); return li; });
    const hypotheses = list("Hypotheses (not proven)", e.hypotheses, item => {
      const li = el("li", item.statement); const r = refs(item.refs); if (r) li.append(r);
      if (item.how_to_check) li.append(el("div", `How to check: ${item.how_to_check}`, "muted"));
      return li;
    });
    const caveats = list("Caveats", e.caveats, item => el("li", item));
    const steps = list("Next steps", e.next_steps, item => el("li", item));
    [findings, hypotheses, caveats, steps].forEach(part => { if (part) box.append(part); });
    if (answer.rejected && answer.rejected.length) {
      box.append(el("p", `${answer.rejected.length} statement(s) were removed because they cited events that are not in the evidence.`, "muted"));
    }
    return box;
  }

  function renderComparison(comparison) {
    const box = el("div", null, "analyst-answer");
    box.append(el("h3", `Comparison with ${comparison.experiment_id}: ${comparison.valid ? "valid" : "not valid"}`));
    const ul = el("ul");
    comparison.checks.forEach(check => ul.append(el("li", `${check.ok ? "✓" : "✗"} ${check.check}: ${check.detail}`, check.ok ? "" : "analyst-fail")));
    box.append(ul);
    if (comparison.metrics) {
      const table = el("table");
      const head = el("tr"); ["Metric", "Session", "Research"].forEach(text => head.append(el("th", text)));
      table.append(head);
      Object.entries(comparison.metrics).forEach(([name, pair]) => {
        const row = el("tr");
        [name.replaceAll("_", " "), pair.session ?? "n/a", pair.research ?? "n/a"].forEach(text => row.append(el("td", String(text))));
        table.append(row);
      });
      box.append(table);
    } else {
      box.append(el("p", comparison.suppressed_reason, "muted"));
    }
    return box;
  }

  async function ask(useClaude) {
    if (busy) return;
    const payload = {kind: $("analyst-kind").value, session_id: $("analyst-session").value || null, use_claude: useClaude};
    if (payload.kind === "symbol") payload.symbol = $("analyst-symbol").value.trim().toUpperCase();
    if (payload.kind === "comparison") payload.experiment_id = $("analyst-experiment").value;
    busy = true;
    $("analyst-local").disabled = true; $("analyst-claude").disabled = true;
    status(useClaude ? "Building evidence and asking Claude… this can take a minute." : "Building evidence from the event log…");
    try {
      const response = await fetch("api/analyst/explain", {method: "POST", headers: {"Content-Type": "application/json", "X-TradeOS-Request": "analyst"}, body: JSON.stringify(payload)});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "The analyst could not answer.");
      const output = $("analyst-output");
      output.replaceChildren(el("p", data.question, "muted"));
      if (data.ai) output.append(renderAnswer("Claude", data.ai));
      output.append(renderAnswer("From the evidence (rule-based)", data.local));
      if (data.comparison) output.append(renderComparison(data.comparison));
      $("analyst-evidence").textContent = JSON.stringify(data.evidence, null, 2);
      status("Done. Refs such as E12 are event numbers in the PAPER event log, in recorded order.", "success");
    } catch (error) {
      status(error.message || "Connection failed.", "error");
    } finally {
      busy = false;
      $("analyst-local").disabled = false;
      loadSessions();
    }
  }

  $("analyst-kind").addEventListener("change", syncFields);
  $("analyst-local").addEventListener("click", () => ask(false));
  $("analyst-claude").addEventListener("click", () => ask(true));
  syncFields();
  loadSessions();
})();
