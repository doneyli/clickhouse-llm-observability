/**
 * Drive one shopper conversation end to end and grade every turn as it goes.
 *
 * This is the script you run first, and the one the other scripts are built on:
 * `driveConversation` is exported and reused by compare-traces, run-experiment
 * and run-demo so there is exactly one definition of "how a conversation is
 * played" in the demo. If the turn loop is subtly different in each script, the
 * comparison the demo is built to make stops meaning anything.
 *
 * Usage:
 *   tsx scripts/run-conversation.ts --list
 *   tsx scripts/run-conversation.ts --conversation dropped-dietary-constraint
 *   tsx scripts/run-conversation.ts --instrumentation broken
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
import { runTurn, type ChatMessage, type InstrumentationMode, type TurnResult } from "../src/assistant.js";
import { getSessionState, resetSessionState } from "../src/tools.js";
import { OFFERS, formatMoney, getProduct } from "../src/catalog.js";
import { CONVERSATIONS, getConversation, type Conversation } from "../src/conversations.js";
import { runDeterministicEvaluators, type EvalContext, type Verdict } from "../src/evaluators/deterministic.js";

// ------------------------------------------------------------ discount truth ---
/**
 * The discount that ACTUALLY applies to the cart right now.
 *
 * This duplicates the private `applicableOffers` in src/tools.ts, which is not
 * exported. Duplicating it is deliberate rather than convenient: the evaluator
 * must not be able to agree with the tool by sharing a bug with it. If both are
 * wrong in the same way the score is green and the shopper is still overcharged.
 */
export function currentDiscountCents(sessionId: string): number {
  const state = getSessionState(sessionId);
  const subtotal = state.cart.reduce((sum, line) => {
    const p = getProduct(line.sku);
    return sum + (p ? p.priceCents * line.quantity : 0);
  }, 0);
  const cartSkus = state.cart.map((l) => l.sku);
  const cats = cartSkus.map((s) => getProduct(s)?.category);

  return OFFERS.filter((offer) => {
    if (offer.requiresClip && !state.clippedOffers.includes(offer.offerId)) return false;
    if (offer.minBasketCents !== undefined && subtotal < offer.minBasketCents) return false;
    if (offer.skus && !offer.skus.some((s) => cartSkus.includes(s))) return false;
    if (offer.categories && !offer.categories.some((c) => cats.includes(c))) return false;
    return true;
  }).reduce((sum, o) => sum + o.discountCents, 0);
}

/**
 * The savings TOTAL the assistant quoted this turn, if it quoted one.
 *
 * Deliberately narrow. A loose "find a dollar amount near the word off" pattern
 * matches the offer catalog's own wording — "$1.00 off any pasta" — and reports
 * a failure that did not happen. A false FAIL is more expensive than a missed
 * one, because the first red score a team does not believe is the last score
 * they look at. So this only fires on phrasings that are a running total, and
 * returns undefined otherwise, which makes the evaluator not-applicable.
 */
