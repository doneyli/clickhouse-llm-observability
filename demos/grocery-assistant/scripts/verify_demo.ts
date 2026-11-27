/**
 * Readiness check for grocery-assistant: reads Langfuse back and says READY or NOT READY.
 *
 *   npx tsx scripts/verify_demo.ts           # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
 *   npx tsx scripts/verify_demo.ts --links   # also print the link each act opens
 *   npx tsx scripts/verify_demo.ts --quick   # readiness after `npm run demo -- --quick`: Act 3's dataset run only warns
 *
 * The TypeScript port of demos/_template/scripts/verify_demo.py, with the same output
 * contract. An HTTP 200 proves little on its own (a trace can exist before its
 * observations land), so every check counts real rows. Node built-ins plus the demo's own
 * key-free modules (src/conversations.ts, src/dataset.ts, src/readback.ts and the evaluator
 * names in src/evaluators/deterministic.ts): it does not
 * import src/env.ts, which refuses to load without a .env, and it needs none of the
 * demo's dependencies. No model calls; a handful of Langfuse reads, done in seconds.
 *
 * Read APIs: projects, Observations API v2, Scores API v3 and the experiments API, never
 * the legacy trace endpoints. A self-hosted Langfuse v3 server answers Observations v2
 * and the experiments API with 404, so on a 3.x server (read from /api/public/health,
 * the same switch scripts/compare-traces.ts uses) — and only there — observation reads
 * fall back to the v1 observation list and the dataset-run read to /datasets/{name}/runs.
 * Scores v3, /v2/datasets and /dataset-items work on both; on a 3.x server the dataset
 * run's evaluator scores are read from Scores v3 by the run items' trace ids.
 *
 * Each act's reads are isolated: a failed read is a FAIL for a required act and a WARN
 * for an optional one, and the other acts still run.
 *
 * tests/verify_demo.test.ts covers the logic against a fake Langfuse.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { DATASET_NAME, canonical, desiredItems, type ItemDefinition } from "../src/dataset.js";
import { EVALUATOR_NAMES } from "../src/evaluators/deterministic.js";
import { apiFlavourFor, cursorAll, pagesAll, sessionOf, type ApiFlavour, type Get } from "../src/readback.js";

export const DEMO_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const REPO_ROOT = resolve(DEMO_DIR, "..", "..");

// --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with the scripts named.
export const TRACE_NAME = "handle-chat-message"; // src/assistant.ts TRACE_NAME (good and collapsed roots)
export const BROKEN_NAME_PREFIX = "chat: "; // broken roots are named after the shopper's message
export { DATASET_NAME };
export const EXPECTED_ITEMS = desiredItems(); // what npm run seed:dataset writes (src/dataset.ts)
export const EXPECTED_ITEM_IDS = EXPECTED_ITEMS.map((i) => i.id);
export const EXPERIMENTS_SINCE = "2026-01-01T00:00:00Z"; // the experiments API needs a lower bound; predates the demo
export const EXPERIMENT_PREFIX = "deterministic-board-"; // scripts/run-experiment.ts runName
export const SESSION_SCORE = "conversation-cart-integrity"; // scripts/score-live-sessions.ts SCORE_NAME
export const SESSION_PREFIX = { good: "cmp-good-", broken: "cmp-broken-", collapsed: "cmp-collapsed-" }; // compare-traces.ts
export const DEFAULT_BASE_URL = "http://localhost:3001"; // src/env.ts default
export const MIN_NODE_MAJOR = 22; // @langfuse/vercel-ai-sdk requires Node 22+
export const LOOKBACK_DAYS = 7;
export const FRESH_HOURS = 24; // older than this only warns: re-run the day before
export const SEED_CMD = "npm run seed:dataset";
export const PREP_CMD = "npm run demo";
const TIMEOUT_MS = 10_000;

// --- Result helpers -----------------------------------------------------------------------

export type Status = "PASS" | "FAIL" | "WARN";
export type Result = { status: Status; required: boolean; what: string };
export const RESULTS: Result[] = [];

/** Record one check and print its line. A failed optional check is a WARN, not a FAIL. */
export function check(ok: boolean, what: string, opts: { required?: boolean; hint?: string } = {}): boolean {
  const required = opts.required ?? true;
  const status: Status = ok ? "PASS" : required ? "FAIL" : "WARN";
  RESULTS.push({ status, required, what });
  console.log(`  ${status}  ${what}` + (opts.hint && !ok ? `  ->  ${opts.hint}` : ""));
  return ok;
}

