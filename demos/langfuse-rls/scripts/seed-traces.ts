// Seed the 30 RLS fixtures into this demo's Langfuse project, idempotently.
//
//   npm run seed                       # create missing, repair drifted, leave the rest alone
//   npm run seed -- --prune-legacy     # also delete duplicates the old appending seeder left
//
// Each fixture has a stable trace id (`rls-demo-NN` in scripts/fixtures/traces.json), so a
// re-run upserts the same 30 traces instead of appending 30 more. The seeder reads the
// project's `rls-demo` traces first, and writes only what is missing or has drifted from the
// fixture; an unchanged run writes nothing. Every result is printed (created / updated /
// unchanged / failed) and any failure exits 1.
//
// Credentials come only from this demo's .env: inherited LANGFUSE_* are cleared first, and a
// missing file or value fails before any request. The keys must map to the expected project
// (LANGFUSE_PROJECT_NAME, else langfuse.project in demo.yaml) before anything is written.
//
// Reads and writes on the pinned self-hosted v3 server (3.221.1), which is this demo's only
// supported target: the traces carry no observations, and Observations v2 404s on v3, so the
// current state is read with `GET /api/public/traces?tags=rls-demo` (the same read the app
// makes in lib/langfuse-client.ts). Writes are `trace-create` events on `POST
// /api/public/ingestion`, whose 207 response lists per-event errors, so partial failures are
// reported instead of being swallowed by an SDK queue.
import { randomUUID } from "node:crypto";
import { relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { type Env, ENV_PATH, expectedProject, langfuseSettings, loadEnv, readManifest, REPO_ROOT } from "./demo_env";
import { DEMO_TAG, type Fixture, fixtureDrift, loadFixtures, type TraceLike } from "./fixtures";
import { ApiError, type Fetch, LangfuseHttp } from "./langfuse_http";

export const PAGE_LIMIT = 100;
export const MAX_PAGES = 50;
const DELETE_CHUNK = 1000;

// --- Reading the current state ------------------------------------------------------------

interface TracesPage {
  data?: TraceLike[];
  meta?: { page?: number; totalPages?: number };
}

/**
 * Every trace in the project tagged `rls-demo`, following page numbers until `totalPages`.
 * Bounded to MAX_PAGES, and it fails rather than loops if a page repeats ids already seen.
 */
export async function listTaggedTraces(lf: LangfuseHttp): Promise<TraceLike[]> {
  const out: TraceLike[] = [];
  const seen = new Set<string>();
  for (let page = 1; page <= MAX_PAGES; page++) {
    const d = ((await lf.get("/api/public/traces", { tags: DEMO_TAG, page, limit: PAGE_LIMIT })) ?? {}) as TracesPage;
    const rows = d.data ?? [];
    const fresh = rows.filter((t) => !seen.has(t.id));
    if (rows.length && !fresh.length) throw new ApiError(undefined, `trace list repeated page ${page}; stopping`);
    for (const t of fresh) seen.add(t.id);
    out.push(...fresh);
    const total = d.meta?.totalPages ?? 0;
    if (!rows.length || page >= total) return out;
  }
  throw new ApiError(undefined, `trace list still had pages after ${MAX_PAGES} x ${PAGE_LIMIT}`);
}

// --- Planning -----------------------------------------------------------------------------

export interface Plan {
  create: Fixture[];
  update: Array<{ fixture: Fixture; existing: TraceLike; fields: string[] }>;
  unchanged: Fixture[];
  /** Tagged traces with a fixture's name but not its stable id: copies the old seeder appended. */
  legacy: TraceLike[];
  /** Tagged traces that are not fixtures at all. Never touched. */
  foreign: TraceLike[];
}

export function plan(fixtures: Fixture[], existing: TraceLike[]): Plan {
  const byId = new Map(existing.map((t) => [t.id, t]));
  const ids = new Set(fixtures.map((f) => f.id));
  const names = new Set(fixtures.map((f) => f.name));
  const p: Plan = { create: [], update: [], unchanged: [], legacy: [], foreign: [] };
  for (const f of fixtures) {
    const t = byId.get(f.id);
    if (!t) p.create.push(f);
    else {
      const fields = fixtureDrift(f, t);
      if (fields.length) p.update.push({ fixture: f, existing: t, fields });
      else p.unchanged.push(f);
    }
  }
  for (const t of existing) {
    if (ids.has(t.id)) continue;
    (t.name && names.has(t.name) ? p.legacy : p.foreign).push(t);
  }
  return p;
}

// --- Writing ------------------------------------------------------------------------------

export interface IngestionEvent {
  id: string;
  type: "trace-create";
  timestamp: string;
  body: Fixture & { timestamp: string };
}

/**
 * One trace-create event. An update keeps the trace's existing timestamp: the v3 traces table
 * is ordered by the trace's date, so moving it would leave a second row behind.
 */
export function traceEvent(fixture: Fixture, now: Date, existingTimestamp?: string, eventId: string = randomUUID()): IngestionEvent {
  const { id, name, userId, tags, metadata, input, output } = fixture;
  return {
    id: eventId,
    type: "trace-create",
    timestamp: now.toISOString(),
    body: { id, timestamp: existingTimestamp ?? now.toISOString(), name, userId, tags, metadata, input, output },
  };
}

interface IngestionResponse {
  successes?: Array<{ id: string; status?: number }>;
  errors?: Array<{ id: string; status?: number; message?: string; error?: unknown }>;
}

/** Send the events; returns the error message per trace id for every event not acknowledged. */
export async function ingest(lf: LangfuseHttp, events: IngestionEvent[]): Promise<Map<string, string>> {
  const failed = new Map<string, string>();
  if (!events.length) return failed;
  const traceOf = new Map(events.map((e) => [e.id, e.body.id]));
  let resp: IngestionResponse;
  try {
    resp = ((await lf.request("POST", "/api/public/ingestion", { body: { batch: events } })) ?? {}) as IngestionResponse;
  } catch (e) {
    for (const ev of events) failed.set(ev.body.id, (e as Error).message);
    return failed;
  }
  const ok = new Set((resp.successes ?? []).map((s) => s.id));
  for (const err of resp.errors ?? []) {
    const traceId = traceOf.get(err.id);
    if (traceId) failed.set(traceId, `${err.status ?? "?"} ${err.message ?? JSON.stringify(err.error ?? "")}`.trim());
  }
  for (const ev of events) {
    if (!ok.has(ev.id) && !failed.has(ev.body.id)) failed.set(ev.body.id, "not acknowledged by the ingestion API");
  }
  return failed;
}

export async function deleteTraces(lf: LangfuseHttp, ids: string[]): Promise<void> {
  for (let i = 0; i < ids.length; i += DELETE_CHUNK) {
    await lf.request("DELETE", "/api/public/traces", { body: { traceIds: ids.slice(i, i + DELETE_CHUNK) } });
  }
}

// --- Main ---------------------------------------------------------------------------------

export interface SeedOptions {
  argv?: string[];
  env?: Env;
  envPath?: string;
  manifest?: string;
  fixtures?: Fixture[];
  fetch?: Fetch;
  out?: (line: string) => void;
  now?: Date;
}

export async function seed(opts: SeedOptions = {}): Promise<number> {
  const out = opts.out ?? console.log;
  const env = opts.env ?? process.env;
  const envPath = opts.envPath ?? ENV_PATH;
  const prune = (opts.argv ?? []).includes("--prune-legacy");
  const now = opts.now ?? new Date();
  const shown = envPath.startsWith(REPO_ROOT) ? relative(REPO_ROOT, envPath) : envPath;

  // 1. Configuration: fails before any request.
  if (!loadEnv(envPath, env)) {
    out(`FAILED: ${shown} not found. cp .env.example .env and fill in this project's keys; shell-exported LANGFUSE_* are ignored.`);
    return 1;
  }
  const { settings, missing } = langfuseSettings(env);
  if (!settings) {
    out(`FAILED: ${shown} does not set ${missing.join(", ")}. Shell-exported LANGFUSE_* are ignored.`);
    return 1;
  }
  let fixtures: Fixture[];
  try {
    fixtures = opts.fixtures ?? loadFixtures();
  } catch (e) {
    out(`FAILED: ${(e as Error).message}`);
    return 1;
  }
  const want = expectedProject(env, opts.manifest ?? readManifest());
  const lf = new LangfuseHttp(settings, opts.fetch);

  // 2. The keys must belong to the demo's project before anything is written.
  let existing: TraceLike[];
  try {
    const proj = await lf.project();
    if (!want || proj.name !== want) {
      out(`FAILED: the keys map to project '${proj.name}', want '${want}'. Nothing was written.`);
      return 1;
    }
    out(`Seeding ${fixtures.length} fixtures into project '${proj.name}' at ${lf.base}`);
    existing = await listTaggedTraces(lf);
  } catch (e) {
    out(`FAILED: reading Langfuse: ${(e as Error).message}. Nothing was written.`);
    return 1;
  }

  // 3. Write only what is missing or has drifted.
  const p = plan(fixtures, existing);
  const events = [
    ...p.create.map((f) => traceEvent(f, now)),
    ...p.update.map((u) => traceEvent(u.fixture, now, u.existing.timestamp)),
  ];
  const failed = await ingest(lf, events);
  const changed = new Map(p.update.map((u) => [u.fixture.id, u.fields]));
  const created = new Set(p.create.map((f) => f.id));
  for (const f of fixtures) {
    const err = failed.get(f.id);
    if (err) out(`  FAILED     ${f.id}  ${f.name}: ${err}`);
    else if (created.has(f.id)) out(`  created    ${f.id}  ${f.name}`);
    else if (changed.has(f.id)) out(`  updated    ${f.id}  ${f.name} (${changed.get(f.id)!.join(", ")})`);
    else out(`  unchanged  ${f.id}  ${f.name}`);
  }

  // 4. Old-seeder duplicates: reported, and deleted only when asked. Other tagged traces are kept.
  let legacyLeft = p.legacy.length;
  if (p.legacy.length && prune) {
    try {
      await deleteTraces(lf, p.legacy.map((t) => t.id));
      for (const t of p.legacy) out(`  deleted    ${t.id}  ${t.name} (old-seeder duplicate)`);
      legacyLeft = 0;
    } catch (e) {
      out(`  FAILED     deleting ${p.legacy.length} old-seeder duplicates: ${(e as Error).message}`);
    }
  } else {
    for (const t of p.legacy) out(`  legacy     ${t.id}  ${t.name} (old-seeder duplicate, kept)`);
  }
  for (const t of p.foreign) out(`  kept       ${t.id}  ${t.name ?? ""} (tagged ${DEMO_TAG} but not a fixture; left alone)`);

  const nFailed = failed.size;
  out(`\nDone: ${p.create.length - countIn(failed, p.create)} created, ${p.update.length - countIn(failed, p.update.map((u) => u.fixture))} updated, ` +
    `${p.unchanged.length} unchanged, ${nFailed} failed` +
    (p.legacy.length ? `; ${p.legacy.length} old-seeder duplicate(s) ${legacyLeft ? "kept" : "deleted"}` : "") + ".");
  if (legacyLeft) out("The app reads every trace in the project, so the duplicates break the 30 / 20 / 10 split. Run: npm run seed -- --prune-legacy");
  if (events.length || (prune && p.legacy.length)) out("Ingestion and deletion are asynchronous: allow a few seconds, then bash scripts/verify.sh.");
  return nFailed || legacyLeft ? 1 : 0;
}

function countIn(failed: Map<string, string>, fixtures: Fixture[]): number {
  return fixtures.filter((f) => failed.has(f.id)).length;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  seed({ argv: process.argv.slice(2) })
    .then((code) => process.exit(code))
    .catch((err) => {
      console.error("FAILED:", err);
      process.exit(1);
    });
}
