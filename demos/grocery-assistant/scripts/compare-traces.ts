/**
 * The headline script: run the SAME conversation twice, changing only the
 * instrumentation, then prove the difference against the Langfuse API.
 *
 * Both runs call the same model with the same tools and produce answers of the
 * same quality. Saying "one of them is observable" is an assertion. Counting the
 * generations whose input is null is a measurement, and a measurement is what
 * survives the question "how do you know?".
 *
 * Five things are counted, each of which blocks something concrete:
 *   1. distinct trace names        — one name per turn means no rule can target
 *                                    the chat endpoint and nothing aggregates.
 *   2. generations with null io    — nothing for any evaluator to read. This is
 *                                    the one that blocks all the others.
 *   3. observations with session.id — an observation-level evaluator or filter
 *                                    reads attributes off the OBSERVATION; an
 *                                    un-propagated session matches nothing.
 *   4. root has input AND output   — a trace's io mirrors its root observation,
 *                                    so a blank root is a blank Traces table.
 *   5. observations per turn        — the noise floor: the empty `postprocess`
 *                                    span is billable ingest that says nothing.
 *
 * ---------------------------------------------------------------------------
 * WHICH READ API EXISTS: the v3-vs-v4 gotcha, and why this file branches.
 *
 * The obvious way to read observations back is
 * `GET /api/public/v2/observations?fields=core,basic,io`. On a self-hosted
 * Langfuse v3 server that endpoint answers HTTP 404:
 *
 *   {"message":"The observations v2 API is only available in a Langfuse v4 write
 *     mode. Learn more at: https://langfuse.com/docs/v4",
 *    "error":"LangfuseNotFoundError"}
 *
 * This demo's default stack is 3.221.1, so the v2 path is unreachable there —
 * but the demo also documents Langfuse Cloud, which is v4. Both paths are
 * therefore implemented and selected from `GET /api/public/health`:
 *
 *   v3  → `GET /api/public/traces/{id}`, which returns the trace together with
 *         its full `observations` array. One request per turn, and input/output
 *         arrive ALREADY PARSED as objects.
 *   v4  → `GET /api/public/v2/observations?traceId=…&fields=core,basic,io,metadata`.
 *         `fields` MUST include `io` or input/output come back undefined, and
 *         there input/output are SERIALIZED JSON STRINGS.
 *
 * Traces are located by the ids `runTurn` hands back, never by
 * `GET /api/public/traces?sessionId=…`. That is not a stylistic choice: in
 * broken mode the session id is stamped into free-form metadata instead of being
 * propagated, so those traces have `sessionId: null` and a session query cannot
 * see them at all. Filtering by session to measure a session defect would
 * silently return an empty broken column — the worst kind of wrong answer,
 * because it looks like a dramatic finding.
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
import { CONVERSATIONS, getConversation } from "../src/conversations.js";
import { driveConversation, sessionUrl, type TurnRecord } from "./run-conversation.js";

const BOLD = "[1m";
const DIM = "[2m";
const GREEN = "[32m";
const RED = "[31m";
const OFF = "[0m";

const AUTH = `Basic ${Buffer.from(`${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}`).toString("base64")}`;

async function api<T>(path: string): Promise<T | undefined> {
  const res = await fetch(`${LANGFUSE_BASE_URL}${path}`, { headers: { Authorization: AUTH } });
  if (res.status === 404) return undefined; // Not ingested yet, or genuinely absent.
  if (!res.ok) {
    throw new Error(`GET ${path} → HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

// -------------------------------------------------------------- API version ---
type ApiFlavour = "v1" | "v2";
let flavour: ApiFlavour | undefined;
let serverVersion = "unknown";

async function detectApiFlavour(): Promise<ApiFlavour> {
  if (flavour) return flavour;
  const health = await api<{ version?: string }>("/api/public/health");
  serverVersion = health?.version ?? "unknown";
  // Major 3 has no v2 observations API. Anything else is treated as v4-or-later.
  flavour = serverVersion.startsWith("3.") ? "v1" : "v2";
  return flavour;
}

// ------------------------------------------------------------------ reading ---
/** One observation, normalised so the metrics do not care which endpoint served it. */
type ObsRow = {
  traceId: string;
  type: string;
  name: string;
  isRoot: boolean;
  hasInput: boolean;
  hasOutput: boolean;
  /**
   * The PROPAGATED OTel attribute, not a derived column.
   *
   * Read the same way on both paths on purpose. v4's observation rows also carry
   * a first-class `sessionId`, but Langfuse derives that from this attribute,
   * and the attribute is what an observation-level filter or evaluator actually
   * matches on — so reading it directly is both portable and closer to the thing
   * that breaks.
   *
   * Expect the good column to read slightly under the total rather than dead
   * level with it, and do not "fix" that. The spans the Langfuse SDK creates
   * itself — each turn's `handle-chat-message` root and the final
   * `conversation-snapshot` — have their session PROMOTED to the trace instead
   * of kept as a span attribute, so they are counted here as not carrying it
   * while the trace-level row above shows the grouping working. Trimming the
   * denominator to make the number look round would be curating the evidence.
   */
  sessionAttribute: string | undefined;
};

