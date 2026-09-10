/**
 * Write one SESSION-level score per demo session: `conversation-cart-integrity`.
 *
 * This is the score type no Langfuse-managed evaluator can produce. A managed
 * LLM-as-a-judge rule is targeted at a trace or an observation, so it sees one
 * turn — and "did the cart ever get misreported across this whole shopping
 * trip?" is not a property of any single turn. It is a property of the session,
 * and the only thing that can compute it is code that reads the session.
 *
 * The reconstruction is deliberate too. Rather than scoring the records still
 * sitting in memory from the run that just happened, this script reads the
 * session back OUT of Langfuse and rebuilds each turn from the trace tree:
 *
 *   - the shopper's message   ← root observation `input.message`
 *   - the assistant's answer  ← root observation `output`
 *   - the cart after the turn ← the last `manage_cart` TOOL observation's
 *                               `output.cart`, carried forward across turns that
 *                               did not touch the cart
 *   - the tools called        ← the TOOL observations' names
 *
 * That is what a real scheduled scoring job has to do, and it doubles as a
 * proof: this only works on the well-instrumented sessions. A broken-mode
 * session has no output on its root observation and its cart tool calls are
 * still there, so the message and the cart survive but the ANSWER does not —
 * and with no answer there is no claim to check. The instrumentation defect
 * is what makes the session unscoreable.
 */
import "../src/instrumentation.js";

import { pathToFileURL } from "node:url";

import { flushTraces } from "../src/instrumentation.js";
import {
  LANGFUSE_BASE_URL,
  LANGFUSE_PUBLIC_KEY,
  LANGFUSE_SECRET_KEY,
  verifyProject,
} from "../src/env.js";
import { TRACE_NAME } from "../src/assistant.js";
import { unverifiedCartClaim, type EvalContext } from "../src/evaluators/deterministic.js";
import { langfuseClient } from "./seed-dataset.js";

const BOLD = "[1m";
const DIM = "[2m";
const GREEN = "[32m";
const RED = "[31m";
const OFF = "[0m";

export const SCORE_NAME = "conversation-cart-integrity";

const AUTH = `Basic ${Buffer.from(`${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}`).toString("base64")}`;

