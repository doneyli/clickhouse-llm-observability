// Claims workbench — vanilla JS. The examiner processes a claim (the agents recommend), reviews it
// (approve / override → the graph resumes and drafts the letter) and can ask the examiner assistant.
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = (v) => (v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const REASONS = ["missed-rule", "wrong-rule", "calc-error", "doc-misread", "policy-data-error", "other"];
let CLAIMS = [], CURRENT = null, PRESENTER = null;

try { if (localStorage.getItem("presenter") === "1") { document.body.classList.add("presenter-on"); $("#presenter").checked = true; } } catch (e) {}
$("#presenter").addEventListener("change", (e) => {
  document.body.classList.toggle("presenter-on", e.target.checked);
  try { localStorage.setItem("presenter", e.target.checked ? "1" : "0"); } catch (err) {}
});
$("#modal-close").onclick = () => $("#modal").classList.add("hidden");
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); };

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

async function loadQueue() {
  const d = await api("/api/claims");
  CLAIMS = d.claims;
  $("#lf-link").href = `${d.langfuse}/project/${d.project_id}`;
  renderQueue();
}

function renderQueue() {
  const f = $("#filter").value, q = $("#search").value.toLowerCase();
  const rows = CLAIMS.filter((c) =>
    (f === "all" || (f === "hero" ? c.tags.includes("hero") : c.set === f)) &&
    (!q || `${c.claim_id} ${c.scenario} ${c.state} ${c.line}`.toLowerCase().includes(q)));
  $("#claims").innerHTML = rows.map((c) => `
    <li data-id="${c.claim_id}" class="${CURRENT === c.claim_id ? "active" : ""}">
      <div class="cid">${c.claim_id} ${c.final ? `<span class="badge ${c.final}" style="font-size:10px;padding:1px 7px">${c.final}</span>` :
        c.recommendation ? `<span class="chip">${c.recommendation} · awaiting</span>` : ""}</div>
      <div class="scn">${esc(c.scenario)}</div>
      <div class="meta"><span class="chip">${c.line.replace("_", " ")}</span><span class="chip">${c.state}</span>
        ${c.tags.includes("hero") ? '<span class="chip hero">featured</span>' : ""}${c.set === "adversarial" ? '<span class="chip adv">adversarial</span>' : ""}</div>
    </li>`).join("");
  document.querySelectorAll("#claims li").forEach((li) => (li.onclick = () => openClaim(li.dataset.id)));
}
$("#filter").onchange = renderQueue;
$("#search").oninput = renderQueue;

async function openClaim(id) {
  CURRENT = id;
  renderQueue();
  const [d, p] = await Promise.all([api(`/api/claims/${id}`), api(`/api/presenter/${id}`)]);
  PRESENTER = p;
  renderClaim(d);
}

function kv(label, value) { return `<div class="kv"><small>${label}</small><b>${esc(value ?? "—")}</b></div>`; }

function renderClaim(d) {
  const h = d.header, pol = d.policy, s = d.summary;
  const amount = pol.face_amount ?? (pol.basic_amount != null ? pol.basic_amount + (pol.supplemental_amount || 0) : pol.contract_value);
  $("#detail").innerHTML = `
    <div class="card"><div class="title-row"><div><h1>${s.claim_id} · ${esc(s.insured)}</h1><p>${esc(s.scenario)}</p></div>
      <div class="links"><a href="${d.session_url}" target="_blank" rel="noopener">Claim session in Langfuse ↗</a></div></div>
      <div class="grid" style="margin-top:12px">
        ${kv("Product", h.line.replace("_", " "))}${kv("Governing state", h.governing_state)}${kv("Policy", h.policy_number)}
        ${kv("Issue date", pol.issue_date)}${kv("Status at death", pol.status_at_death)}${kv("Amount", money(amount))}
        ${kv("Notice", h.notice_date)}${kv("Proof of loss complete", h.proof_date)}${kv("Claimant", `${h.claimant.name} (${h.claimant.relationship})`)}
      </div></div>
    <div class="card"><h3>Scanned documents</h3><div class="docs">
      ${d.documents.map((x) => `<figure data-src="${x.url}"><img src="${x.url}" alt="${x.doc_type}"><figcaption>${x.doc_type.replace("_", " ")}</figcaption></figure>`).join("") || '<span class="muted">No scanned documents in the file.</span>'}
      </div><p class="small muted" style="margin-bottom:0">Forms received: ${Object.entries(h.forms_received).map(([k, v]) => `${k.replace("_", " ")} ${v ? "✓" : "✗"}`).join(" · ")}</p></div>
    <div class="card presenter-only presenter-box"><h3>Presenter · expected outcome (SME-codified oracle)</h3>
      <div><span class="badge ${PRESENTER.expected.decision}">${PRESENTER.expected.decision}</span>
      <span class="small" style="margin-left:8px">rules: ${PRESENTER.expected.rule_ids.join(", ") || "—"}</span></div>
      <p class="small">Critical: ${PRESENTER.critical.map((c) => `<b>${c.rule_id}</b> ${esc(c.title)}`).join("; ") || "none"} ·
        gross ${money(PRESENTER.expected.gross_benefit)} · interest ${money(PRESENTER.expected.interest_due)} (${PRESENTER.expected.interest_rule || "—"})</p></div>
    <div class="card"><h3>Agent recommendation</h3>
      <div class="controls">
        <label>claims-sop-rules prompt <select id="sop-label"><option>production</option><option>staging</option><option>conservative</option></select></label>
        <label>release <select id="release"><option value="">current</option><option value="claims-1.4.0">claims-1.4.0</option></select></label>
        <button id="process">${d.recommendation ? "Re-process claim" : "Process claim"}</button>
        <span id="proc-status" class="small muted"></span>
      </div>
      <div id="rec">${d.recommendation ? recHtml(d.recommendation) : ""}</div></div>
    <div class="card" id="review-card" style="${d.recommendation ? "" : "display:none"}">${reviewHtml(d)}</div>
    <div class="card"><h3>Ask the examiner assistant</h3>
      <div class="qa"><input id="question" placeholder="e.g. Why is this claim pending, and what do I need to request?"><button id="ask" class="secondary">Ask</button></div>
      <div id="answers">${(d.answers || []).map(ansHtml).join("")}</div></div>`;
  document.querySelectorAll(".docs figure").forEach((fg) => (fg.onclick = () => modal(`<img src="${fg.dataset.src}">`)));
  $("#process").onclick = () => processClaim(s.claim_id);
  $("#ask").onclick = () => ask(s.claim_id);
  bindReview(s.claim_id);
  bindSopLinks();
}

