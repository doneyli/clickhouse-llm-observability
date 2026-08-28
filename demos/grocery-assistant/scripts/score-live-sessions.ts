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

async function readObservations(traceId: string): Promise<ObsRow[]> {
  const out: ObsRow[] = [];
  for (let page = 1; ; page += 1) {
    const body = await api<{ data: ObsRow[] }>(
      `/api/public/observations?traceId=${encodeURIComponent(traceId)}&page=${page}&limit=100`,
    );
    out.push(...body.data);
    if (body.data.length < 100) break;
  }
  return out;
}

// ----------------------------------------------------------- reconstruction ---
type ReconstructedTurn = {
  turn: number;
  message: string;
  answer: string;
  toolsCalled: string[];
  /** Cart AFTER the turn. Undefined when this turn never touched the cart. */
  cartSkus: string[] | undefined;
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

function reconstructTurn(observations: ObsRow[], fallbackTurn: number): ReconstructedTurn | undefined {
  const root = observations.find((o) => o.parentObservationId == null);
  if (!root) return undefined;

  const message = messageFrom(root.input);
  const answer = asString(root.output);
  if (message === undefined || answer === undefined) return undefined;

  const tools = observations.filter((o) => o.type === "TOOL");
  // Last by start time: the cart as it stood when the turn ended.
  const cartCalls = tools
    .filter((o) => o.name === "manage_cart")
    .sort((a, b) => a.startTime.localeCompare(b.startTime));
  const lastCart = cartCalls[cartCalls.length - 1];

  return {
    turn: turnNumberFrom(root.metadata, fallbackTurn),
    message,
    answer,
    toolsCalled: tools.map((o) => o.name ?? "(unnamed)"),
    cartSkus: lastCart ? cartFrom(lastCart.output) : undefined,
  };
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
  for (const [index, trace] of traces.entries()) {
    const turn = reconstructTurn(await readObservations(trace.id), index + 1);
    if (turn) turns.push(turn);
  }
  turns.sort((a, b) => a.turn - b.turn);

  // Cart carried forward: a turn that answered a question without touching the
  // cart leaves it exactly as the previous turn did.
  let carried: string[] = [];
  const history: Array<{ role: "user" | "assistant"; content: string }> = [];
  let applicableTurns = 0;
  let passedTurns = 0;
  const failingTurns: number[] = [];
  const failComments: string[] = [];

  for (const turn of turns) {
    if (turn.cartSkus !== undefined) carried = turn.cartSkus;

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
        `${turns.length} of ${traces.length} turn(s) were reconstructable.`
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
      `${DIM}  Every session came back with zero applicable turns. That is a property of the ` +
        `evaluator, not of the sessions: unverifiedCartClaim in src/evaluators/deterministic.ts ` +
        `scans FORWARD from the add verb to the next sentence break, and the assistant writes ` +
        `"**Whole Milk** (DRY-2001) — added!", putting the SKU before the verb. See the note in ` +
        `the report accompanying these scripts.${OFF}`,
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
