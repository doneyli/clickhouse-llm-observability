/**
 * Replay the deterministic evaluators over an already-labelled cohort in
 * Langfuse and print where they disagree with the human labels.
 *
 * This is the cheap half of judge calibration, and it is worth running whenever
 * an evaluator is added: a check written from a handful of remembered examples
 * tends to be either too narrow to fire or too broad to trust, and the cohort
 * that produced it is the only honest place to find out which.
 *
 * Usage:
 *   npx tsx scripts/backtest-evaluators.ts --tag ea:pass-1 --score quoted_total_for_empty_cart
 *   npx tsx scripts/backtest-evaluators.ts --tag ea:pass-1        # every category score present
 */
import "../src/env.js";

import { LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY } from "../src/env.js";
import {
  quotedTotalForEmptyCart,
  readbackRequestUnanswered,
  type EvalContext,
  type Verdict,
} from "../src/evaluators/deterministic.js";

const AUTH = `Basic ${Buffer.from(`${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}`).toString("base64")}`;

/** Evaluators that can be replayed from trace data alone, by score name. */
const REPLAYABLE: Record<string, (ctx: EvalContext) => Verdict> = {
  quoted_total_for_empty_cart: quotedTotalForEmptyCart,
  readback_request_unanswered: readbackRequestUnanswered,
};

