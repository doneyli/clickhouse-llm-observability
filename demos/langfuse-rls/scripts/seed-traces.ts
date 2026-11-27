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
// The current state is read through the same path the app uses (lib/trace-read.ts):
// Observations API v2 on a v4 server, the legacy trace list only when v2 answers 404 (the
// self-hosted v3 server). v4 has no trace-level input, output or metadata, so every fixture is
// written as a trace plus one root observation, `rls-demo-NN-root`, that carries the fixture's
// input, output and RLS metadata; a trace without it is drift ("root") and is repaired. Writes
// go to `POST /api/public/ingestion`, whose 207 response lists per-event errors, so partial
// failures are reported instead of being swallowed by an SDK queue.
import { randomUUID } from "node:crypto";
import { relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import type { ReadSource } from "../lib/trace-read";
import { type Env, ENV_PATH, expectedProject, langfuseSettings, loadEnv, readManifest, REPO_ROOT } from "./demo_env";
import { DEMO_TAG, type Fixture, fixtureDrift, loadFixtures, rootObservationId, type TraceLike } from "./fixtures";
import { type Fetch, LangfuseHttp } from "./langfuse_http";

export { rootObservationId };

const DELETE_CHUNK = 1000;

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

/**
 * `checkRoot` (Observations v2 reads) also requires each fixture's root observation: without
 * it a v4 server has nowhere to keep the input, output and metadata the app reads.
 */
export function plan(fixtures: Fixture[], existing: TraceLike[], opts: { checkRoot?: boolean } = {}): Plan {
  const byId = new Map(existing.map((t) => [t.id, t]));
  const ids = new Set(fixtures.map((f) => f.id));
  const names = new Set(fixtures.map((f) => f.name));
  const p: Plan = { create: [], update: [], unchanged: [], legacy: [], foreign: [] };
  for (const f of fixtures) {
    const t = byId.get(f.id);
    if (!t) p.create.push(f);
    else {
      const fields = fixtureDrift(f, t);
      if (opts.checkRoot && t.rootObservationId !== rootObservationId(f.id)) fields.push("root");
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

export interface TraceEvent {
  id: string;
  type: "trace-create";
  timestamp: string;
  body: Fixture & { timestamp: string };
}

export interface RootEvent {
  id: string;
  type: "span-create";
  timestamp: string;
  body: {
    id: string;
    traceId: string;
    name: string;
    startTime: string;
    endTime: string;
    input: Fixture["input"];
    output: Fixture["output"];
    metadata: Fixture["metadata"];
  };
}

export type IngestionEvent = TraceEvent | RootEvent;

/**
 * One trace-create event. An update keeps the trace's existing timestamp: the v3 traces table
 * is ordered by the trace's date, so moving it would leave a second row behind.
 */
export function traceEvent(fixture: Fixture, now: Date, existingTimestamp?: string, eventId: string = randomUUID()): TraceEvent {
  const { id, name, userId, tags, metadata, input, output } = fixture;
  return {
    id: eventId,
    type: "trace-create",
    timestamp: now.toISOString(),
    body: { id, timestamp: existingTimestamp ?? now.toISOString(), name, userId, tags, metadata, input, output },
  };
}

/**
 * The fixture's root observation, at the trace's timestamp, carrying what v4 reads from the
 * root: input, output and the RLS metadata. Its id is stable, so a repair upserts it.
 */
export function rootEvent(fixture: Fixture, now: Date, existingTimestamp?: string, eventId: string = randomUUID()): RootEvent {
  const at = existingTimestamp ?? now.toISOString();
  return {
    id: eventId,
    type: "span-create",
    timestamp: now.toISOString(),
    body: {
      id: rootObservationId(fixture.id),
      traceId: fixture.id,
      name: fixture.name,
      startTime: at,
      endTime: at,
      input: fixture.input,
      output: fixture.output,
      metadata: fixture.metadata,
    },
  };
}

/** The trace and root-observation events that write one fixture. */
export function fixtureEvents(fixture: Fixture, now: Date, existingTimestamp?: string): IngestionEvent[] {
  return [traceEvent(fixture, now, existingTimestamp), rootEvent(fixture, now, existingTimestamp)];
}

function fixtureIdOf(ev: IngestionEvent): string {
  return ev.type === "trace-create" ? ev.body.id : ev.body.traceId;
}

interface IngestionResponse {
  successes?: Array<{ id: string; status?: number }>;
  errors?: Array<{ id: string; status?: number; message?: string; error?: unknown }>;
}

/** Send the events; returns the error message per fixture id for every event not acknowledged. */
export async function ingest(lf: LangfuseHttp, events: IngestionEvent[]): Promise<Map<string, string>> {
  const failed = new Map<string, string>();
  if (!events.length) return failed;
  const traceOf = new Map(events.map((e) => [e.id, fixtureIdOf(e)]));
  let resp: IngestionResponse;
  try {
    resp = ((await lf.request("POST", "/api/public/ingestion", { body: { batch: events } })) ?? {}) as IngestionResponse;
  } catch (e) {
    for (const ev of events) failed.set(fixtureIdOf(ev), (e as Error).message);
    return failed;
  }
  const ok = new Set((resp.successes ?? []).map((s) => s.id));
  for (const err of resp.errors ?? []) {
    const traceId = traceOf.get(err.id);
    if (traceId && !failed.has(traceId)) failed.set(traceId, `${err.status ?? "?"} ${err.message ?? JSON.stringify(err.error ?? "")}`.trim());
  }
  for (const ev of events) {
    if (!ok.has(ev.id) && !failed.has(fixtureIdOf(ev))) failed.set(fixtureIdOf(ev), "not acknowledged by the ingestion API");
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
  let source: ReadSource;
  try {
    const proj = await lf.project();
    if (!want || proj.name !== want) {
      out(`FAILED: the keys map to project '${proj.name}', want '${want}'. Nothing was written.`);
      return 1;
    }
    out(`Seeding ${fixtures.length} fixtures into project '${proj.name}' at ${lf.base}`);
    const read = await lf.traces({ tags: [DEMO_TAG], now });
    existing = read.traces;
    source = read.source;
    if (source === "legacy-traces") out("Observations v2 returned 404 (self-hosted v3 server): reading the legacy trace list.");
  } catch (e) {
    out(`FAILED: reading Langfuse: ${(e as Error).message}. Nothing was written.`);
    return 1;
  }

  // 3. Write only what is missing or has drifted.
  const p = plan(fixtures, existing, { checkRoot: source === "observations-v2" });
  const events = [
    ...p.create.flatMap((f) => fixtureEvents(f, now)),
    ...p.update.flatMap((u) => fixtureEvents(u.fixture, now, u.existing.timestamp)),
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
  if (events.length || (prune && p.legacy.length)) {
    out(source === "observations-v2"
      ? "Ingestion is asynchronous, and a v4 server can take up to 15 minutes to show batch-ingested events in Observations v2:"
        + " wait before bash scripts/verify.sh, and before re-running the seeder."
      : "Ingestion and deletion are asynchronous: allow a few seconds, then bash scripts/verify.sh.");
  }
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
