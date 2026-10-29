const $ = (id) => document.getElementById(id);
let history = [], sessionId = crypto.randomUUID(), busy = false;
function reset() { history = []; sessionId = crypto.randomUUID(); $("messages").replaceChildren(); }
function show(text, role, result) {
  const node = document.createElement("div"); node.className = "turn " + role; node.textContent = text;
  if (result) {
    const details = document.createElement("small");
    details.textContent = `Tools: ${result.tools_used.join(", ") || "none"}. Sources: ${result.sources.join(", ") || "none"}. Prompt: ${result.prompt_version ?? "local fallback"}. `;
    if (result.trace_url) {
      const link = document.createElement("a"); link.href = result.trace_url; link.textContent = "Inspect this trace"; link.target = "_blank"; link.rel = "noopener"; details.append(link);
    }
    node.append(details);
  }
  $("messages").append(node); $("messages").scrollTop = $("messages").scrollHeight;
}
function pending(value) { busy = value; document.querySelectorAll("button,select").forEach(el => el.disabled = value); }
$("new").onclick = reset; $("customer").onchange = reset;
document.querySelectorAll("[data-question]").forEach(el => el.onclick = () => { $("question").value = el.dataset.question; $("question").focus(); });
$("chat-form").onsubmit = async (event) => {
  event.preventDefault(); if (busy) return;
  const question = $("question").value.trim(); if (!question) return;
  show(question, "user"); $("question").value = ""; pending(true);
  try {
    const response = await fetch("/chat", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({question, customer_id:$("customer").value, session_id:sessionId, history:history.slice(-20)})});
    const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The request could not be completed.");
    show(result.answer, "assistant", result); history.push({role:"user",content:question},{role:"assistant",content:result.answer});
  } catch (error) { show(error.message + " You can retry the question after checking setup.", "error"); }
  finally { pending(false); $("question").focus(); }
};
fetch("/health").then(r => r.json()).then(info => { $("status").textContent = `${info.workshop}: ${info.model}. Tracing ${info.tracing ? "enabled" : "disabled"}.`; $("cloud").href = info.langfuse_url; }).catch(() => $("status").textContent = "Workshop server is unavailable. Start it from your terminal.");
