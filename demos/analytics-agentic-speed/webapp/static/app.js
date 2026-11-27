// Analytics at Agentic Speed live view: renders the agent's structured progress events.
"use strict";

const $ = (id) => document.getElementById(id);
const SEQ = Array.from({ length: 9 }, (_, i) => `var(--seq-${i})`);
const BINS = [[0, 5], [5, 10], [10, 20], [20, 50], [50, 100], [100, 200], [200, 500], [500, Infinity]];
const BIN_LABELS = ["<5", "5–10", "10–20", "20–50", "50–100", "100–200", "200–500", "500+"];
const PHASE_NAMES = { profile: "Profile the table", plan: "Model: plan the sweep", lookups: "Look up facts",
                      analyse: "Model: read the results", verify: "Re-run the winner" };

let S = null;            // state of the current run
let questions = [];
let es = null;           // EventSource
let ticker = null;

// ------------------------------------------------------------------ helpers
const fmtS = (ms) => (ms == null ? "–" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`);
const fmtMs = (ms) => (ms == null ? "–" : ms < 10 ? ms.toFixed(1) : Math.round(ms).toString());
const pct = (x) => `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtVal = (v) => (Array.isArray(v) ? v.join(",") : typeof v === "number" ? (Number.isInteger(v) ? v : +v.toPrecision(4)) : v);
function quantile(arr, q) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b), k = (s.length - 1) * q, lo = Math.floor(k), hi = Math.ceil(k);
  return s[lo] + (s[hi] - s[lo]) * (k - lo);
}
function norm(v) {
  if (Array.isArray(v)) return v.map(norm).join("|");
  const n = Number(v);
  return Number.isFinite(n) && String(v).trim() !== "" ? String(+n.toFixed(6)) : String(v).trim().toUpperCase();
}

function seg(id) {
  const el = $(id);
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    el.querySelectorAll("button").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", x === b); });
  });
  return () => +el.querySelector("button.on").dataset.v;
}
const breadth = seg("seg-breadth");
const maxRounds = seg("seg-rounds");

// ------------------------------------------------------------------ tooltip
const tip = $("tip");
function showTip(html, ev) {
  tip.innerHTML = html; tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
  tip.style.left = `${x}px`; tip.style.top = `${y}px`;
}
const hideTip = () => { tip.hidden = true; };

// ------------------------------------------------------------------ setup
async function init() {
  try {
    const info = await (await fetch("/api/info")).json();
    questions = info.questions;
    $("conn-text").textContent = info.ping_ms != null
      ? `ClickHouse Cloud ${info.host} · ${info.table} · round trip ${info.ping_ms} ms · ${info.model} (${info.effort})`
      : `ClickHouse unreachable: ${info.ping_error || "unknown error"}`;
    const sel = $("q-select");
    sel.innerHTML = questions.map((q) => `<option value="${esc(q.id)}">${esc(q.id)}</option>`).join("");
    sel.value = questions.some((q) => q.id === "q-padding") ? "q-padding" : (questions[0] || {}).id;
    const showQ = () => { const q = questions.find((x) => x.id === sel.value); $("q-text").textContent = q ? q.text : ""; };
    sel.addEventListener("change", showQ); showQ();
  } catch (e) {
    $("conn-text").textContent = `server error: ${e}`;
  }
  $("run").addEventListener("click", start);
  renderHist();
}

function reset() {
  S = { t0: performance.now(), phases: {}, live: null, rounds: {}, server: [], dbMs: 0, llmMs: 0,
        llmCalls: 0, cost: 0, scen: 0, done: null, sweepStart: null, sweepCount: 0, steps: {} };
  $("steps").innerHTML = ""; $("rounds").innerHTML = "";
  $("answer").innerHTML = '<span class="muted">The agent is working…</span>';
  ["t-scen", "t-llm"].forEach((id) => ($(id).textContent = "0"));
  ["t-qps", "t-p50", "t-share"].forEach((id) => ($(id).textContent = "–"));
  $("t-scen-f").innerHTML = "&nbsp;"; $("t-llm-f").innerHTML = "&nbsp;"; $("t-qps-f").innerHTML = "&nbsp;";
  renderHist(); renderSplit();
}