/** The closing line and the exit code: 0 when no required check failed, 1 otherwise. */
export function summary(results: Result[] = RESULTS): [string, number] {
  const passed = results.filter((r) => r.status === "PASS").length;
  const failed = results.filter((r) => r.status === "FAIL").length;
  const warned = results.filter((r) => r.status === "WARN").length;
  let detail = `${passed}/${results.length} checks passed`;
  if (failed) detail += `, ${failed} required failed`;
  if (warned) detail += `, ${warned} warning${warned !== 1 ? "s" : ""}`;
  return failed ? [`NOT READY (${detail})`, 1] : [`READY (${detail})`, 0];
}

// --- Environment and manifest -------------------------------------------------------------

function stripComment(line: string): string {
  let quote = "";
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if ((ch === '"' || ch === "'") && !quote) quote = ch;
    else if (ch === quote) quote = "";
    else if (ch === "#" && !quote && (i === 0 || /\s/.test(line[i - 1] ?? ""))) return line.slice(0, i).trimEnd();
  }
  return line.trimEnd();
}

function unquote(value: string): string {
  if (value.length >= 2 && value[0] === value[value.length - 1] && (value[0] === '"' || value[0] === "'")) {
    const inner = value.slice(1, -1);
    return value[0] === '"' ? inner.replace(/\\"/g, '"').replace(/\\\\/g, "\\") : inner;
  }
  return value;
}

/** Parse KEY=value lines: blank lines, comments, `export`, quotes and inline comments. */
export function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    let line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    if (line.startsWith("export ")) line = line.slice("export ".length);
    const eq = line.indexOf("=");
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    out[key] = value.startsWith('"') || value.startsWith("'") ? unquote(value) : stripComment(value);
  }
  return out;
}

/** Read `section.key` from demo.yaml (`section = ""` for a top-level key) without a YAML library. */
export function manifestValue(text: string, section: string, key: string): string | undefined {
  let current: string | undefined;
  for (const raw of text.split(/\r?\n/)) {
    const line = stripComment(raw);
    if (!line.trim()) continue;
    const nested = /^\s/.test(line);
    const m = line.match(/^\s*([A-Za-z_][\w-]*):\s*(.*)$/);
    if (!m) continue;
    const [, k = "", v = ""] = m;
    if (!nested) {
      current = k;
      if (section === "" && k === key) return unquote(v.trim()) || undefined;
    } else if (current === section && k === key) {
      return unquote(v.trim()) || undefined;
    }
  }
  return undefined;
}

/**
 * Make `path` the only source of LANGFUSE_*: drop shell exports, then apply the file.
 *
 * Same rule as src/env.ts (dotenv with override: true), made stricter: shell LANGFUSE_*
 * are dropped even when the file is missing, so a missing file fails the key check
 * instead of reading another project. Returns whether the file was loaded.
 */
export function loadEnv(path: string, env: NodeJS.ProcessEnv = process.env): boolean {
  for (const k of Object.keys(env)) if (k.startsWith("LANGFUSE_")) delete env[k];
  if (!existsSync(path)) return false;
  Object.assign(env, parseEnv(readFileSync(path, "utf8")));
  return true;
}

export function nodeMajor(version: string = process.versions.node): number {
  return Number.parseInt(version.split(".")[0] ?? "0", 10);
}

// --- Langfuse reads -----------------------------------------------------------------------