/** One turn's worth of trace, however it was fetched. */
type TraceFacts = { traceId: string; traceName: string; sessionId: string | undefined; rows: ObsRow[] };

type RawObservation = {
  id: string;
  traceId?: string | null;
  type: string;
  name?: string | null;
  parentObservationId?: string | null;
  isRootObservation?: boolean;
  input?: unknown;
  output?: unknown;
  metadata?: unknown;
};

function sessionAttributeOf(metadata: unknown): string | undefined {
  if (typeof metadata !== "object" || metadata === null) return undefined;
  const attrs = (metadata as { attributes?: Record<string, unknown> }).attributes;
  const value = attrs?.["session.id"];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** Non-empty, accounting for v4 handing io back as a JSON string. */
function isPresent(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value !== "string") return true;
  const trimmed = value.trim();
  if (trimmed === "" || trimmed === "null") return false;
  try {
    return JSON.parse(trimmed) !== null;
  } catch {
    return true; // A plain string answer is a perfectly good output.
  }
}

function toRow(traceId: string, o: RawObservation): ObsRow {
  return {
    traceId,
    type: o.type,
    name: o.name ?? "(unnamed)",
    isRoot: o.isRootObservation ?? o.parentObservationId == null,
    hasInput: isPresent(o.input),
    hasOutput: isPresent(o.output),
    sessionAttribute: sessionAttributeOf(o.metadata),
  };
}

async function readTrace(traceId: string): Promise<TraceFacts | undefined> {
  const version = await detectApiFlavour();

  if (version === "v1") {
    const trace = await api<{
      id: string;
      name?: string | null;
      sessionId?: string | null;
      observations?: RawObservation[];
    }>(`/api/public/traces/${encodeURIComponent(traceId)}`);
    if (!trace || !trace.observations || trace.observations.length === 0) return undefined;
    return {
      traceId,
      traceName: trace.name ?? "(unnamed)",
      sessionId: trace.sessionId ?? undefined,
      rows: trace.observations.map((o) => toRow(traceId, o)),
    };
  }

  const rows: ObsRow[] = [];
  let cursor: string | undefined;
  do {
    const qs = new URLSearchParams({
      traceId,
      // WITHOUT `io` here, input and output come back undefined and every
      // generation looks broken. `metadata` carries the OTel attributes.
      fields: "core,basic,io,metadata",
      limit: "100",
    });
    if (cursor) qs.set("cursor", cursor);
    const body = await api<{ data: RawObservation[]; meta?: { nextCursor?: string | null } }>(
      `/api/public/v2/observations?${qs.toString()}`,
    );
    if (!body) return undefined;
    rows.push(...body.data.map((o) => toRow(traceId, o)));
    cursor = body.meta?.nextCursor ?? undefined;
  } while (cursor);

  if (rows.length === 0) return undefined;
  const root = rows.find((r) => r.isRoot);
  return {
    traceId,
    // In broken mode the root observation's name IS the high-cardinality trace
    // name, so deriving it here works for both modes without a second request.
    traceName: root?.name ?? "(unnamed)",
    sessionId: rows.find((r) => r.sessionAttribute)?.sessionAttribute,
    rows,
  };
}

