// Readiness check for langfuse-rls: reads Langfuse and the running app back, says READY or NOT READY.
//
//   bash scripts/verify.sh            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
//   bash scripts/verify.sh --links    # also print the link each act opens
//
// An HTTP 200 proves little on its own, so every check counts real rows: the traces in the
// Langfuse project, and the traces each persona sees through the app's policy engine. Node
// built-ins only (fetch, fs), so it needs nothing beyond `tsx`. No model calls, no writes.
//
// Why there is no observation or score check: `npm run seed` sends trace-create events
// through the legacy JS SDK, so the seeded traces have no observations and no scores. The
// app reads them with `GET /api/public/traces` (lib/langfuse-client.ts), which the
// self-hosted v3 server serves. The trace count here uses Metrics API v1, the read
// CLAUDE.md names for self-hosted v3 code; Observations v2 and Metrics v2 404 on that server.
import { existsSync, readFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { PERSONAS } from "../lib/personas";
import { evaluate } from "../lib/rls-policy";
import type { LangfuseTrace, Subject, TracesApiResponse } from "../lib/types";

const HERE = dirname(fileURLToPath(import.meta.url));
export const DEMO_DIR = resolve(HERE, "..");
export const REPO_ROOT = resolve(DEMO_DIR, "..", "..");

// --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with scripts/seed-traces.ts.
export const FIXTURES_PATH = resolve(DEMO_DIR, "scripts/fixtures/traces.json");
export const DEFAULT_APP_URL = "http://localhost:3000";
const SEED_CMD = "npm run seed";
const RUN_CMD = "npm run dev";
const TIMEOUT_MS = 10_000;

// --- Result helpers -----------------------------------------------------------------------

export type Env = Record<string, string | undefined>;
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

// --- Environment and manifest -------------------------------------------------------------

function stripComment(value: string): string {
  for (let i = 0; i < value.length; i++) {
    if (value[i] === "#" && (i === 0 || /\s/.test(value[i - 1]))) return value.slice(0, i).trimEnd();
  }
  return value.trimEnd();
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
    const q = value[0];
    if ((q === '"' || q === "'") && value.lastIndexOf(q) > 0) {
      out[key] = value.slice(1, value.lastIndexOf(q));
    } else {
      out[key] = stripComment(value);
    }
  }
  return out;
}

/**
 * Make `path` the only source of LANGFUSE_*: drop shell exports, then apply the file.
 *
 * A shell-exported key for another project would otherwise make these reads look at a
 * different project than the one the demo seeded. Shell LANGFUSE_* are dropped even when the
 * file is missing, so a missing file fails the key check. Returns whether the file was loaded.
 */
export function loadEnv(path: string, env: Env = process.env): boolean {
  for (const k of Object.keys(env)) if (k.startsWith("LANGFUSE_")) delete env[k];
  if (!existsSync(path)) return false;
  Object.assign(env, parseEnv(readFileSync(path, "utf-8")));
  return true;
}

/** Read `section.key` from demo.yaml without a YAML parser (top level and one level of nesting). */
export function manifestValue(text: string, section: string, key: string): string | undefined {
  let current: string | undefined;
  for (const raw of text.split(/\r?\n/)) {
    const line = stripComment(raw);
    if (!line.trim()) continue;
    const m = line.match(/^(\s*)([A-Za-z_][\w-]*):\s*(.*)$/);
    if (!m) continue;
    const value = m[3].trim().replace(/^(["'])(.*)\1$/, "$2") || undefined;
    if (!m[1]) {
      current = m[2];
      if (section === "" && m[2] === key) return value;
    } else if (current === section && m[2] === key) {
      return value;
    }
  }
  return undefined;
}

/** The project the keys must map to: an explicit override, then the .env's name, then demo.yaml. */
export function expectedProject(env: Env, manifest: string): string | undefined {
  return env.DEMO_LANGFUSE_PROJECT || env.LANGFUSE_PROJECT_NAME || manifestValue(manifest, "langfuse", "project");
}

// --- What the seeded data should produce --------------------------------------------------

/** How many fixture traces each persona must see, computed with the app's own policy engine. */
export function expectedVisible(fixtures: LangfuseTrace[], personas: Subject[] = PERSONAS): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of personas) out[p.id] = fixtures.filter((t) => evaluate(p, t).allow).length;
  return out;
}

export function loadFixtures(path: string = FIXTURES_PATH): LangfuseTrace[] {
  return (JSON.parse(readFileSync(path, "utf-8")) as Omit<LangfuseTrace, "id">[]).map((f, i) => ({
    id: `fixture-${i}`,
    ...f,
  })) as LangfuseTrace[];
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

// --- HTTP ---------------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(readonly status: number | undefined, message: string) {
    super(status ? `HTTP ${status}: ${message}` : message);
  }
}