async function start() {
  const ask = $("q-ask").value.trim();
  const body = { max_scenarios: breadth(), rounds: maxRounds(), concurrency: 32 };
  if (ask && $("q-ask").closest("details").open) body.ask = ask; else body.question_id = $("q-select").value;
  $("run").disabled = true; $("run").textContent = "Running…";
  reset();
  const r = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { finish(); $("answer").textContent = `Could not start: ${await r.text()}`; return; }
  const { run_id } = await r.json();
  if (es) es.close();
  es = new EventSource(`/api/runs/${run_id}/events`);
  es.onmessage = (m) => handle(JSON.parse(m.data));
  es.onerror = () => { if (S && S.done) es.close(); };
  clearInterval(ticker);
  ticker = setInterval(tick, 200);
}

function finish() {
  $("run").disabled = false; $("run").textContent = "Run the agent";
  clearInterval(ticker); ticker = null; tick();
}

// ------------------------------------------------------------------ events
function handle(e) {
  switch (e.type) {
    case "start": addStep("start", "other", `Question received · up to ${e.config.max_scenarios_per_round} scenarios × ${e.config.max_rounds} rounds`); break;
    case "phase": onPhase(e); break;
    case "round_start": onRoundStart(e); break;
    case "query": onQuery(e); break;
    case "round_end": onRoundEnd(e); break;
    case "decision": onDecision(e); break;
    case "done": onDone(e.result); break;
    case "fatal": addStep("fatal", "other", `Run failed: ${e.error}`); break;
    case "closed": finish(); if (es) es.close(); break;
  }
}

function onPhase(e) {
  const kind = e.phase === "plan" || e.phase === "analyse" ? "llm" : "db";
  const key = `${e.phase}-${(S.phases[e.phase] || 0) + (e.state === "start" ? 1 : 0)}`;
  if (e.state === "start") {
    S.phases[e.phase] = (S.phases[e.phase] || 0) + 1;
    S.live = { phase: e.phase, kind, t: performance.now(), key };
    let label = PHASE_NAMES[e.phase] || e.phase;
    if (e.phase === "lookups") label = `Look up ${e.queries.length} fact${e.queries.length > 1 ? "s" : ""} in ClickHouse`;
    addStep(key, kind, label, true);
  } else {
    S.live = null;
    if (kind === "llm") { S.llmMs += e.ms; S.llmCalls = e.llm_calls; S.cost = e.cost_usd; }
    else S.dbMs += e.ms;
    let extra = "";
    if (e.phase === "verify") extra = e.grounded === true ? "same number on re-run ✓" : e.grounded === false ? "did not match" : "";
    if (e.phase === "lookups" && e.server_ms) extra = `ClickHouse ${e.server_ms.map((x, k) => ((e.rejected || [])[k] ? "rejected (measured an outcome)" : x == null ? "error" : `${fmtMs(x)} ms`)).join(", ")}`;
    endStep(key, e.ms, extra);
    $("t-llm").textContent = S.llmCalls;
    $("t-llm-f").textContent = `$${S.cost.toFixed(3)} so far`;
  }
}