export function iso(t: Date): string {
  return t.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export class ApiError extends Error {
  constructor(
    readonly status: number | undefined,
    message: string,
  ) {
    super(status ? `HTTP ${status}: ${message}` : message);
  }
}

export type FetchLike = (url: string, init: { headers: Record<string, string>; signal?: AbortSignal }) => Promise<{
  ok: boolean;
  status: number;
  text(): Promise<string>;
}>;

type Params = Record<string, string | number | boolean | undefined>;

/** The few public-API calls this check needs. `fetchImpl` is injectable for the tests. */
export class Langfuse {
  readonly base: string;
  private readonly headers: Record<string, string>;

  constructor(
    baseUrl: string,
    publicKey: string,
    secretKey: string,
    private readonly fetchImpl: FetchLike = fetch as unknown as FetchLike,
  ) {
    this.base = baseUrl.replace(/\/+$/, "");
    this.headers = {
      Authorization: `Basic ${Buffer.from(`${publicKey}:${secretKey}`).toString("base64")}`,
      Accept: "application/json",
      "User-Agent": "demo-verify/1",
    };
  }

  async get<T = unknown>(path: string, params: Params = {}): Promise<T> {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined) qs.set(k, String(v));
    const url = this.base + path + (qs.size ? `?${qs.toString()}` : "");
    let res;
    try {
      res = await this.fetchImpl(url, { headers: this.headers, signal: AbortSignal.timeout(TIMEOUT_MS) });
    } catch (err) {
      const e = err as { name?: string; cause?: { code?: string }; message?: string };
      throw new ApiError(undefined, `${e.name ?? "Error"}: ${e.cause?.code ?? e.message ?? String(err)}`);
    }
    const body = await res.text();
    if (!res.ok) throw new ApiError(res.status, body.slice(0, 200) || "no body");
    try {
      return (body ? JSON.parse(body) : undefined) as T;
    } catch {
      throw new ApiError(undefined, `non-JSON response from ${path}`);
    }
  }

  /** `get` for src/readback.ts: the path carries its query string, and every non-2xx throws. */
  readonly getPath: Get = async <T>(pathWithQuery: string): Promise<T> => {
    const [path = "", qs = ""] = pathWithQuery.split("?", 2);
    return this.get<T>(path, Object.fromEntries(new URLSearchParams(qs)));
  };

  /** Follow `meta.cursor` (Observations v2, Scores v3, experiments), bounded, through empty pages. */
  cursor<T = unknown>(path: string, params: Params, opts: { limit?: number; cap?: number; maxPages?: number; sample?: boolean } = {}) {
    return cursorAll<T>(this.getPath, path, params, { maxPages: 20, ...opts });
  }

  /** Follow `meta.totalPages` (v1 observations, dataset items, legacy dataset runs). */
  pages<T = unknown>(path: string, params: Params, opts: { limit?: number; maxPages?: number } = {}) {
    return pagesAll<T>(this.getPath, path, params, { maxPages: 20, ...opts });
  }
}

/** Observations API v2 exists from server 4.x on (and on Cloud); a 3.x server needs the v1 list. */
export const apiFlavour = apiFlavourFor;

export type RawObservation = {
  id?: string;
  traceId?: string | null;
  type?: string;
  name?: string | null;
  startTime?: string;
  parentObservationId?: string | null;
  isRootObservation?: boolean;
  sessionId?: string | null;
  tags?: string[] | null;
  input?: unknown;
  metadata?: unknown;
};

export type Root = { traceId: string; name: string; sessionId?: string; tags: string[]; startTime: string };

export { sessionOf };

export function toRoot(o: RawObservation): Root | undefined {
  const isRoot = o.isRootObservation ?? o.parentObservationId == null;
  if (!isRoot || !o.traceId || !o.startTime) return undefined;
  return { traceId: o.traceId, name: o.name ?? "", sessionId: sessionOf(o), tags: o.tags ?? [], startTime: o.startTime };
}

/** Non-empty, accounting for Observations v2 handing io back as a JSON string. */
export function isPresent(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value !== "string") return true;
  const trimmed = value.trim();
  if (trimmed === "" || trimmed === "null") return false;
  try {
    return JSON.parse(trimmed) !== null;
  } catch {
    return true;
  }
}

export type Run = { sessionId?: string; turns: Root[]; latest: Root };