export type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

export async function getJson(fetchFn: Fetch, url: string, headers: Record<string, string> = {}): Promise<unknown> {
  let res: Response;
  try {
    res = await fetchFn(url, { headers: { Accept: "application/json", ...headers }, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (err) {
    const e = err as Error & { cause?: { code?: string } };
    throw new ApiError(undefined, e.cause?.code ?? e.name ?? String(err));
  }
  const body = await res.text();
  if (!res.ok) throw new ApiError(res.status, body.slice(0, 200) || res.statusText);
  try {
    return body ? JSON.parse(body) : null;
  } catch {
    throw new ApiError(undefined, `non-JSON response from ${url}`);
  }
}

// --- Checks -------------------------------------------------------------------------------

export interface RunOptions {
  argv?: string[];
  env?: Env;
  envPath?: string;
  manifest?: string;
  fixtures?: LangfuseTrace[];
  fetch?: Fetch;
  out?: (line: string) => void;
  now?: Date;
}

interface State {
  projectId?: string;
}

async function langfuseChecks(c: Checker, f: Fetch, base: string, auth: string, want: string | undefined,
  fixtureCount: number, now: Date, state: State): Promise<void> {
  const headers = { Authorization: `Basic ${auth}` };
  c.print("Langfuse");
  let projects: Array<{ id?: string; name?: string }>;
  try {
    projects = ((await getJson(f, `${base}/api/public/projects`, headers)) as { data?: typeof projects })?.data ?? [];
  } catch (e) {
    c.check(false, `keys authenticate against ${base}: ${(e as Error).message}`,
      { hint: "401: wrong keys or LANGFUSE_BASE_URL; network error: is the langfuse profile up?" });
    return;
  }
  const proj = projects[0] ?? {};
  state.projectId = proj.id;
  if (!c.check(Boolean(want) && proj.name === want, `keys map to project '${proj.name}' (want '${want}')`,
    { hint: "use this project's keys in .env, or set LANGFUSE_PROJECT_NAME to its name" })) return;

  try {
    const q = encodeURIComponent(traceCountQuery(now));
    const n = metricCount(await getJson(f, `${base}/api/public/metrics?query=${q}`, headers));
    const hint = n !== undefined && n > fixtureCount
      ? "the seeder appends on every run: wipe the project's traces and seed once (README, Troubleshooting)"
      : `run ${SEED_CMD}; traces take a few seconds to land`;
    c.check(n === fixtureCount, `project holds ${n ?? "?"} trace(s) (want ${fixtureCount}, one per fixture)`, { hint });
  } catch (e) {
    const err = e as ApiError;
    c.check(false, `trace count read failed: ${err.message}`,
      { hint: err.status === 404 ? "Metrics v1 is missing: this demo needs the self-hosted v3 server" : "is Langfuse healthy?" });
  }
}

async function appChecks(c: Checker, f: Fetch, appUrl: string, expected: Record<string, number>,
  fixtureCount: number): Promise<void> {
  c.print("App");
  try {
    const r = await f(`${appUrl}/api/personas`, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!c.check(r.ok, `app up at ${appUrl} (${r.status})`, { hint: `run ${RUN_CMD}` })) return;
  } catch (e) {
    const err = e as Error & { cause?: { code?: string } };
    c.check(false, `app not reachable at ${appUrl}: ${err.cause?.code ?? err.name}`, { hint: `run ${RUN_CMD} in demos/langfuse-rls` });
    return;
  }
  c.print("Personas (through the app's policy engine)");
  for (const p of PERSONAS) {
    const want = expected[p.id];
    try {
      const resp = (await getJson(f, `${appUrl}/api/traces?persona=${p.id}`)) as TracesApiResponse;
      const visible = resp.visible?.length ?? 0;
      const total = visible + (resp.denied?.count ?? 0);
      const ok = !resp.error && visible === want && total === fixtureCount;
      const hint = resp.error
        ? `${resp.error} Check the keys the app was started with (shell exports beat .env in next dev)`
        : total !== fixtureCount
          ? `app sees ${total} traces, want ${fixtureCount}; the app caches for 60 s, so wait and re-run after a reseed`
          : "the policy engine disagrees with the fixtures: run npm test";
      c.check(ok, `${p.name} (${p.clearance}) sees ${visible}/${total} (want ${want}/${fixtureCount})`, { hint });
    } catch (e) {
      c.check(false, `${p.name}: ${(e as Error).message}`, { hint: "check the app's terminal for errors" });
    }
  }
}

export function actLinks(base: string, appUrl: string, state: State): Array<[string, string]> {
  return [
    ["Opening · every member sees all", `${base}/project/${state.projectId}/traces`],
    ["Act 1 · persona switch", `${appUrl}/`],
    ["Act 2 · the policy model", `${appUrl}/policies`],
    ["Act 3 · design and gaps", `${appUrl}/design`],
  ];
}

export async function run(opts: RunOptions = {}): Promise<number> {
  const env = opts.env ?? process.env;
  const f = opts.fetch ?? fetch;
  const now = opts.now ?? new Date();
  const c = new Checker(opts.out);
  const links = (opts.argv ?? []).includes("--links");
  const envPath = opts.envPath ?? resolve(DEMO_DIR, ".env");
  const manifestPath = resolve(DEMO_DIR, "demo.yaml");
  const manifest = opts.manifest ?? (existsSync(manifestPath) ? readFileSync(manifestPath, "utf-8") : "");
  const fixtures = opts.fixtures ?? loadFixtures();
  const state: State = {};

  c.print("langfuse-rls readiness");
  c.print("Config");
  const shown = envPath.startsWith(REPO_ROOT) ? relative(REPO_ROOT, envPath) : envPath;
  c.check(loadEnv(envPath, env), `env loaded from ${shown}; shell-exported LANGFUSE_* ignored`,
    { hint: "cp .env.example .env and fill it in" });
  const base = (env.LANGFUSE_BASE_URL || env.LANGFUSE_HOST || "").replace(/\/+$/, "");
  const pk = env.LANGFUSE_PUBLIC_KEY ?? "";
  const sk = env.LANGFUSE_SECRET_KEY ?? "";
  const appUrl = (env.DEMO_APP_URL || DEFAULT_APP_URL).replace(/\/+$/, "");
  const expected = expectedVisible(fixtures);

  if (c.check(Boolean(base && pk && sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set")) {
    const auth = Buffer.from(`${pk}:${sk}`).toString("base64");
    await langfuseChecks(c, f, base, auth, expectedProject(env, manifest), fixtures.length, now, state);
  }
  await appChecks(c, f, appUrl, expected, fixtures.length);

  if (links && state.projectId) {
    c.print("\nLinks");
    for (const [label, url] of actLinks(base, appUrl, state)) c.print(`  ${label.padEnd(34)} ${url}`);
  }
  const [line, code] = summary(c.results);
  c.print(`\n${line}`);
  return code;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  run({ argv: process.argv.slice(2) }).then((code) => process.exit(code));
}