async function api<T>(path: string): Promise<T> {
  const res = await fetch(`${LANGFUSE_BASE_URL}${path}`, { headers: { Authorization: AUTH } });
  if (!res.ok) {
    throw new Error(`GET ${path} → HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

// ------------------------------------------------------------------ reading ---
type TraceRow = { id: string; name?: string | null; sessionId?: string | null; timestamp?: string };

type ObsRow = {
  id: string;
  traceId: string;
  type: string;
  name?: string | null;
  parentObservationId?: string | null;
  startTime: string;
  input?: unknown;
  output?: unknown;
  metadata?: unknown;
};

// -------------------------------------------------------------- API version ---
/**
 * Which read API this server speaks, decided the same way `compare-traces.ts`
 * decides it — from `GET /api/public/health`.
 *
 * `GET /api/public/traces` and `GET /api/public/traces/{id}` are deprecated and
 * are removed from Langfuse Cloud on 2026-11-16, so the v2 observations API is
 * the path that has a future. It is also *unavailable* on a Langfuse v3 server,
 * which answers HTTP 404 with `LangfuseNotFoundError` — and this demo's default
 * self-hosted stack is 3.221.1 while its `.env` points at Cloud. Neither path
 * alone covers both, so both are implemented and the server chooses.
 */
type ApiFlavour = "v1" | "v2";
let flavour: ApiFlavour | undefined;

async function detectApiFlavour(): Promise<ApiFlavour> {
  if (flavour) return flavour;
  const health = await api<{ version?: string }>("/api/public/health");
  const version = health?.version ?? "unknown";
  // Major 3 has no v2 observations API. Anything else is treated as v4-or-later.
  flavour = version.startsWith("3.") ? "v1" : "v2";
  return flavour;
}

/**
 * v2 hands input/output back as SERIALIZED JSON STRINGS where v1 returns parsed
 * objects. Every consumer below reads them as objects (`output.cart`,
 * `input.action`), so normalise at the boundary rather than teaching each
 * reader both shapes — that is the difference between one change here and a
 * dozen `typeof` checks scattered through the reconstruction.
 */
function parseIo(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  // Only object/array payloads are serialized JSON here. Attempting a parse on
  // anything else would silently transform the assistant's prose answer: an
  // answer of "42" is a string the evaluators read, not the number 42.
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function normalise(o: ObsRow): ObsRow {
  return { ...o, input: parseIo(o.input), output: parseIo(o.output) };
}

/**
 * The most recent sessions, newest first.
 *
 * On v2 the session is a first-class column on every observation, so the roots
 * alone identify both the session and its turns — no trace list needed. On v3
 * it comes from the traces list, which is where `sessionId` lives there.
 *
 * This project holds only this demo's traffic, so every session here is a demo
 * session.
 */
async function readRecentSessions(maxSessions: number): Promise<Array<{ sessionId: string; traces: TraceRow[] }>> {
  const rows: TraceRow[] = [];

  if ((await detectApiFlavour()) === "v2") {
    type V2Root = { traceId: string; sessionId?: string | null; startTime: string; name?: string | null };
    let cursor: string | undefined;
    for (let page = 0; page < 10; page += 1) {
      const qs = new URLSearchParams({ fields: "core,basic", limit: "100", isRootObservation: "true" });
      if (cursor) qs.set("cursor", cursor);
      const body = await api<{ data: V2Root[]; meta?: { cursor?: string; nextCursor?: string } }>(
        `/api/public/v2/observations?${qs.toString()}`,
      );
      for (const o of body.data) {
        rows.push({ id: o.traceId, sessionId: o.sessionId ?? null, timestamp: o.startTime, name: o.name ?? null });
      }
      cursor = body.meta?.nextCursor ?? body.meta?.cursor ?? undefined;
      if (!cursor || body.data.length === 0) break;
    }
    // Cursor order is not guaranteed to be newest-first; make it so explicitly.
    rows.sort((a, b) => String(b.timestamp ?? "").localeCompare(String(a.timestamp ?? "")));
  } else {
    for (let page = 1; page <= 10; page += 1) {
      const body = await api<{ data: TraceRow[] }>(`/api/public/traces?page=${page}&limit=100`);
      rows.push(...body.data);
      if (body.data.length < 100) break;
    }
  }

  const grouped = new Map<string, TraceRow[]>();
  for (const t of rows) {
    if (!t.sessionId) continue;
    grouped.set(t.sessionId, [...(grouped.get(t.sessionId) ?? []), t]);
  }

  // Newest first, so first-seen is newest.
  return [...grouped.entries()].slice(0, maxSessions).map(([sessionId, traces]) => ({ sessionId, traces }));
}

/**
 * One turn's observations.
 *
 * `fields` MUST include `io` on v2 or input and output come back undefined —
 * which reconstructs every turn with an empty answer and reads exactly like the
 * broken-instrumentation session this script exists to distinguish.
 */
async function readObservations(traceId: string): Promise<ObsRow[]> {
  if ((await detectApiFlavour()) === "v2") {
    const qs = new URLSearchParams({
      traceId,
      fields: "core,basic,io,metadata",
      limit: "1000",
    });
    const body = await api<{ data: ObsRow[] }>(`/api/public/v2/observations?${qs.toString()}`);
    return (body.data ?? []).map(normalise);
  }
  const trace = await api<{ observations?: ObsRow[] }>(
    `/api/public/traces/${encodeURIComponent(traceId)}`,
  );
  return (trace.observations ?? []).map(normalise);
}

// ----------------------------------------------------------- reconstruction ---
type CartOp = { action: string; sku: string | undefined; failed: boolean };

type ReconstructedTurn = {
  turn: number;
  message: string;
  answer: string;
  toolsCalled: string[];
  /**
   * The cart exactly as one `manage_cart` call reported it, when the turn made
   * exactly one such call. That snapshot is authoritative; see `cartOps` for why
   * it is not usable when there were several.
   */
  cartSnapshot: string[] | undefined;
  /** The cart mutations this turn attempted, for replaying onto the carried cart. */
  cartOps: CartOp[];
};

function asString(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  return undefined;
}

function messageFrom(input: unknown): string | undefined {
  if (typeof input !== "object" || input === null) return undefined;
  return asString((input as { message?: unknown }).message);
}

function cartFrom(output: unknown): string[] | undefined {
  if (typeof output !== "object" || output === null) return undefined;
  const cart = (output as { cart?: unknown }).cart;
  if (!Array.isArray(cart)) return undefined;
  return cart
    .map((line) => (typeof line === "object" && line !== null ? asString((line as { sku?: unknown }).sku) : undefined))
    .filter((sku): sku is string => sku !== undefined);
}

function turnNumberFrom(metadata: unknown, fallback: number): number {
  if (typeof metadata === "object" && metadata !== null) {
    const turn = (metadata as { turn?: unknown }).turn;
    if (typeof turn === "number") return turn;
    if (typeof turn === "string" && /^\d+$/.test(turn)) return Number.parseInt(turn, 10);
  }
  return fallback;
}

function cartOpFrom(o: ObsRow): CartOp {
  const input = (o.input ?? {}) as { action?: unknown; sku?: unknown };
  const output = (o.output ?? {}) as { error?: unknown };
  return {
    action: typeof input.action === "string" ? input.action : "view",
    sku: typeof input.sku === "string" ? input.sku.trim().toUpperCase() : undefined,
    // An out-of-stock add returns `{ error, suggestedSubstitute }` and changes
    // nothing. Treating it as a successful add would make the evaluator agree
    // with the very claim it exists to catch.
    failed: typeof output.error === "string",
  };
}

/**
 * Every turn inside one trace.
 *
 * A turn is a `handle-chat-message` observation carrying both a message and an
 * answer — NOT "the root observation of a trace". Those are the same thing for a
 * conversation played by run-conversation or compare-traces, where each turn is
 * its own trace, and they are emphatically not the same thing for a dataset run:
 * the experiment runner owns the item's trace root and all seven turns hang
 * underneath it, so one trace holds the whole conversation. Keying on the trace
 * root reported those sessions as "0 of 1 turns reconstructable" — the score
 * silently vanished for exactly the runs a presenter is most likely to open.
 *
 * So turns are found by name, and each turn's tools are its DESCENDANTS rather
 * than every TOOL in the trace, which would smear seven turns' cart operations
 * into each one.
 */
function reconstructTurns(observations: ObsRow[]): ReconstructedTurn[] {
  const childrenOf = new Map<string, ObsRow[]>();
  for (const o of observations) {
    const parent = o.parentObservationId ?? "";
    childrenOf.set(parent, [...(childrenOf.get(parent) ?? []), o]);
  }

  const turnRoots = observations.filter(
    (o) =>
      o.name === TRACE_NAME &&
      messageFrom(o.input) !== undefined &&
      asString(o.output) !== undefined,
  );

  return turnRoots.flatMap((turnRoot, index) => {
    const message = messageFrom(turnRoot.input);
    const answer = asString(turnRoot.output);
    if (message === undefined || answer === undefined) return [];

    // Descendants of this turn only, stopping at any nested turn so a
    // conversation-in-one-trace cannot bleed across turn boundaries.
    const descendants: ObsRow[] = [];
    const stack = [...(childrenOf.get(turnRoot.id) ?? [])];
    while (stack.length > 0) {
      const next = stack.pop();
      if (!next || next.name === TRACE_NAME) continue;
      descendants.push(next);
      stack.push(...(childrenOf.get(next.id) ?? []));
    }

    const tools = descendants.filter((o) => o.type === "TOOL");
    const cartCalls = tools.filter((o) => o.name === "manage_cart");

    // Why not simply take the last cart snapshot by startTime: tool calls the
    // model issues in one step run in parallel and are stamped with start times
    // equal to the millisecond, so the ordering is not recoverable and "last"
    // silently picks a snapshot taken BEFORE a sibling add. That produced a
    // confident, wrong FAIL — the assistant really had added the milk. With more
    // than one call the mutations are replayed onto the carried cart instead,
    // which does not depend on an order that was never recorded.
    const only = cartCalls.length === 1 ? cartCalls[0] : undefined;

    return [
      {
        turn: turnNumberFrom(turnRoot.metadata, index + 1),
        message,
        answer,
        toolsCalled: tools.map((o) => o.name ?? "(unnamed)"),
        cartSnapshot: only ? cartFrom(only.output) : undefined,
        cartOps: cartCalls.map(cartOpFrom),
      },
    ];
  });
}

/** Apply one turn's cart mutations to the cart as it stood before the turn. */
function applyCartOps(before: string[], ops: CartOp[]): string[] {
  const cart = [...before];
  for (const op of ops) {
    if (!op.sku || op.failed) continue;
    if (op.action === "add") {
      if (!cart.includes(op.sku)) cart.push(op.sku);
    } else if (op.action === "remove") {
      const at = cart.indexOf(op.sku);
      if (at >= 0) cart.splice(at, 1);
    }
  }
  return cart;
}

// -------------------------------------------------------------------- scoring ---
export type SessionScore = {
  sessionId: string;
  turnsScored: number;
  applicableTurns: number;
  passedTurns: number;
  failingTurns: number[];
  fraction: number | undefined;
  comment: string;
};

export async function scoreSession(sessionId: string, traces: TraceRow[]): Promise<SessionScore> {
  const turns: ReconstructedTurn[] = [];
  for (const trace of traces) {
    turns.push(...reconstructTurns(await readObservations(trace.id)));
  }
  turns.sort((a, b) => a.turn - b.turn);

  let carried: string[] = [];
  const history: Array<{ role: "user" | "assistant"; content: string }> = [];
  let applicableTurns = 0;
  let passedTurns = 0;
  const failingTurns: number[] = [];
  const failComments: string[] = [];

  for (const turn of turns) {
    // A single reported snapshot beats a replay; several calls make the snapshot
    // ambiguous, so replay. A turn that never touched the cart leaves it exactly
    // as the previous turn did.
    carried =
      turn.cartSnapshot !== undefined ? turn.cartSnapshot : applyCartOps(carried, turn.cartOps);

    const ctx: EvalContext = {
      message: turn.message,
      answer: turn.answer,
      cartSkus: carried,
      toolsCalled: turn.toolsCalled,
      history: [...history],
    };
    const verdict = unverifiedCartClaim(ctx);
    if (verdict.applicable) {
      applicableTurns += 1;
      if (verdict.passed) passedTurns += 1;
      else {
        failingTurns.push(turn.turn);
        failComments.push(`turn ${turn.turn}: ${verdict.comment}`);
      }
    }

    history.push({ role: "user", content: turn.message });
    history.push({ role: "assistant", content: turn.answer });
  }

  const fraction = applicableTurns === 0 ? undefined : passedTurns / applicableTurns;
  const comment =
    applicableTurns === 0
      ? `No turn of this session made a verifiable add-to-cart claim, so there is nothing to score. ` +
        `${turns.length} turn(s) reconstructed from ${traces.length} trace(s).`
      : failingTurns.length === 0
        ? `${passedTurns} of ${applicableTurns} cart claim(s) verified across ${turns.length} turns. No turn misreported the cart.`
        : `${passedTurns} of ${applicableTurns} cart claim(s) verified. Misreported on turn ${failingTurns.join(", ")}. ` +
          failComments.join(" | ");

  return {
    sessionId,
    turnsScored: turns.length,
    applicableTurns,
    passedTurns,
    failingTurns,
    fraction,
    comment,
  };
}

export async function scoreLiveSessions(maxSessions: number): Promise<SessionScore[]> {
  const langfuse = langfuseClient();
  const sessions = await readRecentSessions(maxSessions);

  console.log("");
  console.log(`${BOLD}session-level scoring${OFF} ${DIM}(${SCORE_NAME})${OFF}`);
  console.log(`  ${sessions.length} recent session(s) read back from Langfuse`);

  const results: SessionScore[] = [];
  for (const { sessionId, traces } of sessions) {
    const score = await scoreSession(sessionId, traces);
    results.push(score);

    if (score.fraction === undefined) {
      console.log("");
      console.log(`  ${DIM}${sessionId}${OFF}  ${DIM}no score written${OFF}`);
      console.log(`    ${DIM}${score.comment}${OFF}`);
      continue;
    }

    // sessionId and NO traceId — that is what makes this a session score rather
    // than a score on whichever turn happened to be last.
    langfuse.score.create({
      name: SCORE_NAME,
      value: score.fraction,
      dataType: "NUMERIC",
      sessionId,
      comment: score.comment,
      metadata: {
        turnsReconstructed: score.turnsScored,
        applicableTurns: score.applicableTurns,
        failingTurns: score.failingTurns,
      },
    });

    const colour = score.fraction === 1 ? GREEN : RED;
    console.log("");
    console.log(
      `  ${BOLD}${sessionId}${OFF}  ${colour}${score.fraction.toFixed(2)}${OFF} ` +
        `${DIM}(${score.passedTurns}/${score.applicableTurns} applicable turns, ` +
        `${score.turnsScored} turns reconstructed)${OFF}`,
    );
    console.log(`    ${score.comment}`);
  }

  await langfuse.flush();

  const written = results.filter((r) => r.fraction !== undefined).length;
  console.log("");
  console.log(`${BOLD}${written} session score(s) written${OFF}, ${results.length - written} skipped for lack of evidence.`);
  if (written === 0 && results.length > 0) {
    console.log(
      `${DIM}  Every session came back with zero applicable turns, which means no turn made a ` +
        `verifiable add-to-cart claim. Broken-mode sessions land here by construction: their ` +
        `root observations carry no output, so there is no answer to read a claim out of.${OFF}`,
    );
  }
  return results;
}

async function main(): Promise<void> {
  await verifyProject();
  const argv = process.argv.slice(2);
  const idx = argv.indexOf("--sessions");
  const raw = idx >= 0 ? argv[idx + 1] : undefined;
  const maxSessions = raw !== undefined && /^\d+$/.test(raw) ? Number.parseInt(raw, 10) : 8;
  await scoreLiveSessions(maxSessions);
}

const isEntrypoint =
  process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isEntrypoint) {
  try {
    await main();
  } catch (err) {
    console.error(`\n${RED}✗${OFF} ${err instanceof Error ? err.message : String(err)}`);
    process.exitCode = 1;
  } finally {
    await flushTraces();
  }
}