async function get(path: string, params: Record<string, string> = {}): Promise<any> {
  const url = new URL(`${LANGFUSE_BASE_URL}/api/public/${path}`);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const res = await fetch(url, { headers: { authorization: AUTH } });
  if (!res.ok) throw new Error(`${res.status} ${path}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

async function getAllObservations(environment: string, from: string): Promise<any[]> {
  const out: any[] = [];
  let cursor: string | undefined;
  do {
    const page = await get("v2/observations", {
      environment,
      fromStartTime: from,
      fields: "core,basic,io,trace_context,metadata",
      limit: "1000",
      ...(cursor ? { cursor } : {}),
    });
    out.push(...(page.data ?? []));
    // `v3/scores` returns the continuation as `meta.cursor`; other paginated
    // endpoints use `meta.nextCursor`. Accepting either is cheaper than being
    // wrong, and being wrong here is silent — you simply compare fewer labels
    // than you think and the agreement number looks fine.
    cursor = page.meta?.nextCursor ?? page.meta?.cursor ?? undefined;
  } while (cursor);
  return out;
}

const asText = (v: unknown): string => (v == null ? "" : typeof v === "string" ? v : JSON.stringify(v));

function parseJson(output: unknown): any {
  let parsed: any = output;
  if (typeof parsed === "string") {
    try { parsed = JSON.parse(parsed); } catch { return undefined; }
  }
  return parsed && typeof parsed === "object" ? parsed : undefined;
}

/** Pull `{cart:[{sku}], subtotalCents}` out of a `manage_cart` result. */
function readCartSnapshot(output: unknown): { skus: string[]; subtotalCents: number } | undefined {
  const p = parseJson(output);
  if (!p || !Array.isArray(p.cart) || typeof p.subtotalCents !== "number") return undefined;
  return { skus: p.cart.map((l: any) => String(l.sku)).filter(Boolean), subtotalCents: p.subtotalCents };
}

/**
 * `list_offers` recomputes the basket total from scratch, so its `cartSubtotal`
 * is the one figure in a turn that is definitely settled.
 *
 * This matters more than it looks. The assistant issues cart mutations in
 * PARALLEL, and each `manage_cart` result is a snapshot taken mid-flight — the
 * span that starts last, and the span that ends last, both routinely report a
 * basket missing another call's addition. Reconstructing "the cart after this
 * turn" from mutation spans produced three false failures here (a $14.46 quote
 * scored against a reconstructed $9.97) before this read was preferred.
 *
 * The general lesson for replaying evaluators off traces: a mutation span tells
 * you what one call did, not what the state ended up as. Prefer a read.
 */
function readSettledSubtotalCents(output: unknown): number | undefined {
  const p = parseJson(output);
  const raw = p?.cartSubtotal;
  if (typeof raw !== "string") return undefined;
  const m = raw.match(/([\d,]+\.\d{2})/);
  return m?.[1] === undefined ? undefined : Math.round(Number(m[1].replace(/,/g, "")) * 100);
}

function arg(flag: string): string | undefined {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

async function main(): Promise<void> {
  const tag = arg("--tag") ?? "ea:pass-1";
  const only = arg("--score");
  const names = only ? [only] : Object.keys(REPLAYABLE);

  const obs = await getAllObservations("error-analysis", "2026-01-01T00:00:00Z");
  const inCohort = obs.filter((o) => (o.tags ?? []).includes(tag));

  const roots = inCohort
    .filter((o) => o.isRootObservation && o.name === "handle-chat-message")
    .sort((a, b) => {
      const s = String(a.sessionId).localeCompare(String(b.sessionId));
      return s !== 0 ? s : Number(a.metadata?.turn ?? 0) - Number(b.metadata?.turn ?? 0);
    });

  // Human labels already in the project, keyed by observation id + score name.
  const human = new Map<string, number>();
  let cursor: string | undefined;
  do {
    const page = await get("v3/scores", { limit: "100", fields: "core,subject", ...(cursor ? { cursor } : {}) });
    for (const s of page.data ?? []) {
      if (names.includes(s.name) && s.subject?.id) human.set(`${s.subject.id}|${s.name}`, Number(s.value));
    }
    // `v3/scores` returns the continuation as `meta.cursor`; other paginated
    // endpoints use `meta.nextCursor`. Accepting either is cheaper than being
    // wrong, and being wrong here is silent — you simply compare fewer labels
    // than you think and the agreement number looks fine.
    cursor = page.meta?.nextCursor ?? page.meta?.cursor ?? undefined;
  } while (cursor);

  console.log(`cohort ${tag}: ${roots.length} turns, ${human.size} human labels for ${names.join(", ")}\n`);

  // Cart state carries forward within a session: a turn that calls no cart tool
  // inherits the previous turn's basket.
  const carts = new Map<string, { skus: string[]; subtotalCents: number }>();
  const disagreements: string[] = [];
  let compared = 0;
  let agreed = 0;

  for (const r of roots) {
    const session = String(r.sessionId);
    // Order by END time, not start. The assistant issues cart mutations in
    // parallel, so several `manage_cart` spans start at effectively the same
    // moment and the one that STARTED last is not the one holding the final
    // basket — ordering by startTime reconstructed a 3-item cart for a turn
    // that ended with 4, and produced two false failures on first run.
    const kids = inCohort
      .filter((o) => o.traceId === r.traceId && o.id !== r.id && o.type === "TOOL")
      .sort(
        (a, b) =>
          String(a.endTime ?? a.startTime).localeCompare(String(b.endTime ?? b.startTime)) ||
          String(a.startTime).localeCompare(String(b.startTime)),
      );
    // Widest snapshot for the SKU list, a settled read for the money. Neither
    // alone is reliable while mutations run concurrently.
    let widest: { skus: string[]; subtotalCents: number } | undefined;
    let settled: number | undefined;
    for (const t of kids) {
      const snap = readCartSnapshot(t.output);
      if (snap && (widest === undefined || snap.skus.length > widest.skus.length)) widest = snap;
      const sub = readSettledSubtotalCents(t.output);
      if (sub !== undefined) settled = sub;
    }
    if (widest || settled !== undefined) {
      const prev = carts.get(session);
      carts.set(session, {
        skus: widest?.skus ?? prev?.skus ?? [],
        subtotalCents: settled ?? widest?.subtotalCents ?? prev?.subtotalCents ?? 0,
      });
    }
    const cart = carts.get(session) ?? { skus: [], subtotalCents: 0 };

    const ctx: EvalContext = {
      message: asText(r.input).replace(/^\{"message":"?/, "").replace(/"?\}$/, ""),
      answer: asText(r.output),
      cartSkus: cart.skus,
      toolsCalled: kids.map((t) => String(t.name)),
      history: [],
      cartSubtotalCents: cart.subtotalCents,
    };

    for (const name of names) {
      const fn = REPLAYABLE[name];
      if (!fn) continue;
      const v = fn(ctx);
      const machine = v.applicable && !v.passed ? 1 : 0;
      const key = `${r.id}|${name}`;
      if (!human.has(key)) continue;
      const label = human.get(key) ?? 0;
      compared += 1;
      if (label === machine) agreed += 1;
      else {
        disagreements.push(
          `${session.replace("ea-2026-09-09-", "")} turn ${r.metadata?.turn}  ${name}\n` +
            `    human=${label ? "true" : "false"}  evaluator=${machine ? "true" : "false"}` +
            `  applicable=${v.applicable}\n` +
            `    ${v.comment}`,
        );
      }
    }
  }

  console.log(`compared ${compared}   agreed ${agreed}   disagreed ${compared - agreed}`);
  if (compared > 0) console.log(`agreement ${((agreed / compared) * 100).toFixed(1)}%\n`);
  for (const d of disagreements) console.log(d + "\n");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