/** Group roots into the runs the acts open. Newest first; turns of a run share its session. */
export function classifyRoots(roots: Root[]): { good?: Run; collapsed?: Run; broken?: Run } {
  const newestFirst = [...roots].sort((a, b) => b.startTime.localeCompare(a.startTime));
  const latestSession = (prefix: string): Run | undefined => {
    const first = newestFirst.find((r) => r.sessionId?.startsWith(prefix) && r.name === TRACE_NAME);
    if (!first) return undefined;
    const turns = newestFirst.filter((r) => r.sessionId === first.sessionId && r.name === TRACE_NAME);
    return { sessionId: first.sessionId, turns, latest: first };
  };
  // The broken run has no sessionId (defect 5), so it is found by its tag, or, on a v3
  // server where roots carry no tags, by its message-derived name (defect 2).
  const broken = newestFirst.filter((r) =>
    r.tags.length ? r.tags.includes("compare:broken") : r.name.startsWith(BROKEN_NAME_PREFIX) && !r.sessionId,
  );
  const latestBroken = broken[0];
  return {
    good: latestSession(SESSION_PREFIX.good),
    collapsed: latestSession(SESSION_PREFIX.collapsed),
    // Without a session there is no run boundary, so `turns` is every broken turn in the window.
    broken: latestBroken ? { turns: broken, latest: latestBroken } : undefined,
  };
}

export type ItemRow = { id?: string; status?: string; input?: unknown; expectedOutput?: unknown };

/** Compare the dataset's items with the fixtures: what is missing, archived, or differs. */
export function itemProblems(rows: ItemRow[], expected: ItemDefinition[] = EXPECTED_ITEMS): string[] {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const problems: string[] = [];
  for (const want of expected) {
    const have = byId.get(want.id);
    if (!have) problems.push(`${want.id} missing`);
    else if ((have.status ?? "ACTIVE") !== "ACTIVE") problems.push(`${want.id} ${have.status}`);
    else if (canonical(have.input) !== canonical(want.input) || canonical(have.expectedOutput) !== canonical(want.expectedOutput))
      problems.push(`${want.id} differs from src/conversations.ts`);
  }
  return problems;
}

export type State = {
  projectId?: string;
  flavour?: ApiFlavour;
  datasetId?: string;
  experimentName?: string;
  good?: Run;
  collapsed?: Run;
  broken?: Run;
};

/** One (label, url) per act of DEMO_SCRIPT.md, in act order. */
export function actLinks(base: string, state: State): Array<[string, string]> {
  const p = `${base}/project/${state.projectId}`;
  const links: Array<[string, string]> = [[`Act 1 · Traces (tag compare:broken)`, `${p}/traces`]];
  if (state.broken) links.push(["Act 1 · a broken turn", `${p}/traces/${encodeURIComponent(state.broken.latest.traceId)}`]);
  if (state.good?.sessionId) links.push(["Act 1 · the good session", `${p}/sessions/${encodeURIComponent(state.good.sessionId)}`]);
  if (state.collapsed?.sessionId)
    links.push(["Act 2 · the collapsed session", `${p}/sessions/${encodeURIComponent(state.collapsed.sessionId)}`]);
  if (state.datasetId) links.push([`Act 3 · ${DATASET_NAME}`, `${p}/datasets/${state.datasetId}`]);
  if (state.good?.sessionId)
    links.push([`Act 4 · ${SESSION_SCORE}`, `${p}/sessions/${encodeURIComponent(state.good.sessionId)}`]);
  return links;
}

// --- Checks -------------------------------------------------------------------------------

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/**
 * Run one act's checks. A read that throws becomes a failed check for that act, with
 * the act's own weight: a FAIL for a required act, a WARN for an optional one. The
 * other acts still run, and every failure reaches the verdict.
 */
async function section(title: string, required: boolean, body: () => Promise<void>): Promise<void> {
  console.log(title);
  try {
    await body();
  } catch (e) {
    check(false, `read failed: ${msg(e)}`, { required });
  }
}

async function listRoots(lf: Langfuse, flavour: ApiFlavour, from: Date, to: Date): Promise<Root[]> {
  const window = { fromStartTime: iso(from), toStartTime: iso(to) };
  const raw =
    flavour === "v2"
      ? // `basic` carries name, sessionId and isRootObservation; `trace_context` the tags.
        await lf.cursor<RawObservation>(
          "/api/public/v2/observations",
          { ...window, isRootObservation: true, fields: "core,basic,trace_context" },
          { limit: 1000, cap: 5000, maxPages: 5, sample: true },
        )
      : // v1 has no root filter; roots are spans with no parent.
        await lf.pages<RawObservation>("/api/public/observations", { ...window, type: "SPAN" }, { maxPages: 20 });
  return raw.map(toRoot).filter((r): r is Root => r !== undefined);
}

