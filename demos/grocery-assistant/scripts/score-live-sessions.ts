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

/**
 * The most recent sessions, newest first, grouped from the traces list.
 *
 * Grouping traces rather than calling /api/public/sessions keeps one shape to
 * parse and gives an ordering for free. This project holds only this demo's
 * traffic, so every session here is a demo session.
 */
async function readRecentSessions(maxSessions: number): Promise<Array<{ sessionId: string; traces: TraceRow[] }>> {
  const traces: TraceRow[] = [];
  for (let page = 1; page <= 10; page += 1) {
    const body = await api<{ data: TraceRow[] }>(`/api/public/traces?page=${page}&limit=100`);
    traces.push(...body.data);
    if (body.data.length < 100) break;
  }

  const grouped = new Map<string, TraceRow[]>();
  for (const t of traces) {
    if (!t.sessionId) continue;
    grouped.set(t.sessionId, [...(grouped.get(t.sessionId) ?? []), t]);
  }

  // The traces list already comes back newest first, so first-seen is newest.
  return [...grouped.entries()].slice(0, maxSessions).map(([sessionId, rows]) => ({ sessionId, traces: rows }));
}

/**
 * One turn's observations.
 *
 * `GET /api/public/traces/{id}` returns the trace WITH its full `observations`
 * array, so a turn costs one request instead of a paged list — and on this v3
 * server input/output arrive already parsed as objects.
 */
async function readObservations(traceId: string): Promise<ObsRow[]> {
  const trace = await api<{ observations?: ObsRow[] }>(
    `/api/public/traces/${encodeURIComponent(traceId)}`,
  );
  return trace.observations ?? [];
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
