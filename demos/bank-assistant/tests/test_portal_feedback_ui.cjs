/* Exercise the portal's real feedback functions with a minimal DOM and HTTP stub.
 * Run: node --test tests/test_portal_feedback_ui.cjs
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../portal/static/app.js"), "utf8");
const render = source.slice(source.indexOf("  function renderMessage("), source.indexOf("  function openFeedbackBox("));
const feedback = source.slice(source.indexOf("  const feedbackPending ="), source.indexOf("  function replaceMessage("));
const TRACE = "a".repeat(32);
const failed = { ok: false, error: "queue unavailable" };
const queued = { ok: true, name: "Review queue", url: "https://langfuse.test/project/p/annotation-queues/q" };

function harness(messages, respond) {
  let saved, rendered;
  const calls = [];
  const context = {
    S: { messages: JSON.parse(JSON.stringify(messages)) },
    postJSON: async (route, body) => { calls.push({ route, body }); return respond(route, body); },
    persist: () => { saved = JSON.parse(JSON.stringify(context.S.messages)); },
    replaceMessage: (idx) => { rendered = context.renderMessage(context.S.messages[idx], idx); },
    toast: () => {}, t: (s) => s, esc: String, icon: () => "", md: String,
    document: { createElement: () => ({}) },
    $: (selector, el) => {
      if (selector !== "[data-fb-retry]" || !el.innerHTML.includes("data-fb-retry")) return null;
      return el.retry = {
        disabled: /data-fb-retry\s+disabled/.test(el.innerHTML),
        addEventListener: (_, callback) => { el.retry.click = callback; },
      };
    },
    $$: () => [],
  };
  vm.createContext(context);
  vm.runInContext(render + "\n" + feedback, context);
  return { context, calls, saved: () => saved, rendered: () => rendered };
}

const answer = () => ({ role: "assistant", content: "answer", meta: { trace_id: TRACE } });

test("a failed enqueue survives reload and retries only queue delivery", async () => {
  const first = harness([answer()], () => ({ ok: true, queue: failed }));
  await first.context.sendFeedback(0, 0, "wrong fee");
  assert.equal(first.saved()[0].meta.feedback, 0);
  assert.equal(first.saved()[0].meta.queue_failed, true);
  assert(first.rendered().retry);
  assert.equal(first.rendered().retry.disabled, false);
  assert.equal((first.rendered().innerHTML.match(/data-fb="[01]"[^>]*disabled/g) || []).length, 2);

  let finish;
  const reloaded = harness(first.saved(), () => new Promise((resolve) => { finish = resolve; }));
  const retry = reloaded.context.renderMessage(reloaded.context.S.messages[0], 0).retry;
  const sending = retry.click();
  assert.equal(reloaded.rendered().retry.disabled, true);
  await reloaded.context.retryFeedbackQueue(0); // duplicate click while waiting
  assert.equal(reloaded.calls.length, 1);
  assert.equal(reloaded.calls[0].route, "/api/feedback/queue");
  assert.deepEqual(JSON.parse(JSON.stringify(reloaded.calls[0].body)), { trace_id: TRACE });
  finish(queued);
  await sending;
  assert.equal(reloaded.saved()[0].meta.feedback, 0);
  assert.equal(reloaded.saved()[0].meta.queue_failed, false);
  assert.equal(reloaded.saved()[0].meta.queue_url, queued.url);
  assert.equal(reloaded.rendered().retry, undefined);
  assert(reloaded.rendered().innerHTML.includes(queued.url));
});

test("a failed retry remains available for API and network failures", async () => {
  for (const respond of [() => failed, () => { throw new Error("network offline"); }]) {
    const message = answer();
    Object.assign(message.meta, { feedback: 0, queue_failed: true });
    const page = harness([message], respond);
    await page.context.retryFeedbackQueue(0);
    assert.equal(page.context.S.messages[0].meta.queue_failed, true);
    assert.equal(page.rendered().retry.disabled, false);
    assert.equal(page.calls[0].route, "/api/feedback/queue");
  }
});

test("successful negative and positive feedback have no queue retry", async () => {
  for (const value of [0, 1]) {
    const page = harness([answer()], () => ({ ok: true, queue: value === 0 ? queued : null }));
    await page.context.sendFeedback(0, value, null);
    assert.equal(page.saved()[0].meta.feedback, value);
    assert.equal(page.rendered().retry, undefined);
  }
});

test("repeated sends while waiting record feedback once", async () => {
  let finish;
  const page = harness([answer()], () => new Promise((resolve) => { finish = resolve; }));
  const first = page.context.sendFeedback(0, 0, "wrong fee");
  await page.context.sendFeedback(0, 0, "wrong fee");
  assert.equal(page.calls.length, 1);
  finish({ ok: true, queue: failed });
  await first;
  assert.equal(page.rendered().retry.disabled, false);
});