const QUOTED_SAVINGS_PATTERNS: RegExp[] = [
  /(?:total(?:ling)?|combined)\s+(?:savings?|discounts?)\D{0,20}\$(\d+(?:\.\d{2})?)/i,
  /(?:savings?|discounts?)\s*(?:of|is|are|:|come to|comes to|total(?:s|ling)?)\D{0,10}\$(\d+(?:\.\d{2})?)/i,
  /you(?:'re| are)\s+sav\w+\D{0,20}\$(\d+(?:\.\d{2})?)/i,
  /\$(\d+(?:\.\d{2})?)\s+in\s+(?:total\s+)?savings/i,
  /\$(\d+(?:\.\d{2})?)\s+off\s+(?:your|the)\s+(?:order|total|basket|cart|subtotal)/i,
  /sav\w+\s+(?:you\s+)?\$(\d+(?:\.\d{2})?)\s+(?:in\s+)?total/i,
];

export function extractQuotedDiscountCents(answer: string): number | undefined {
  for (const re of QUOTED_SAVINGS_PATTERNS) {
    const m = re.exec(answer);
    if (m?.[1] !== undefined) return Math.round(Number.parseFloat(m[1]) * 100);
  }
  return undefined;
}

// ------------------------------------------------------------------ the loop ---
export type TurnRecord = {
  turnIndex: number;
  message: string;
  result: TurnResult;
  verdicts: Verdict[];
};

export type DriveOptions = {
  conversation: Conversation;
  sessionId: string;
  mode?: InstrumentationMode;
  extraTags?: string[];
  /** Called after each turn, once it has been graded. Printing lives here. */
  onTurn?: (record: TurnRecord) => void;
};

/**
 * Play every turn of a conversation, accumulating history, and grade each turn.
 *
 * `history` is what makes this a conversation rather than five unrelated
 * questions — the constraint from turn 1 only survives to turn 5 because it is
 * still in the messages array. Note it is passed to the MODEL, not restated on
 * the trace root; see the comments in src/assistant.ts for why that distinction
 * is the whole point of the good/broken split.
 */
export async function driveConversation(opts: DriveOptions): Promise<TurnRecord[]> {
  const { conversation, sessionId, mode = "good", extraTags = [], onTurn } = opts;

  // Cart and clipped offers are keyed by session, so a re-run with the same id
  // would inherit the last run's basket and every total would be wrong.
  resetSessionState(sessionId);

  const history: ChatMessage[] = [];
  const records: TurnRecord[] = [];

  for (const [turnIndex, message] of conversation.turns.entries()) {
    const isFinalTurn = turnIndex === conversation.turns.length - 1;

    const result = await runTurn({
      message,
      sessionId,
      userId: conversation.userId,
      history: [...history],
      turnIndex,
      isFinalTurn,
      mode,
      extraTags: [...extraTags, `conversation:${conversation.id}`],
    });

    // Read the discount truth AFTER the turn: the tools the model just called
    // are what changed it.
    const quoted = extractQuotedDiscountCents(result.answer);
    const ctx: EvalContext = {
      message,
      answer: result.answer,
      cartSkus: result.cartSkus,
      toolsCalled: result.toolsCalled,
      history: [...history],
      ...(quoted !== undefined
        ? { quotedDiscountCents: quoted, actualDiscountCents: currentDiscountCents(sessionId) }
        : {}),
    };

    const record: TurnRecord = {
      turnIndex,
      message,
      result,
      verdicts: runDeterministicEvaluators(ctx),
    };
    records.push(record);
    onTurn?.(record);

    history.push({ role: "user", content: message });
    history.push({ role: "assistant", content: result.answer });
  }

  return records;
}

// ------------------------------------------------------------------ printing ---
const GREEN = "[32m";
const RED = "[31m";
const DIM = "[2m";
const BOLD = "[1m";
const OFF = "[0m";

/** One line per verdict: PASS, FAIL, or n/a when the turn gave it nothing. */
export function formatVerdict(v: Verdict): string {
  const label = !v.applicable
    ? `${DIM}n/a ${OFF}`
    : v.passed
      ? `${GREEN}PASS${OFF}`
      : `${RED}FAIL${OFF}`;
  const body = v.applicable ? v.comment : `${DIM}${v.comment}${OFF}`;
  return `    ${label}  ${v.name.padEnd(30)} ${body}`;
}

function oneLine(text: string, width = 150): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > width ? `${flat.slice(0, width - 1)}…` : flat;
}

export function printTurn(record: TurnRecord): void {
  const { turnIndex, message, result, verdicts } = record;
  console.log("");
  console.log(`${BOLD}── turn ${turnIndex + 1} ${OFF}${DIM}trace ${result.traceId ?? "(none)"}${OFF}`);
  console.log(`  ${BOLD}shopper${OFF}  ${oneLine(message)}`);
  console.log(`  assistant  ${oneLine(result.answer)}`);
  console.log(
    `  ${DIM}tools${OFF}      ${result.toolsCalled.join(", ") || "(none)"}`,
  );
  console.log(
    `  ${DIM}cart${OFF}       ${result.cartSkus.join(", ") || "(empty)"}  ` +
      `subtotal ${formatMoney(result.cartSubtotalCents)}`,
  );
  for (const v of verdicts) console.log(formatVerdict(v));
}

// ------------------------------------------------------------- session links ---
let cachedProjectId: string | null | undefined;

/**
 * The Sessions deep link, when the project id can be resolved.
 *
 * `/project/<id>/sessions/<sessionId>` needs the project's internal id, which is
 * not in .env — but /api/public/projects hands it over for the keys we already
 * hold, so one request buys a link the presenter can click. If that lookup fails
 * we say so and point at the Sessions list instead of printing a URL that 404s.
 */