function onRoundStart(e) {
  const levers = e.levers;
  const rows = levers.length ? levers[0].values : [""];
  const rest = levers.slice(1);
  const cols = rest.reduce((acc, l) => acc.flatMap((c) => l.values.map((v) => [...c, v])), [[]]);
  const R = { ...e, rows, cols, cells: [], metrics: new Array(e.n).fill(undefined), info: new Array(e.n), done: 0, t: performance.now() };
  S.rounds[e.round] = R; S.sweepStart = performance.now(); S.sweepCount = 0;

  const card = document.createElement("section");
  card.className = "card round";
  const dims = levers.map((l) => `${esc(l.name)} (${l.values.length})`).join(" × ");
  card.innerHTML = `
    <div class="card-head"><h2>Round ${e.round}: ${e.n} scenarios</h2>
      <span class="meta" id="rmeta-${e.round}">${dims} · ${e.concurrency} in flight · ${esc(e.objective)} ${esc(e.metric_name)}</span></div>
    <pre title="The one SQL template the model wrote; ClickHouse runs it once per scenario">${esc(e.template)}</pre>
    <div class="gridwrap"><div class="grid" id="grid-${e.round}"></div></div>
    <div class="legend-seq"><span>${esc(e.metric_name)}</span><span id="lmin-${e.round}">low</span>
      <span class="ramp">${SEQ.map((c) => `<i style="background:${c}"></i>`).join("")}</span><span id="lmax-${e.round}">high</span>
      <span>· hatched = no metric (below minimum support) · hover a cell for its query</span></div>`;
  $("rounds").prepend(card);

  const grid = card.querySelector(".grid");
  const avail = Math.max(320, card.clientWidth - 120);
  const cw = Math.max(8, Math.min(28, Math.floor(avail / Math.max(cols.length, 1)) - 2));
  grid.style.gridTemplateColumns = `auto repeat(${cols.length}, ${cw}px)`;
  const every = Math.ceil(cols.length / Math.max(1, Math.floor(avail / 40)));
  let html = "<span></span>";
  cols.forEach((c, j) => { html += `<span class="cl" title="${esc(c.join(" · "))}">${j % every === 0 ? esc(c.map(fmtVal).join("·")) : ""}</span>`; });
  rows.forEach((rv, i) => {
    html += `<span class="rl">${esc(fmtVal(rv))}</span>`;
    cols.forEach((_, j) => { const idx = i * cols.length + j; html += idx < e.n ? `<span class="cell" data-i="${idx}" style="height:${Math.max(cw, 12)}px"></span>` : "<span></span>"; });
  });
  grid.innerHTML = html;
  R.cells = Array.from(grid.querySelectorAll(".cell"));
  grid.addEventListener("mousemove", (ev) => {
    const c = ev.target.closest(".cell"); if (!c) return hideTip();
    showTip(cellTip(R, +c.dataset.i), ev);
  });
  grid.addEventListener("mouseleave", hideTip);
  addStep(`round-${e.round}`, "db", `Round ${e.round}: ClickHouse runs ${e.n} scenarios`, true);
  S.live = { phase: "sweep", kind: "db", t: performance.now(), key: `round-${e.round}` };
}

function scenarioOf(R, i) {
  const out = {}; const r = Math.floor(i / R.cols.length), c = i % R.cols.length;
  if (R.levers.length) out[R.levers[0].name] = R.rows[r];
  R.levers.slice(1).forEach((l, k) => (out[l.name] = R.cols[c][k]));
  return out;
}

function cellTip(R, i) {
  const sc = scenarioOf(R, i), q = R.info[i];
  let h = Object.entries(sc).map(([k, v]) => `<b>${esc(k)}</b> ${esc(fmtVal(v))}`).join(" · ");
  if (!q) return `${h}<br>queued…`;
  if (q.error) return `${h}<br>error: ${esc(q.error)}`;
  h += `<br>${esc(R.metric_name)}: <b>${q.metric == null ? "no value" : esc(fmtVal(q.metric))}</b>`;
  if (q.support != null) h += ` over ${q.support.toLocaleString()} flights`;
  h += `<br>ClickHouse <b>${fmtMs(q.server_ms)} ms</b> server · ${fmtMs(q.roundtrip_ms)} ms round trip`;
  if (q.read_rows != null) h += `<br>${q.read_rows.toLocaleString()} rows read`;
  return h;
}

function onQuery(e) {
  const R = S.rounds[e.round]; if (!R) return;
  R.info[e.i] = e; R.done++; S.sweepCount++; S.scen++;
  if (!e.error) { R.metrics[e.i] = e.metric; S.server.push(e.server_ms); }
  const cell = R.cells[e.i];
  if (cell) cell.classList.add("done", ...(e.error ? ["err"] : e.metric == null ? ["null"] : []));
  if (!R.raf) R.raf = requestAnimationFrame(() => { R.raf = null; paint(R); renderHist(); renderTiles(); });
}