// ---------------------------------------------------------------- ingestion ---
/**
 * Progress that redraws one line, and only when a human is watching.
 *
 * Piping this script — into a log, a report, a colleague's terminal — is normal,
 * and carriage returns plus erase-line sequences turn into visible junk mid-table
 * there. So the spinner only exists on a TTY.
 */
function progress(text: string): void {
  if (process.stdout.isTTY) process.stdout.write(`${DIM}${text}${OFF}\r`);
}

function clearProgress(): void {
  if (process.stdout.isTTY) process.stdout.write("\r\u001b[K");
}

/**
 * Wait for the traces to actually be queryable — and to be FINISHED.
 *
 * Langfuse ingests asynchronously: the flush returns as soon as the batch is
 * accepted, and the worker writes to ClickHouse after that. Reading immediately
 * gives zero observations and a script that concludes "broken mode produced
 * nothing" — a false result far more damaging than a slow one, because it looks
 * exactly like the finding the demo is trying to make.
 *
 * "All trace ids resolve" is NOT sufficient, and assuming it was cost a wrong
 * table before this loop grew its second half. A trace becomes readable as soon
 * as its FIRST span lands, so a run gets reported as 7 of 7 traces while one of
 * them holds 3 of its 14 observations and its root has no output yet — which
 * shows up as a defect in the GOOD column that is really a defect in the reader.
 * So the loop also waits for the total observation count to stop moving between
 * rounds. A half-written trace is indistinguishable from a badly instrumented
 * one, and only one of those is worth showing a customer.
 */
async function readAllTraces(label: string, traceIds: string[]): Promise<TraceFacts[]> {
  const deadline = Date.now() + 30_000;
  let previousTotal = -1;
  for (let attempt = 1; ; attempt += 1) {
    const facts = (await Promise.all(traceIds.map(readTrace))).filter(
      (f): f is TraceFacts => f !== undefined,
    );
    const total = facts.reduce((sum, f) => sum + f.rows.length, 0);
    if (facts.length === traceIds.length && total > 0 && total === previousTotal) {
      clearProgress();
      return facts;
    }
    if (Date.now() >= deadline) {
      clearProgress();
      console.log(
        `${DIM}  gave up after 30s: ${facts.length}/${traceIds.length} ${label} traces ` +
          `queryable, ${total} observations and still moving. The numbers below are over ` +
          `what the API returned.${OFF}`,
      );
      return facts;
    }
    previousTotal = total;
    progress(
      `  waiting for ${label} ingestion: ${facts.length}/${traceIds.length} traces, ` +
        `${total} observations (attempt ${attempt})`,
    );
    await new Promise((r) => setTimeout(r, 2_000));
  }
}

// ------------------------------------------------------------------ metrics ---
type Metrics = {
  sessionId: string;
  turns: number;
  traceCount: number;
  traceNames: string[];
  distinctTraceNames: number;
  generations: number;
  generationsMissingIo: number;
  observations: number;
  observationsWithSession: number;
  rootsWithBothIo: number;
  tracesWithSessionId: number;
  perTurnCounts: number[];
};

