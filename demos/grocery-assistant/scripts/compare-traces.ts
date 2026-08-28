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
 *   5. observations per turn        — the noise floor: empty spans added
 *                                    defensively are billable ingest.
 *
 * API NOTE, and it is load-bearing. The task for this script specifies
 * `GET /api/public/v2/observations?fields=core,basic,io`, where input/output
 * arrive as SERIALIZED JSON STRINGS. That endpoint does not exist on a
 * self-hosted Langfuse v3 server — it answers 404 with "The observations v2 API
 * is only available in a Langfuse v4 write mode" — and this demo's stack is
 * 3.221.1. So the reader below probes v2 once and falls back to the v1
 * `GET /api/public/observations` endpoint, which takes `traceId` rather than
 * `sessionId` and returns input/output ALREADY PARSED. Both paths are
 * implemented so this script keeps working when the server moves to v4.
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
import { driveConversation, sessionUrl } from "./run-conversation.js";

const BOLD = "[1m";
const DIM = "[2m";
const GREEN = "[32m";
const RED = "[31m";
const OFF = "[0m";

const AUTH = `Basic ${Buffer.from(`${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}`).toString("base64")}`;

async function api<T>(path: string): Promise<T> {
  const res = await fetch(`${LANGFUSE_BASE_URL}${path}`, { headers: { Authorization: AUTH } });
  if (!res.ok) {
    throw new Error(`GET ${path} → HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

// -------------------------------------------------------------- API version ---
type ApiFlavour = "v2" | "v1";
let flavour: ApiFlavour | undefined;

async function detectApiFlavour(): Promise<ApiFlavour> {
  if (flavour) return flavour;
  const res = await fetch(`${LANGFUSE_BASE_URL}/api/public/v2/observations?limit=1&fields=core`, {
    headers: { Authorization: AUTH },
  });
  flavour = res.ok ? "v2" : "v1";
  return flavour;
}

// ------------------------------------------------------------------ reading ---
/** One observation, normalised so the metrics do not care which endpoint served it. */
type ObsRow = {
  id: string;
  traceId: string;
  type: string;
  name: string;
  isRoot: boolean;
  hasInput: boolean;
  hasOutput: boolean;
  /**
   * The PROPAGATED OTel attribute, not the derived column.
   *
   * Both endpoints are read the same way on purpose. v2 also exposes a
   * first-class `sessionId`, but Langfuse derives that from this attribute, and
   * the attribute is what an observation-level filter or evaluator actually
   * matches on — so reading it directly is both portable across the two
   * endpoints and closer to the thing that breaks.
   */
  sessionAttribute: string | undefined;
};

type MetadataWithAttributes = { attributes?: Record<string, unknown> };

function sessionAttributeOf(metadata: unknown): string | undefined {
  if (typeof metadata !== "object" || metadata === null) return undefined;
  const attrs = (metadata as MetadataWithAttributes).attributes;
  const value = attrs?.["session.id"];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** Non-empty after accounting for v2 handing io back as a JSON string. */
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

type V1Observation = {
  id: string;
  traceId: string;
  type: string;
  name?: string | null;
  parentObservationId?: string | null;
  input?: unknown;
  output?: unknown;
  metadata?: unknown;
};

type V2Observation = V1Observation & { isRootObservation?: boolean };

function toRow(o: V2Observation): ObsRow {
  return {
    id: o.id,
    traceId: o.traceId,
    type: o.type,
    name: o.name ?? "(unnamed)",
    isRoot: o.isRootObservation ?? o.parentObservationId == null,
    hasInput: isPresent(o.input),
    hasOutput: isPresent(o.output),
    sessionAttribute: sessionAttributeOf(o.metadata),
  };
}

/**
 * Every observation for a set of traces.
 *
 * v3's paginated list endpoints report a `meta.totalItems` that does not match
 * the number of rows returned — 9 for a 7-trace session, in this project — so
 * pagination stops when a page comes back short rather than when a claimed
 * total is reached. Trusting that count is how a script reports a number the UI
 * disagrees with.
 */
async function readObservations(traceIds: string[]): Promise<ObsRow[]> {
  const version = await detectApiFlavour();
  const rows: ObsRow[] = [];

  for (const traceId of traceIds) {
    if (version === "v2") {
      let cursor: string | undefined;
      do {
        const qs = new URLSearchParams({
          traceId,
          // WITHOUT `io` in `fields`, input and output come back undefined and
          // every generation looks broken. `metadata` carries the OTel attributes.
          fields: "core,basic,io,metadata",
          limit: "100",
        });
        if (cursor) qs.set("cursor", cursor);
        const body = await api<{ data: V2Observation[]; meta?: { nextCursor?: string | null } }>(
          `/api/public/v2/observations?${qs.toString()}`,
        );
        rows.push(...body.data.map(toRow));
        cursor = body.meta?.nextCursor ?? undefined;
      } while (cursor);
    } else {
      for (let page = 1; ; page += 1) {
        const body = await api<{ data: V1Observation[] }>(
          `/api/public/observations?traceId=${encodeURIComponent(traceId)}&page=${page}&limit=100`,
        );
        rows.push(...body.data.map(toRow));
        if (body.data.length < 100) break;
      }
    }
  }
  return rows;
}

type TraceRow = { id: string; name?: string | null; sessionId?: string | null };

async function readTraces(sessionId: string): Promise<TraceRow[]> {
  const out: TraceRow[] = [];
  for (let page = 1; ; page += 1) {
    const body = await api<{ data: TraceRow[] }>(
      `/api/public/traces?sessionId=${encodeURIComponent(sessionId)}&page=${page}&limit=50`,
    );
    out.push(...body.data);
    if (body.data.length < 50) break;
  }
  return out;
}

// ---------------------------------------------------------------- ingestion ---
/**
 * Wait for the traces to actually be queryable.
 *
 * Langfuse ingests asynchronously: the flush returns as soon as the batch is
 * accepted, and the worker writes to ClickHouse after that. Reading immediately
 * gives zero observations and a script that concludes "broken mode produced
 * nothing" — a false result far more damaging than a slow one, because it looks
 * like exactly the finding the demo is trying to make.
 */
async function waitForIngestion(sessionId: string, expectedTraces: number): Promise<TraceRow[]> {
  const deadline = Date.now() + 30_000;
  let traces: TraceRow[] = [];
  for (let attempt = 1; ; attempt += 1) {
    traces = await readTraces(sessionId);
    if (traces.length >= expectedTraces) {
      const first = traces[0];
      if (first && (await readObservations([first.id])).length > 0) return traces;
    }
    if (Date.now() >= deadline) {
      console.log(
        `${DIM}  gave up waiting after 30s: ${sessionId} has ${traces.length}/${expectedTraces} ` +
          `traces queryable. Numbers below are what the API returned.${OFF}`,
      );
      return traces;
    }
    process.stdout.write(`${DIM}  waiting for ingestion of ${sessionId} (attempt ${attempt})…\r${OFF}`);
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
  perTurnCounts: number[];
};

function computeMetrics(sessionId: string, turns: number, traces: TraceRow[], rows: ObsRow[]): Metrics {
  const names = traces.map((t) => t.name ?? "(unnamed)");
  const generations = rows.filter((r) => r.type === "GENERATION");
  const byTrace = new Map<string, ObsRow[]>();
  for (const r of rows) byTrace.set(r.traceId, [...(byTrace.get(r.traceId) ?? []), r]);

  let rootsWithBothIo = 0;
  for (const traceRows of byTrace.values()) {
    const root = traceRows.find((r) => r.isRoot);
    if (root?.hasInput && root.hasOutput) rootsWithBothIo += 1;
  }

  return {
    sessionId,
    turns,
    traceCount: traces.length,
    traceNames: names,
    distinctTraceNames: new Set(names).size,
    generations: generations.length,
    generationsMissingIo: generations.filter((g) => !g.hasInput || !g.hasOutput).length,
    observations: rows.length,
    observationsWithSession: rows.filter((r) => r.sessionAttribute === sessionId).length,
    rootsWithBothIo,
    perTurnCounts: [...byTrace.values()].map((v) => v.length).sort((a, b) => a - b),
  };
}

function spread(counts: number[]): string {
  if (counts.length === 0) return "—";
  const min = counts[0] ?? 0;
  const max = counts[counts.length - 1] ?? 0;
  const mean = counts.reduce((a, b) => a + b, 0) / counts.length;
  return `${min} / ${mean.toFixed(1)} / ${max}`;
}

/** Green when the good column is the one you would want, red when it is not. */
function verdictMark(better: boolean): string {
  return better ? `${GREEN}✓${OFF}` : `${RED}✗${OFF}`;
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

  const width = 42;
  console.log("");
  console.log(
    `${BOLD}${"metric".padEnd(width)} ${"broken".padEnd(18)} ${"good".padEnd(18)}${OFF}`,
  );
  console.log("─".repeat(width + 40));
  for (const [label, b, g, better] of rows) {
    console.log(`${label.padEnd(width)} ${b.padEnd(18)} ${g.padEnd(18)} ${verdictMark(better)}`);
  }

  console.log("");
  console.log(`${BOLD}per-turn observation counts${OFF}`);
  console.log(`  broken  ${broken.perTurnCounts.join(", ") || "—"}`);
  console.log(`  good    ${good.perTurnCounts.join(", ") || "—"}`);

  console.log("");
  console.log(`${BOLD}the trace names each run produced${OFF}`);
  console.log(`  ${DIM}broken (${broken.distinctTraceNames} distinct):${OFF}`);
  for (const n of [...new Set(broken.traceNames)]) {
    console.log(`    ${n.length > 80 ? `${n.slice(0, 79)}…` : n}`);
  }
  console.log(`  ${DIM}good (${good.distinctTraceNames} distinct):${OFF}`);
  for (const n of [...new Set(good.traceNames)]) console.log(`    ${n}`);
}

// ----------------------------------------------------------------- the runs ---
export type CompareResult = {
  brokenSessionId: string;
  goodSessionId: string;
  broken: Metrics;
  good: Metrics;
};

export async function compareTraces(conversationId?: string): Promise<CompareResult> {
  const first = CONVERSATIONS[0];
  if (!first) throw new Error("No conversations defined in src/conversations.ts");
  const conversation = conversationId ? getConversation(conversationId) : first;
  if (!conversation) throw new Error(`No conversation '${conversationId}'.`);

  // One timestamp for both sessions so they sort together in the Sessions list.
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

  const flavourUsed = await detectApiFlavour();
  console.log("");
  console.log(
    `${DIM}reading back via the ${flavourUsed === "v2" ? "v2 observations API" : "v1 observations API (v2 is not available on this server)"}${OFF}`,
  );

  const brokenTraces = await waitForIngestion(brokenSessionId, brokenRecords.length);
  const goodTraces = await waitForIngestion(goodSessionId, goodRecords.length);

  const broken = computeMetrics(
    brokenSessionId,
    brokenRecords.length,
    brokenTraces,
    await readObservations(brokenTraces.map((t) => t.id)),
  );
  const good = computeMetrics(
    goodSessionId,
    goodRecords.length,
    goodTraces,
    await readObservations(goodTraces.map((t) => t.id)),
  );

  printComparison(broken, good);

  console.log("");
  console.log(`${BOLD}Open both and scroll the session view${OFF}`);
  console.log(`  broken  ${await sessionUrl(brokenSessionId)}`);
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
