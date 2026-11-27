// Readiness check for langfuse-rls: reads Langfuse and the running app back, says READY or NOT READY.
//
//   bash scripts/verify.sh            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
//   bash scripts/verify.sh --links    # also print the link each act opens
//
// An HTTP 200 proves little on its own, so every check counts real rows: the traces in the
// Langfuse project, the 30 fixtures by their stable ids and content, and the traces each
// persona sees through the app's policy engine. Node built-ins only (fetch, fs), so it needs
// nothing beyond `tsx`. Read-only: no model calls, no writes.
//
// Why there is no observation or score check: the seeded traces are trace-create events with
// no observations and no scores. The app reads them with `GET /api/public/traces`
// (lib/langfuse-client.ts), which only the pinned self-hosted v3 server serves. The trace
// count here uses Metrics API v1, the read CLAUDE.md names for self-hosted v3 code;
// Observations v2 and Metrics v2 404 on that server. Fixture identity and content are read
// through the app's own route, so this script makes no legacy trace reads.
import { relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { PERSONAS } from "../lib/personas";
import { evaluate } from "../lib/rls-policy";
import type { LangfuseTrace, Subject, TracesApiResponse } from "../lib/types";
import { type Env, ENV_PATH, expectedProject, langfuseSettings, loadEnv, readManifest, REPO_ROOT } from "./demo_env";
import { type Fixture, fixtureDrift, loadFixtures } from "./fixtures";
import { ApiError, type Fetch, LangfuseHttp, requestJson, TIMEOUT_MS } from "./langfuse_http";

// --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with scripts/seed-traces.ts.
export const DEFAULT_APP_URL = "http://localhost:3000";
const SEED_CMD = "npm run seed";
const RUN_CMD = "npm run dev";

// --- Result helpers -----------------------------------------------------------------------

export type Status = "PASS" | "FAIL" | "WARN";
export interface Result {
  status: Status;
  required: boolean;
  what: string;
}

export class Checker {
  readonly results: Result[] = [];
  constructor(private readonly out: (line: string) => void = console.log) {}

  /** Record one check and print its line. A failed optional check is a WARN, not a FAIL. */
  check(ok: boolean, what: string, opts: { required?: boolean; hint?: string } = {}): boolean {
    const required = opts.required ?? true;
    const status: Status = ok ? "PASS" : required ? "FAIL" : "WARN";
    this.results.push({ status, required, what });
    this.out(`  ${status}  ${what}` + (opts.hint && !ok ? `  ->  ${opts.hint}` : ""));
    return ok;
  }

  print(line: string): void {
    this.out(line);
  }
}

/** The closing line and the exit code: 0 when no required check failed, 1 otherwise. */
export function summary(results: Result[]): [string, number] {
  const passed = results.filter((r) => r.status === "PASS").length;
  const failed = results.filter((r) => r.status === "FAIL").length;
  const warned = results.filter((r) => r.status === "WARN").length;
  let detail = `${passed}/${results.length} checks passed`;
  if (failed) detail += `, ${failed} required failed`;
  if (warned) detail += `, ${warned} warning${warned !== 1 ? "s" : ""}`;
  return failed ? [`NOT READY (${detail})`, 1] : [`READY (${detail})`, 0];
}

// --- What the seeded data should produce --------------------------------------------------

/** How many fixture traces each persona must see, computed with the app's own policy engine. */
export function expectedVisible(fixtures: Fixture[], personas: Subject[] = PERSONAS): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of personas) out[p.id] = fixtures.filter((t) => evaluate(p, t as unknown as LangfuseTrace).allow).length;
  return out;
}