function computeMetrics(sessionId: string, turns: number, facts: TraceFacts[]): Metrics {
  const names = facts.map((f) => f.traceName);
  const rows = facts.flatMap((f) => f.rows);
  const generations = rows.filter((r) => r.type === "GENERATION");

  let rootsWithBothIo = 0;
  for (const f of facts) {
    const root = f.rows.find((r) => r.isRoot);
    if (root?.hasInput && root.hasOutput) rootsWithBothIo += 1;
  }

  return {
    sessionId,
    turns,
    traceCount: facts.length,
    traceNames: names,
    distinctTraceNames: new Set(names).size,
    generations: generations.length,
    generationsMissingIo: generations.filter((g) => !g.hasInput || !g.hasOutput).length,
    observations: rows.length,
    observationsWithSession: rows.filter((r) => r.sessionAttribute === sessionId).length,
    rootsWithBothIo,
    tracesWithSessionId: facts.filter((f) => f.sessionId === sessionId).length,
    perTurnCounts: facts.map((f) => f.rows.length).sort((a, b) => a - b),
  };
}

function spread(counts: number[]): string {
  if (counts.length === 0) return "—";
  const min = counts[0] ?? 0;
  const max = counts[counts.length - 1] ?? 0;
  const mean = counts.reduce((a, b) => a + b, 0) / counts.length;
  return `${min} / ${mean.toFixed(1)} / ${max}`;
}