function paint(R) {
  const vals = R.metrics.filter((m) => m != null && Number.isFinite(m));
  if (!vals.length) return;
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  R.metrics.forEach((m, i) => {
    if (m == null || !Number.isFinite(m)) return;
    const k = Math.min(8, Math.floor(((m - lo) / span) * 9));
    if (R.cells[i]) R.cells[i].style.background = SEQ[k];
  });
  $(`lmin-${R.round}`).textContent = fmtVal(lo); $(`lmax-${R.round}`).textContent = fmtVal(hi);
}

function onRoundEnd(e) {
  const R = S.rounds[e.round]; if (R) { paint(R); R.wall = e.sweep_wall_ms; }
  S.live = null; S.dbMs += e.sweep_wall_ms;
  endStep(`round-${e.round}`, e.sweep_wall_ms,
    `${e.n} scenarios in ${fmtS(e.sweep_wall_ms)} · server p50 ${fmtMs(e.server_p50_ms)} ms, p95 ${fmtMs(e.server_p95_ms)} ms` +
    (e.errors ? ` · ${e.errors} errors` : ""));
  $(`rmeta-${e.round}`).textContent += ` · done in ${fmtS(e.sweep_wall_ms)}`;
  $("t-qps").textContent = Math.round(e.n / (e.sweep_wall_ms / 1000)).toLocaleString();
  $("t-qps-f").textContent = `round ${e.round}: ${e.n} in ${fmtS(e.sweep_wall_ms)}`;
  renderTiles();
}

function onDecision(e) {
  const key = `analyse-${S.phases.analyse || 1}`;
  const li = S.steps[key];
  const txt = e.decision === "refine" ? "Refine: another round" : "Answer";
  if (li) {
    li.querySelector(".what").textContent = `Model: ${txt.toLowerCase()}`;
    const why = document.createElement("span"); why.className = "why"; why.textContent = e.reasoning.slice(0, 260) + (e.reasoning.length > 260 ? "…" : "");
    li.appendChild(why);
  }
}

function onDone(r) {
  S.done = r;
  renderTiles(true);
  // outline the winning scenario
  const target = Object.fromEntries(Object.entries(r.best_scenario || {}).map(([k, v]) => [k, norm(v)]));
  Object.values(S.rounds).forEach((R) => R.cells.forEach((c, i) => {
    const sc = scenarioOf(R, i);
    const hit = Object.keys(target).length && Object.entries(target).every(([k, v]) => k in sc && norm(sc[k]) === v);
    c.classList.toggle("best", !!hit);
  }));
  const chips = Object.entries(r.answer_values || {}).map(([k, v]) => `<span class="chip">${esc(k)} <b>${esc(fmtVal(v))}</b></span>`).join("");
  const grounded = r.recommendation_grounded === true ? '<span class="badge ok">✓ Re-ran the winning scenario: same number</span>'
    : r.recommendation_grounded === false ? '<span class="badge warn">! Not confirmed by a re-run</span>' : "";
  const err = r.error ? `<p class="badge warn">! ${esc(r.error)}</p>` : "";
  $("answer").innerHTML = `${err}<p class="summary">${esc(r.summary || "(no summary)")}</p><div class="chips">${chips}</div>${grounded}
    ${r.trace_url ? `<br><a class="tracelink" href="${esc(r.trace_url)}" target="_blank" rel="noopener">Open the trace in Langfuse ↗</a>` : ""}`;
  addStep("done", "other", `Done · ${r.stop_reason}`); endStep("done", r.wall_ms, `${r.scenarios_evaluated} scenarios, ${r.llm_calls} model calls, $${r.cost_usd.toFixed(3)}`);
}