async function traceObservations(lf: Langfuse, flavour: ApiFlavour, traceId: string): Promise<RawObservation[]> {
  return flavour === "v2"
    ? lf.cursor<RawObservation>("/api/public/v2/observations", { traceId, fields: "core,io" }, { limit: 1000 })
    : lf.pages<RawObservation>("/api/public/observations", { traceId });
}

async function datasetChecks(lf: Langfuse, state: State): Promise<void> {
  let dataset: { id?: string } | undefined;
  try {
    dataset = await lf.get<{ id?: string }>(`/api/public/v2/datasets/${encodeURIComponent(DATASET_NAME)}`);
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 404)) throw e;
    check(false, `dataset ${DATASET_NAME} not found`, { hint: `run ${SEED_CMD}` });
    return;
  }
  state.datasetId = dataset?.id;
  const rows = await lf.pages<ItemRow>("/api/public/dataset-items", { datasetName: DATASET_NAME });
  const problems = itemProblems(rows);
  check(
    problems.length === 0,
    `dataset ${DATASET_NAME}: ${EXPECTED_ITEMS.length - problems.length}/${EXPECTED_ITEMS.length} conversation items ` +
      `active and matching src/conversations.ts (${rows.length} in total)` +
      (problems.length ? `; ${problems.join("; ")}` : ""),
    { hint: `run ${SEED_CMD}` },
  );
}

type Experiment = { id: string; name: string; startTime: string; itemCount?: number };
type ScoreRow = { name?: string; subject?: { kind?: string; id?: string; traceId?: string | null } };
type RunEvidence = { name: string; itemIds: Set<string>; scores: number; scoredItems: number };

const EVALUATOR_SET = new Set<string>(EVALUATOR_NAMES);

/**
 * Evaluator scores on a 3.x dataset run, read from Scores v3 by the run items' own
 * trace ids. Each score counts only when its subject (the trace, or an observation in
 * it) is one of those traces, so a same-named score from another run never counts.
 */
async function runItemScores(lf: Langfuse, items: Array<{ datasetItemId?: string; traceId?: string }>): Promise<Map<string, number>> {
  const itemOfTrace = new Map(items.filter((i) => i.traceId && i.datasetItemId).map((i) => [i.traceId!, i.datasetItemId!]));
  const traceIds = [...itemOfTrace.keys()];
  const perItem = new Map<string, number>();
  for (let i = 0; i < traceIds.length; i += 50) {
    const scores = await lf.cursor<ScoreRow>(
      "/api/public/v3/scores",
      { traceId: traceIds.slice(i, i + 50).join(","), name: EVALUATOR_NAMES.join(","), fields: "subject" },
      { limit: 100, maxPages: 20 },
    );
    for (const s of scores) {
      const trace = s.subject?.kind === "trace" ? s.subject.id : s.subject?.kind === "observation" ? s.subject.traceId : undefined;
      const item = trace ? itemOfTrace.get(trace) : undefined;
      if (item && EVALUATOR_SET.has(String(s.name))) perItem.set(item, (perItem.get(item) ?? 0) + 1);
    }
  }
  return perItem;
}

/**
 * The newest dataset run, its distinct items, and the evaluator scores on those items.
 * Both API paths apply one rule: only scores named after a deterministic evaluator and
 * attached to one of the run's own items count.
 */