export async function sessionUrl(sessionId: string): Promise<string> {
  if (cachedProjectId === undefined) {
    cachedProjectId = null;
    try {
      const auth = Buffer.from(`${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}`).toString("base64");
      const res = await fetch(`${LANGFUSE_BASE_URL}/api/public/projects`, {
        headers: { Authorization: `Basic ${auth}` },
      });
      if (res.ok) {
        const body = (await res.json()) as { data?: Array<{ id?: string }> };
        cachedProjectId = body.data?.[0]?.id ?? null;
      }
    } catch {
      cachedProjectId = null;
    }
  }
  return cachedProjectId
    ? `${LANGFUSE_BASE_URL}/project/${cachedProjectId}/sessions/${encodeURIComponent(sessionId)}`
    : `${LANGFUSE_BASE_URL} → Sessions, then filter for '${sessionId}' ` +
        `(could not resolve the project id, so no direct link)`;
}

export function summarizeVerdicts(records: TurnRecord[]): void {
  const byName = new Map<string, { pass: number; fail: number; na: number }>();
  for (const r of records) {
    for (const v of r.verdicts) {
      const row = byName.get(v.name) ?? { pass: 0, fail: 0, na: 0 };
      if (!v.applicable) row.na += 1;
      else if (v.passed) row.pass += 1;
      else row.fail += 1;
      byName.set(v.name, row);
    }
  }
  console.log("");
  console.log(`${BOLD}evaluator summary${OFF} ${DIM}(over ${records.length} turns)${OFF}`);
  for (const [name, row] of byName) {
    const applicable = row.pass + row.fail;
    const rate = applicable === 0 ? "—" : `${row.pass}/${applicable}`;
    const colour = row.fail > 0 ? RED : applicable > 0 ? GREEN : DIM;
    console.log(
      `  ${colour}${name.padEnd(30)}${OFF} passed ${rate}` +
        `${DIM}   (${row.na} turn(s) not applicable)${OFF}`,
    );
  }
}

// ----------------------------------------------------------------- entrypoint ---
type Args = {
  mode: InstrumentationMode;
  conversationId: string | undefined;
  sessionId: string | undefined;
  list: boolean;
};

export function parseArgs(argv: string[]): Args {
  const args: Args = { mode: "good", conversationId: undefined, sessionId: undefined, list: false };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (flag === "--list") args.list = true;
    else if (flag === "--instrumentation" && value) {
      if (value !== "good" && value !== "broken") {
        throw new Error(`--instrumentation must be 'good' or 'broken', got '${value}'`);
      }
      args.mode = value;
      i += 1;
    } else if (flag === "--conversation" && value) {
      args.conversationId = value;
      i += 1;
    } else if (flag === "--session-id" && value) {
      args.sessionId = value;
      i += 1;
    }
  }
  return args;
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));

  if (args.list) {
    console.log(`${BOLD}Conversations${OFF} ${DIM}(--conversation <id>)${OFF}`);
    for (const c of CONVERSATIONS) {
      console.log(`  ${c.id.padEnd(30)} ${c.turns.length} turns  ${DIM}${c.title}${OFF}`);
    }
    return;
  }

  await verifyProject();

  const first = CONVERSATIONS[0];
  if (!first) throw new Error("No conversations defined in src/conversations.ts");
  const conversation = args.conversationId ? getConversation(args.conversationId) : first;
  if (!conversation) {
    throw new Error(
      `No conversation '${args.conversationId}'. Run with --list to see the ids.`,
    );
  }

  const sessionId = args.sessionId ?? `${args.mode}-${conversation.id}-${Date.now()}`;

  console.log("");
  console.log(`${BOLD}${conversation.title}${OFF}`);
  console.log(
    `  conversation ${conversation.id}   instrumentation ${BOLD}${args.mode}${OFF}   ` +
      `failure mode ${conversation.failureMode}`,
  );
  console.log(`  session ${sessionId}   user ${conversation.userId}`);

  const records = await driveConversation({
    conversation,
    sessionId,
    mode: args.mode,
    onTurn: printTurn,
  });

  summarizeVerdicts(records);

  console.log("");
  console.log(`${BOLD}In Langfuse${OFF}  ${await sessionUrl(sessionId)}`);
  if (args.mode === "broken") {
    console.log(
      `${DIM}  Broken mode: expect one trace name per turn, empty generations, and a ` +
        `session view that repeats the whole conversation on every turn.${OFF}`,
    );
  }
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
    // Every script here is short-lived, so an unflushed batch means the run
    // happened and Langfuse is empty. Flush on the error path too.
    await flushTraces();
  }
}