/** The one number in a Metrics API response (`count_count` comes back as a string or a number). */
export function metricCount(resp: unknown): number | undefined {
  const row = (resp as { data?: Array<Record<string, unknown>> } | null)?.data?.[0];
  if (!row) return 0; // no rows: nothing matched
  const v = row.count_count ?? Object.values(row).find((x) => !Number.isNaN(Number(x)));
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

export function traceCountQuery(now: Date): string {
  return JSON.stringify({
    view: "traces",
    metrics: [{ measure: "count", aggregation: "count" }],
    dimensions: [],
    filters: [],
    fromTimestamp: "2020-01-01T00:00:00Z",
    toTimestamp: new Date(now.getTime() + 5 * 60_000).toISOString(),
  });
}

// --- Checks -------------------------------------------------------------------------------

export interface RunOptions {
  argv?: string[];
  env?: Env;
  envPath?: string;
  manifest?: string;
  fixtures?: Fixture[];
  fetch?: Fetch;
  out?: (line: string) => void;
  now?: Date;
}

interface State {
  projectId?: string;
}

async function langfuseChecks(c: Checker, lf: LangfuseHttp, want: string | undefined,
  fixtureCount: number, now: Date, state: State): Promise<void> {
  c.print("Langfuse");
  let proj: { id?: string; name?: string };
  try {
    proj = await lf.project();
  } catch (e) {
    c.check(false, `keys authenticate against ${lf.base}: ${(e as Error).message}`,
      { hint: "401: wrong keys or LANGFUSE_BASE_URL; network error: is the langfuse profile up?" });
    return;
  }
  state.projectId = proj.id;
  if (!c.check(Boolean(want) && proj.name === want, `keys map to project '${proj.name ?? ""}' (want '${want ?? ""}')`,
    { hint: "use this project's keys in .env, or set LANGFUSE_PROJECT_NAME to its name" })) return;

  try {
    const n = metricCount(await lf.get("/api/public/metrics", { query: traceCountQuery(now) }));
    const hint = n !== undefined && n > fixtureCount
      ? `the app reads every trace in the project: remove the extras (old-seeder duplicates: ${SEED_CMD} -- --prune-legacy)`
      : `run ${SEED_CMD}; traces take a few seconds to land`;
    c.check(n === fixtureCount, `project holds ${n ?? "?"} trace(s) (want ${fixtureCount}, one per fixture)`, { hint });
  } catch (e) {
    const err = e as ApiError;
    c.check(false, `trace count read failed: ${err.message}`,
      { hint: err.status === 404 ? "Metrics v1 is missing: this demo needs the self-hosted v3 server" : "is Langfuse healthy?" });
  }
}

async function pageCheck(c: Checker, f: Fetch, url: string, what: string): Promise<boolean> {
  try {
    const r = await f(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    return c.check(r.ok, `${what} at ${url} (${r.status})`, { hint: "check the app's terminal for errors" });
  } catch (e) {
    const err = e as Error & { cause?: { code?: string } };
    return c.check(false, `${what} not reachable at ${url}: ${err.cause?.code ?? err.name}`,
      { hint: `run ${RUN_CMD} in demos/langfuse-rls` });
  }
}

/** The fixtures missing from, or drifted in, a persona's visible traces. */
export function fixtureProblems(fixtures: Fixture[], visible: Array<{ id: string }>): { missing: string[]; drifted: string[] } {
  const byId = new Map(visible.map((t) => [t.id, t]));
  const missing: string[] = [];
  const drifted: string[] = [];
  for (const fx of fixtures) {
    const t = byId.get(fx.id);
    if (!t) missing.push(fx.id);
    else if (fixtureDrift(fx, t).length) drifted.push(fx.id);
  }
  return { missing, drifted };
}

function list(ids: string[]): string {
  return ids.length > 4 ? `${ids.slice(0, 4).join(", ")} and ${ids.length - 4} more` : ids.join(", ");
}

async function appChecks(c: Checker, f: Fetch, appUrl: string, fixtures: Fixture[]): Promise<void> {
  c.print("App");
  try {
    const personas = (await requestJson(f, `${appUrl}/api/personas`)) as Array<{ id?: string }> | null;
    const ids = (personas ?? []).map((p) => p.id).join(", ");
    if (!c.check(ids === PERSONAS.map((p) => p.id).join(", "), `app up at ${appUrl}, personas: ${ids || "none"}`,
      { hint: "a different app is on this port, or the app is broken: check DEMO_APP_URL and the app's terminal" })) return;
  } catch (e) {
    c.check(false, `app not reachable at ${appUrl}: ${(e as Error).message}`, { hint: `run ${RUN_CMD} in demos/langfuse-rls` });
    return;
  }
  await pageCheck(c, f, `${appUrl}/policies`, "Act 2 page");
  await pageCheck(c, f, `${appUrl}/design`, "Act 3 page");

  c.print("Personas (through the app's policy engine)");
  const expected = expectedVisible(fixtures);
  const n = fixtures.length;
  let full: TracesApiResponse | undefined;
  for (const p of PERSONAS) {
    const want = expected[p.id];
    try {
      const resp = (await requestJson(f, `${appUrl}/api/traces?persona=${encodeURIComponent(p.id)}`)) as TracesApiResponse;
      const visible = resp?.visible?.length ?? 0;
      const total = visible + (resp?.denied?.count ?? 0);
      const ok = !resp?.error && visible === want && total === n;
      const hint = resp?.error
        ? `${resp.error} Check the keys the app was started with (shell exports beat .env in next dev)`
        : total !== n
          ? `app sees ${total} traces, want ${n}; it caches for 60 s, so wait and re-run after a reseed`
          : "the policy engine disagrees with the fixtures: run npm test";
      c.check(ok, `${p.name} (${p.clearance}) sees ${visible}/${total} (want ${want}/${n})`, { hint });
      if (want === n && !resp?.error) full = resp;
    } catch (e) {
      c.check(false, `${p.name}: ${(e as Error).message}`, { hint: "check the app's terminal for errors" });
    }
  }

  c.print("Fixtures");
  if (!full) {
    c.check(false, "fixture ids and content (no persona view with every trace to read them from)",
      { hint: "fix the persona checks above first" });
    return;
  }
  const { missing, drifted } = fixtureProblems(fixtures, full.visible ?? []);
  c.check(!missing.length && !drifted.length,
    `all ${n} fixtures present under their stable ids with current content` +
      (missing.length ? `; missing ${list(missing)}` : "") + (drifted.length ? `; drifted ${list(drifted)}` : ""),
    { hint: `run ${SEED_CMD} (repairs drift; keeps everything else)` });
}

export function actLinks(base: string, appUrl: string, state: State): Array<[string, string]> {
  return [
    ["Opening · every member sees all", `${base}/project/${state.projectId}/traces`],
    ["Act 1 · persona switch", `${appUrl}/`],
    ["Act 2 · the policy model", `${appUrl}/policies`],
    ["Act 3 · design and gaps", `${appUrl}/design`],
  ];
}

async function checks(c: Checker, opts: RunOptions): Promise<void> {
  const env = opts.env ?? process.env;
  const f = opts.fetch ?? fetch;
  const now = opts.now ?? new Date();
  const envPath = opts.envPath ?? ENV_PATH;
  const state: State = {};

  c.print("Config");
  const shown = envPath.startsWith(REPO_ROOT) ? relative(REPO_ROOT, envPath) : envPath;
  const loaded = c.check(loadEnv(envPath, env), `env loaded from ${shown}; shell-exported LANGFUSE_* ignored`,
    { hint: "cp .env.example .env and fill it in" });
  const { settings, missing } = langfuseSettings(env);
  const configured = loaded && c.check(Boolean(settings),
    settings ? "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set" : `.env does not set ${missing.join(", ")}`,
    { hint: "fill them in; shell exports are ignored" });
  let fixtures: Fixture[] = [];
  let fixturesOk = true;
  try {
    fixtures = opts.fixtures ?? loadFixtures();
    c.check(true, `${fixtures.length} fixtures load with stable ids`);
  } catch (e) {
    fixturesOk = c.check(false, `fixtures: ${(e as Error).message}`, { hint: "fix scripts/fixtures/traces.json" });
  }
  if (!configured || !fixturesOk) return; // configuration failures stop before any request

  const lf = new LangfuseHttp(settings!, f);
  await langfuseChecks(c, lf, expectedProject(env, opts.manifest ?? readManifest()), fixtures.length, now, state);
  const appUrl = (env.DEMO_APP_URL || DEFAULT_APP_URL).replace(/\/+$/, "");
  await appChecks(c, f, appUrl, fixtures);

  if ((opts.argv ?? []).includes("--links") && state.projectId) {
    c.print("\nLinks");
    for (const [label, url] of actLinks(lf.base, appUrl, state)) c.print(`  ${label.padEnd(34)} ${url}`);
  }
}

/** Run every check and print the verdict. Any unexpected error is a FAIL, never a missing verdict. */
export async function run(opts: RunOptions = {}): Promise<number> {
  const c = new Checker(opts.out);
  c.print("langfuse-rls readiness");
  try {
    await checks(c, opts);
  } catch (e) {
    c.check(false, `unexpected error: ${(e as Error)?.message ?? String(e)}`);
  }
  const [line, code] = summary(c.results);
  c.print(`\n${line}`);
  return code;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  run({ argv: process.argv.slice(2) }).then((code) => process.exit(code), () => process.exit(1));
}