function pad(text: string, width: number): string {
  // Pad on VISIBLE length: the colour escapes carry no width.
  const visible = text.replace(/\[[0-9;]*m/g, "").length;
  return text + " ".repeat(Math.max(0, width - visible));
}

function printComparison(broken: Metrics, good: Metrics): void {
  const rows: Array<[string, string, string, boolean]> = [
    [
      "distinct trace names produced",
      String(broken.distinctTraceNames),
      String(good.distinctTraceNames),
      good.distinctTraceNames === 1 && broken.distinctTraceNames > 1,
    ],
    [
      "generations with null input or output",
      `${broken.generationsMissingIo} of ${broken.generations}`,
      `${good.generationsMissingIo} of ${good.generations}`,
      good.generationsMissingIo === 0 && broken.generationsMissingIo > 0,
    ],
    [
      "observations carrying session.id",
      `${broken.observationsWithSession} of ${broken.observations}`,
      `${good.observationsWithSession} of ${good.observations}`,
      good.observationsWithSession > broken.observationsWithSession,
    ],
    [
      "traces the Sessions view can group",
      `${broken.tracesWithSessionId} of ${broken.traceCount}`,
      `${good.tracesWithSessionId} of ${good.traceCount}`,
      good.tracesWithSessionId > broken.tracesWithSessionId,
    ],
    [
      "traces whose root has input AND output",
      `${broken.rootsWithBothIo} of ${broken.traceCount}`,
      `${good.rootsWithBothIo} of ${good.traceCount}`,
      good.rootsWithBothIo === good.traceCount && broken.rootsWithBothIo < broken.traceCount,
    ],
    [
      "observations per turn (min / mean / max)",
      spread(broken.perTurnCounts),
      spread(good.perTurnCounts),
      true,
    ],
  ];

  const W = 42;
  console.log("");
  console.log(`${BOLD}${pad("metric", W)} ${pad("broken", 18)} ${pad("good", 18)}${OFF}`);
  console.log("─".repeat(W + 40));
  for (const [label, b, g, better] of rows) {
    const mark = better ? `${GREEN}✓${OFF}` : `${RED}✗${OFF}`;
    console.log(`${pad(label, W)} ${pad(`${RED}${b}${OFF}`, 18)} ${pad(`${GREEN}${g}${OFF}`, 18)} ${mark}`);
  }

  console.log("");
  console.log(`${BOLD}per-turn observation counts${OFF}`);
  console.log(`  broken  ${broken.perTurnCounts.join(", ") || "—"}`);
  console.log(`  good    ${good.perTurnCounts.join(", ") || "—"}`);

  console.log("");
  console.log(`${BOLD}the trace names each run produced${OFF}`);
  console.log(`  ${DIM}broken — ${broken.distinctTraceNames} distinct name(s) for ${broken.traceCount} turn(s):${OFF}`);
  for (const n of new Set(broken.traceNames)) {
    console.log(`    ${n.length > 76 ? `${n.slice(0, 75)}…` : n}`);
  }
  console.log(`  ${DIM}good — ${good.distinctTraceNames} distinct name(s) for ${good.traceCount} turn(s):${OFF}`);
  for (const n of new Set(good.traceNames)) console.log(`    ${n}`);
}

// ----------------------------------------------------------------- the runs ---
export type CompareResult = {
  brokenSessionId: string;
  goodSessionId: string;
  broken: Metrics;
  good: Metrics;
};

/** Trace ids, in turn order, for the turns that actually produced one. */
function traceIdsOf(records: TurnRecord[]): string[] {
  return records
    .map((r) => r.result.traceId)
    .filter((id): id is string => typeof id === "string" && id.length > 0);
}

export async function compareTraces(conversationId?: string): Promise<CompareResult> {
  const first = CONVERSATIONS[0];
  if (!first) throw new Error("No conversations defined in src/conversations.ts");
  const conversation = conversationId ? getConversation(conversationId) : first;
  if (!conversation) throw new Error(`No conversation '${conversationId}'. Try --list on run-conversation.`);

  // One timestamp for both sessions so they sort next to each other in the UI.
  const stamp = Date.now();
  const brokenSessionId = `cmp-broken-${stamp}`;
  const goodSessionId = `cmp-good-${stamp}`;

  console.log("");
  console.log(`${BOLD}Same conversation, twice, instrumented two ways${OFF}`);
  console.log(`  conversation ${conversation.id} ${DIM}(${conversation.turns.length} turns each)${OFF}`);
  console.log(`  broken session  ${brokenSessionId}`);
  console.log(`  good session    ${goodSessionId}`);

  console.log("");
  console.log(`${DIM}running broken…${OFF}`);
  const brokenRecords = await driveConversation({
    conversation,
    sessionId: brokenSessionId,
    mode: "broken",
    extraTags: ["compare:broken"],
  });

  console.log(`${DIM}running good…${OFF}`);
  const goodRecords = await driveConversation({
    conversation,
    sessionId: goodSessionId,
    mode: "good",
    extraTags: ["compare:good"],
  });

  // Both runs must be on the wire before anything is read back.
  await flushTraces();

  const version = await detectApiFlavour();
  console.log("");
  console.log(
    `${DIM}Langfuse ${serverVersion} → reading back via the ` +
      `${version === "v1" ? "v1 trace API (v2 observations is v4-only)" : "v2 observations API"}${OFF}`,
  );

  const brokenFacts = await readAllTraces("broken", traceIdsOf(brokenRecords));
  const goodFacts = await readAllTraces("good", traceIdsOf(goodRecords));

  const broken = computeMetrics(brokenSessionId, brokenRecords.length, brokenFacts);
  const good = computeMetrics(goodSessionId, goodRecords.length, goodFacts);

  printComparison(broken, good);

  console.log("");
  console.log(`${BOLD}Open both and scroll the session view${OFF}`);
  console.log(`  broken  ${await sessionUrl(brokenSessionId)}`);
  console.log(
    `${DIM}          (the broken run's traces carry no sessionId at all, so this session ` +
      `is empty — which is the defect, not a bug in the link)${OFF}`,
  );
  console.log(`  good    ${await sessionUrl(goodSessionId)}`);

  return { brokenSessionId, goodSessionId, broken, good };
}

async function main(): Promise<void> {
  await verifyProject();
  const argv = process.argv.slice(2);
  const idx = argv.indexOf("--conversation");
  await compareTraces(idx >= 0 ? argv[idx + 1] : undefined);
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