function recHtml(r) {
  const exp = PRESENTER ? new Set(PRESENTER.expected.rule_ids) : null;
  const got = new Set(r.rules.map((x) => x.rule_id));
  const missed = exp ? [...exp].filter((x) => !got.has(x)) : [];
  const rows = r.rules.map((x) => {
    const cls = x.added_by ? "added" : exp && !exp.has(x.rule_id) ? "extra" : "";
    // Rule fields come from LLM output (and the adversarial set injects text into documents): escape all of them.
    const type = esc(String(x.type || "").replace(/[^A-Z_]/g, ""));
    return `<tr class="${cls}"><td class="rid"><span class="sop-link" data-sop="${esc(x.sop_id)}">${esc(x.rule_id)}</span>${x.version ? ` <span class="muted">v${esc(x.version)}</span>` : ""}</td>
      <td><span class="type ${type}">${type}</span></td><td>${esc(x.action)}</td><td class="small">${esc(x.rationale)}${x.added_by ? ` <b>(${esc(x.added_by)})</b>` : ""}</td></tr>`;
  }).join("") + missed.map((m) => `<tr class="missed presenter-only" style="display:table-row"><td class="rid">${m}</td><td colspan="3" class="small"><b>missed</b> — the oracle says this rule applies</td></tr>`).join("");
  const c = r.calculation || {};
  const guard = r.guard && r.guard.triggered ? `<div class="guard">⚠️ <b>Critical-rule guard:</b> the agent missed ${esc(r.guard.missing.join(", "))}. The codified rule was added before adjudication${r.recommendation !== "PAY" ? " — the claim is held, not paid" : ""}.</div>` : "";
  return `<div class="rec-head" style="margin-top:12px"><span class="badge ${r.recommendation}">${r.recommendation}</span>
      ${c.gross_benefit != null ? `<span>Gross <b>${money(c.gross_benefit)}</b> · interest <b>${money(c.interest_due)}</b> <span class="muted small">${c.interest_rule || ""}</span></span>` : ""}
      <span class="links"><a href="${r.trace_url}" target="_blank" rel="noopener">Trace in Langfuse ↗</a><a href="${r.apm_url}" target="_blank" rel="noopener">Same trace in APM ↗</a></span>
      <span class="small muted">≈ $${(r.cost_usd || 0).toFixed(3)} · prompts ${Object.entries(r.prompt_versions || {}).map(([k, v]) => `${k.replace("claims-", "")} v${v}`).join(", ")}</span></div>
    ${guard}
    <p class="small muted">Retrieved ${r.retrieved.length} SOPs · citation validity ${r.citation_validity?.score ?? "—"} · retrieval coverage ${r.retrieval_coverage?.score ?? "—"}${r.retrieval_coverage?.missing?.length ? ` (missing ${r.retrieval_coverage.missing.join(", ")})` : ""}</p>
    <table><thead><tr><th>Rule</th><th>Type</th><th>Action</th><th>Why it applies</th></tr></thead><tbody>${rows}</tbody></table>
    <h3 style="margin-top:14px">Examiner note</h3><div class="note">${esc(r.note)}</div>
    ${r.next_actions.length ? `<p class="small"><b>Next actions:</b> ${r.next_actions.map(esc).join(" · ")}</p>` : ""}
    ${r.approvals.length ? `<p class="small"><b>Approvals:</b> ${r.approvals.map(esc).join(" · ")}</p>` : ""}`;
}