async function newestExperiment(lf: Langfuse, flavour: ApiFlavour, datasetId: string | undefined): Promise<RunEvidence | undefined> {
  let name: string;
  let itemIds: Set<string>;
  let perItem: Map<string, number>;
  if (flavour === "v1") {
    // A 3.x server has no experiments API; its dataset runs are the same objects.
    const enc = encodeURIComponent(DATASET_NAME);
    const runs = (await lf.pages<{ name?: string; createdAt?: string }>(`/api/public/datasets/${enc}/runs`, {}))
      .filter((r) => r.name?.startsWith(EXPERIMENT_PREFIX))
      .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
    const newest = runs[0]?.name;
    if (!newest) return undefined;
    const run = await lf.get<{ datasetRunItems?: Array<{ datasetItemId?: string; traceId?: string }> }>(
      `/api/public/datasets/${enc}/runs/${encodeURIComponent(newest)}`,
    );
    const runItems = run?.datasetRunItems ?? [];
    name = newest;
    itemIds = new Set(runItems.map((i) => i.datasetItemId ?? ""));
    perItem = await runItemScores(lf, runItems);
  } else {
    if (!datasetId) return undefined; // no dataset, so no run on it (the seeded-artifacts check says why)
    const experiments = (
      await lf.cursor<Experiment>(
        "/api/public/experiments",
        { datasetId, fromStartTime: EXPERIMENTS_SINCE, fields: "core" },
        { limit: 100 },
      )
    )
      .filter((e) => e.name.startsWith(EXPERIMENT_PREFIX))
      .sort((a, b) => b.startTime.localeCompare(a.startTime));
    const newest = experiments[0];
    if (!newest) return undefined;
    const since = new Date(Date.parse(newest.startTime) - 3_600_000);
    // `scores` holds each item's own item and trace scores.
    const items = await lf.cursor<{ experimentItemId?: string; scores?: ScoreRow[] | null }>(
      "/api/public/experiment-items",
      { experimentId: newest.id, fromStartTime: iso(since), fields: "core,scores" },
      { limit: 100 },
    );
    name = newest.name;
    itemIds = new Set(items.map((i) => i.experimentItemId ?? ""));
    perItem = new Map();
    for (const i of items) {
      const n = (i.scores ?? []).filter((s) => EVALUATOR_SET.has(String(s.name))).length;
      if (i.experimentItemId && n) perItem.set(i.experimentItemId, (perItem.get(i.experimentItemId) ?? 0) + n);
    }
  }
  const counted = [...perItem].filter(([id]) => itemIds.has(id));
  return { name, itemIds, scores: counted.reduce((n, [, c]) => n + c, 0), scoredItems: counted.length };
}