// ------------------------------------------------------------------ render
function addStep(key, kind, label, live = false) {
  const ol = $("steps"); ol.querySelector(".empty")?.remove();
  const li = document.createElement("li");
  li.className = `${kind}${live ? " live" : ""}`;
  li.innerHTML = `<span class="t"></span><span class="what">${esc(label)}</span>`;
  ol.appendChild(li); S.steps[key] = li;
}
function endStep(key, ms, extra) {
  const li = S.steps[key]; if (!li) return;
  li.classList.remove("live"); li.querySelector(".t").textContent = fmtS(ms);
  if (extra) { const s = document.createElement("span"); s.className = "why"; s.textContent = extra; li.appendChild(s); }
}

function elapsed() { return S.done ? S.done.wall_ms : performance.now() - S.t0; }
function liveParts() {
  let db = S.dbMs, llm = S.llmMs;
  if (S.live && !S.done) {
    const d = performance.now() - S.live.t;
    if (S.live.kind === "llm") llm += d; else db += d;
    const li = S.steps[S.live.key]; if (li) li.querySelector(".t").textContent = fmtS(d);
  }
  if (S.done) db = S.done.db_wait_ms_total;
  return { db, llm, total: Math.max(elapsed(), db + llm) };
}

function renderSplit() {
  if (!S) return;
  const { db, llm, total } = liveParts();
  const other = Math.max(0, total - db - llm);
  const w = (x) => `${total ? (x / total) * 100 : 0}%`;
  $("split-db").style.width = w(db); $("split-llm").style.width = w(llm); $("split-other").style.width = w(other);
  $("lg-db").textContent = `${fmtS(db)} · ${total ? pct(db / total) : "–"}`;
  $("lg-llm").textContent = `${fmtS(llm)} · ${total ? pct(llm / total) : "–"}`;
  $("lg-other").textContent = `${fmtS(other)}`;
  $("split-total").textContent = `run time ${fmtS(total)}`;
  $("t-share").textContent = total > 500 ? pct(db / total) : "–";
}

function renderTiles(final = false) {
  if (!S) return;
  $("t-scen").textContent = S.scen.toLocaleString();
  const nr = Object.keys(S.rounds).length;
  $("t-scen-f").textContent = nr ? `over ${nr} round${nr > 1 ? "s" : ""}` : "";
  const p50 = quantile(S.server, 0.5), p95 = quantile(S.server, 0.95);
  if (p50 != null) $("t-p50").textContent = `${fmtMs(p50)} · ${fmtMs(p95)} ms`;
  if (S.live && S.live.phase === "sweep" && S.sweepStart) {
    const s = (performance.now() - S.sweepStart) / 1000;
    if (s > 0.15) $("t-qps").textContent = Math.round(S.sweepCount / s).toLocaleString();
  }
  if (final && S.done) {
    $("t-llm").textContent = S.done.llm_calls; $("t-llm-f").textContent = `$${S.done.cost_usd.toFixed(3)} for the run`;
  }
  renderSplit();
}

function renderHist() {
  const counts = BINS.map(([a, b]) => (S ? S.server.filter((x) => x >= a && x < b).length : 0));
  const max = Math.max(1, ...counts);
  $("hist").innerHTML = counts.map((c, i) =>
    `<div class="bar" data-i="${i}" style="height:${(c / max) * 100}%">${c ? `<span>${c}</span>` : ""}</div>`).join("");
  if (!$("hist").nextElementSibling || !$("hist").nextElementSibling.classList.contains("hist-axis")) {
    $("hist").insertAdjacentHTML("afterend", `<div class="hist-axis">${BIN_LABELS.map((l) => `<span>${l}</span>`).join("")}</div>
      <div class="muted small" style="text-align:center;margin-top:2px">milliseconds, ClickHouse server time</div>`);
  }
  $("hist").onmousemove = (ev) => {
    const b = ev.target.closest(".bar"); if (!b) return hideTip();
    const i = +b.dataset.i, total = counts.reduce((a, x) => a + x, 0);
    showTip(`<b>${BIN_LABELS[i]} ms</b><br>${counts[i]} queries${total ? ` (${pct(counts[i] / total)})` : ""}`, ev);
  };
  $("hist").onmouseleave = hideTip;
}

function tick() { if (S) renderTiles(); }

init();