function reviewHtml(d) {
  if (d.review_result) {
    const fd = esc(d.review_result.final_decision);
    return `<h3>Examiner decision</h3><p><span class="badge ${fd}">${fd}</span>
      <span class="small muted" style="margin-left:8px">${d.review.action === "approve" ? "approved as recommended" : `overridden (${esc(d.review.reason)})`} · ${esc(d.review.note)}</span>
      <span class="links" style="margin-left:10px"><a href="${d.review_result.trace_url}" target="_blank" rel="noopener">Review trace ↗</a></span></p>
      <h3>Letter to the claimant</h3><div class="letter">${esc(d.review_result.letter)}</div>`;
  }
  return `<h3>Examiner review</h3>
    <div class="review-form">
      <div><button id="approve">Approve recommendation</button></div>
      <div><label class="small muted">Override decision</label><select id="ov-decision"><option>PAY</option><option>PEND</option><option>DENY</option><option>REFER_SIU</option></select></div>
      <div><label class="small muted">Reason</label><select id="ov-reason">${REASONS.map((r) => `<option>${r}</option>`).join("")}</select></div>
      <div><label class="small muted">Missed rule ids</label><input id="ov-missed" placeholder="e.g. LIFE-COMM-001"></div>
      <textarea id="ov-note" placeholder="Note (why you approve or change the recommendation)"></textarea>
      <div><button id="override" class="secondary">Override</button> <span id="rev-status" class="small muted"></span></div>
    </div>`;
}

function bindReview(id) {
  const send = async (body) => {
    document.querySelectorAll("#approve, #override").forEach((b) => (b.disabled = true));
    $("#rev-status").innerHTML = '<span class="spinner" style="border-color:#888;border-right-color:transparent"></span> resuming the graph, drafting the letter…';
    try { await api(`/api/claims/${id}/review`, { method: "POST", body: JSON.stringify(body) }); await loadQueue(); await openClaim(id); }
    catch (e) { $("#rev-status").textContent = e.message; document.querySelectorAll("#approve, #override").forEach((b) => (b.disabled = false)); }
  };
  if ($("#approve")) $("#approve").onclick = () => send({ action: "approve", note: $("#ov-note").value || "" });
  if ($("#override")) $("#override").onclick = () => send({
    action: "override", decision: $("#ov-decision").value, reason: $("#ov-reason").value, note: $("#ov-note").value,
    missed_rule_ids: $("#ov-missed").value.split(/[ ,]+/).filter(Boolean) });
}

async function processClaim(id) {
  const btn = $("#process");
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Agents working…';
  $("#proc-status").textContent = "intake (A2A) → policy (MCP) → screening → SOP rules → guard → adjudication";
  try {
    await api(`/api/claims/${id}/process`, { method: "POST", body: JSON.stringify({ sop_label: $("#sop-label").value, release: $("#release").value || null }) });
    await loadQueue(); await openClaim(id);
  } catch (e) { $("#proc-status").textContent = "Error: " + e.message; btn.disabled = false; btn.textContent = "Process claim"; }
}

function ansHtml(a) { return `<div class="answer"><b>Q:</b> ${esc(a.question)}<br><b>A:</b> ${esc(a.answer)} <a class="small" href="${a.trace_url}" target="_blank" rel="noopener">trace ↗</a></div>`; }

async function ask(id) {
  const q = $("#question").value.trim();
  if (!q) return;
  $("#ask").disabled = true;
  try { const r = await api(`/api/claims/${id}/ask`, { method: "POST", body: JSON.stringify({ question: q }) });
    $("#answers").insertAdjacentHTML("afterbegin", ansHtml({ question: q, ...r })); $("#question").value = ""; }
  catch (e) { $("#answers").insertAdjacentHTML("afterbegin", `<div class="answer">Error: ${esc(e.message)}</div>`); }
  $("#ask").disabled = false;
}

function bindSopLinks() {
  document.querySelectorAll(".sop-link").forEach((el) => (el.onclick = async () => {
    const d = await api(`/api/sops/${el.dataset.sop}`);
    modal(`<h2>${d.sop_id} — ${esc(d.title)}</h2><pre>${esc(d.text)}</pre>`);
  }));
}

function modal(html) { $("#modal-content").innerHTML = html; $("#modal").classList.remove("hidden"); }

loadQueue().then(() => openClaim("CLM-2026-00042"));