export async function langfuseChecks(
  lf: Langfuse,
  expectedProject: string | undefined,
  now = new Date(),
  opts: { quick?: boolean } = {},
): Promise<State> {
  const state: State = {};
  console.log("Langfuse");
  let projects: Array<{ id?: string; name?: string }>;
  try {
    projects = (await lf.get<{ data?: Array<{ id?: string; name?: string }> }>("/api/public/projects"))?.data ?? [];
  } catch (e) {
    check(false, `keys authenticate against ${lf.base}: ${msg(e)}`, {
      hint: "401: wrong keys or wrong LANGFUSE_BASE_URL; network error: is Langfuse up?",
    });
    return state;
  }
  const proj = projects[0] ?? {};
  state.projectId = proj.id;
  if (
    !check(Boolean(expectedProject) && proj.name === expectedProject, `keys map to project '${proj.name ?? "(none)"}' (want '${expectedProject}')`, {
      hint: "use this project's keys in .env, or set LANGFUSE_PROJECT_NAME",
    })
  ) {
    return state;
  }
  let version: string | undefined;
  try {
    version = (await lf.get<{ version?: string }>("/api/public/health"))?.version;
  } catch (e) {
    // Only the v3 fallback depends on it; Cloud and v4 serve Observations v2.
    check(false, `server version unknown (${msg(e)}); reading Observations API v2`, { required: false });
  }
  const flavour = (state.flavour = apiFlavour(version));
  if (version) {
    check(true, `Langfuse ${version}: reading Observations ${flavour === "v2" ? "API v2" : "v1 (a 3.x server has no v2)"}`);
  }

  await section("Seeded artifacts", true, () => datasetChecks(lf, state));

  let runs: ReturnType<typeof classifyRoots> = {};
  await section("Act 1 · trace shape (npm run compare)", true, async () => {
    const roots = await listRoots(lf, flavour, new Date(now.getTime() - LOOKBACK_DAYS * 86_400_000), new Date(now.getTime() + 300_000));
    runs = classifyRoots(roots);
    state.good = runs.good;
    state.broken = runs.broken;
    state.collapsed = runs.collapsed;

    const good = runs.good;
    if (
      check(Boolean(good), good ? `good session ${good.sessionId}: ${good.turns.length} turn(s)` : `a '${SESSION_PREFIX.good}*' session in the last ${LOOKBACK_DAYS} days`, {
        hint: `run ${PREP_CMD} (or npm run compare)`,
      }) &&
      good
    ) {
      const obs = await traceObservations(lf, flavour, good.latest.traceId);
      const gens = obs.filter((o) => o.type === "GENERATION").length;
      check(gens >= 1 && obs.length >= 2, `its latest turn has ${obs.length} observation(s), ${gens} generation(s) (want 2+ and 1+)`, {
        hint: "observations may still be landing: wait 30 s and re-run",
      });
      const ageH = (now.getTime() - Date.parse(good.latest.startTime)) / 3_600_000;
      check(ageH <= FRESH_HOURS, `the comparison is ${ageH.toFixed(1)} h old (fresh under ${FRESH_HOURS} h)`, {
        required: false,
        hint: `run ${PREP_CMD} the day before`,
      });
    }

    const broken = runs.broken;
    const withSession = broken ? broken.turns.filter((r) => r.sessionId).length : 0;
    if (
      check(
        Boolean(broken) && withSession === 0,
        broken
          ? `${broken.turns.length} broken turn(s) in the last ${LOOKBACK_DAYS} days, ${withSession} with a session (want 0: defect 5)`
          : `a compare:broken run in the last ${LOOKBACK_DAYS} days`,
        { hint: broken ? "broken mode is propagating sessionId: check src/assistant.ts" : `run ${PREP_CMD} (or npm run compare)` },
      ) &&
      broken
    ) {
      const obs = await traceObservations(lf, flavour, broken.latest.traceId);
      const gens = obs.filter((o) => o.type === "GENERATION");
      const empty = gens.filter((o) => !isPresent(o.input)).length;
      check(gens.length >= 1 && empty === gens.length, `its latest turn has ${gens.length} generation(s), ${empty} with null input (defect 1)`, {
        hint: gens.length ? "broken mode is recording inputs: check src/assistant.ts recordInputs" : "wait 30 s and re-run",
      });
    }
  });

  await section("Act 2 · the collapsed loop (optional, npm run compare:loop)", false, async () => {
    check(
      Boolean(runs.collapsed),
      runs.collapsed
        ? `collapsed session ${runs.collapsed.sessionId}: ${runs.collapsed.turns.length} turn(s)`
        : `a '${SESSION_PREFIX.collapsed}*' session in the last ${LOOKBACK_DAYS} days`,
      { required: false, hint: "run npm run compare:loop before presenting Act 2" },
    );
  });

  // Full prep (`npm run demo`) runs the experiment; quick prep skips it, and says so.
  const actThreeRequired = !opts.quick;
  await section(`Act 3 · evaluators (dataset run${opts.quick ? ", optional with --quick" : ""})`, actThreeRequired, async () => {
    const run = await newestExperiment(lf, flavour, state.datasetId);
    const covered = EXPECTED_ITEMS.filter((i) => run?.itemIds.has(i.id)).length;
    check(
      Boolean(run) && covered === EXPECTED_ITEMS.length && (run?.scores ?? 0) > 0,
      run
        ? `newest run ${run.name}: ${covered}/${EXPECTED_ITEMS.length} conversation items, ` +
            `${run.scores} evaluator score(s) on ${run.scoredItems} of them (want 1+)`
        : `a ${EXPERIMENT_PREFIX}* run on ${DATASET_NAME}`,
      {
        required: actThreeRequired,
        hint: opts.quick
          ? "quick prep skips it; run npm run experiment for the dataset-run fallback"
          : "run npm run experiment (npm run demo runs it; --quick skips it)",
      },
    );
  });

  await section("Act 4 · session score (optional, npm run evaluate:live)", false, async () => {
    const sessionId = runs.good?.sessionId;
    if (!sessionId) {
      check(false, `${SESSION_SCORE} on the good session (no good session found)`, { required: false, hint: `run ${PREP_CMD}` });
      return;
    }
    // Scores v3 returns its core fields always; `subject` is the only group needed.
    const scores = await lf.cursor<{ subject?: { kind?: string; id?: string } }>(
      "/api/public/v3/scores",
      { sessionId, name: SESSION_SCORE, fields: "subject" },
      { limit: 100, maxPages: 5 },
    );
    const onSession = scores.filter((s) => s.subject?.kind === "session" && s.subject.id === sessionId).length;
    check(onSession > 0, `${SESSION_SCORE} on session ${sessionId}: ${onSession} score(s)`, {
      required: false,
      hint: "run npm run evaluate:live after the comparison",
    });
  });
  return state;
}

export type MainDeps = { fetchImpl?: FetchLike; env?: NodeJS.ProcessEnv; demoDir?: string; now?: Date; nodeVersion?: string };

export const USAGE = `usage: npx tsx scripts/verify_demo.ts [--links] [--quick]
  --links   also print the link each act opens
  --quick   readiness for quick prep (npm run demo -- --quick): the Act 3 dataset run only warns`;

export async function main(argv: string[] = process.argv.slice(2), deps: MainDeps = {}): Promise<number> {
  if (argv.includes("--help") || argv.includes("-h")) {
    console.log(USAGE);
    return 0;
  }
  const unknown = argv.filter((a) => !["--links", "--quick"].includes(a));
  if (unknown.length) {
    console.log(`unknown option(s): ${unknown.join(" ")}\n${USAGE}`);
    return 2;
  }
  const links = argv.includes("--links");
  const quick = argv.includes("--quick");
  const env = deps.env ?? process.env;
  const demoDir = deps.demoDir ?? DEMO_DIR;
  RESULTS.length = 0;

  const manifestPath = resolve(demoDir, "demo.yaml");
  const manifest = existsSync(manifestPath) ? readFileSync(manifestPath, "utf8") : "";
  const envPath = resolve(demoDir, ".env");
  console.log("grocery-assistant readiness");
  console.log(
    quick
      ? "Scope: quick prep (npm run demo -- --quick); the Act 3 dataset run is optional"
      : "Scope: full prep (npm run demo); every core act is required",
  );
  console.log("Config");
  const shown = relative(REPO_ROOT, envPath).startsWith("..") ? envPath : relative(REPO_ROOT, envPath);
  const loaded = check(loadEnv(envPath, env), `env loaded from ${shown}; shell-exported LANGFUSE_* ignored`, {
    hint: "run ./scripts/provision-project.sh, or cp .env.example .env and fill it in",
  });
  const expected = env["DEMO_LANGFUSE_PROJECT"] || env["LANGFUSE_PROJECT_NAME"] || manifestValue(manifest, "langfuse", "project");
  const base = env["LANGFUSE_BASE_URL"] || DEFAULT_BASE_URL;
  const pk = env["LANGFUSE_PUBLIC_KEY"] ?? "";
  const sk = env["LANGFUSE_SECRET_KEY"] ?? "";
  const keysSet = check(Boolean(pk && sk), `LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY set (base URL ${base})`, {
    hint: "add both to .env",
  });
  check(Boolean(env["ANTHROPIC_API_KEY"]), "ANTHROPIC_API_KEY set (the acts run conversations live)", {
    hint: "add it to .env",
  });
  const major = nodeMajor(deps.nodeVersion);
  check(major >= MIN_NODE_MAJOR, `Node ${deps.nodeVersion ?? process.versions.node} (want ${MIN_NODE_MAJOR}+)`, {
    hint: "@langfuse/vercel-ai-sdk needs Node 22 or later",
  });
  check(existsSync(resolve(demoDir, "node_modules", "@langfuse", "vercel-ai-sdk")), "dependencies installed", {
    hint: "run npm ci",
  });

  // No API call without this demo's own .env and both keys.
  if (loaded && keysSet) {
    const lf = new Langfuse(base, pk, sk, deps.fetchImpl);
    const state = await langfuseChecks(lf, expected, deps.now, { quick });
    if (links && state.projectId) {
      console.log("\nLinks");
      for (const [label, url] of actLinks(lf.base, state)) console.log(`  ${label.padEnd(36)} ${url}`);
    }
  }

  const [line, code] = summary();
  console.log(`\n${line}`);
  return code;
}

const isEntrypoint = process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isEntrypoint) {
  process.exitCode = await main();
}
