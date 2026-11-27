// Reading the project's traces: the one read the app, the seeder and the readiness check share.
//
// Primary path: Observations API v2 (`GET /api/public/v2/observations`), the current read API
// on Langfuse v4 servers and on Cloud. v4 has no trace objects, so each trace is rebuilt from
// its logical root observation: name, userId and tags from the trace context, and input,
// output and the RLS metadata (classification, team, topic) from the root observation itself,
// which the seeder writes. A trace created without observations comes back as one synthetic
// row `t-<traceId>`; it still supplies the trace context, but no input or output.
//
// Fallback: the legacy `GET /api/public/traces`, used only when Observations v2 answers the
// first request with 404. That is exactly the self-hosted v3 server (the 3.221.1 image the
// repo's committed docker-compose.yaml still pins), where v2 does not exist. A v4 server or
// Cloud never reaches it, and any other v2 failure is an error, not a reason to fall back.
import type { LangfuseTrace } from "./types";

export type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

/** No fixture predates the demo (built July 2026); every read is bounded from here to now. */
export const READ_FROM = "2026-01-01T00:00:00.000Z";
export const V2_PATH = "/api/public/v2/observations";
export const LEGACY_PATH = "/api/public/traces";
/** core is always returned; basic: name, userId, isRootObservation; trace_context: traceName, tags. */
export const V2_FIELDS = "core,basic,io,metadata,trace_context";
/** Metadata values over 200 characters are truncated unless named here. */
export const RLS_METADATA_KEYS = ["classification", "team", "topic"];
export const V2_LIMIT = 1000;
export const V2_MAX_PAGES = 200;
export const LEGACY_LIMIT = 100;
export const LEGACY_MAX_PAGES = 100;
const TIMEOUT_MS = 10_000;

export type ReadSource = "observations-v2" | "legacy-traces";

/** A trace as the app uses it, plus where it came from. */
export interface ReadTrace extends LangfuseTrace {
  /** The id of the trace's real (non-synthetic) root observation; v2 reads only. */
  rootObservationId?: string;
}

export interface TraceRead {
  traces: ReadTrace[];
  source: ReadSource;
}

export interface ReadTarget {
  baseUrl: string;
  publicKey: string;
  secretKey: string;
  fetch?: FetchLike;
}

export interface ReadOptions {
  /** Only traces carrying every one of these tags. */
  tags?: string[];
  now?: Date;
}

export class ReadError extends Error {
  constructor(readonly status: number | undefined, message: string) {
    super(status ? `HTTP ${status}: ${message}` : message);
  }
}

/** One Observations v2 row, as far as this demo reads it. Fields of groups not requested are absent. */
export interface V2Row {
  id: string;
  traceId: string;
  startTime?: string;
  parentObservationId?: string | null;
  name?: string | null;
  userId?: string | null;
  isRootObservation?: boolean;
  input?: unknown;
  output?: unknown;
  metadata?: unknown;
  tags?: string[] | null;
  traceName?: string | null;
}

/** The v2 `filter`: logical roots, started in [from, to), optionally carrying every tag. */
export function v2Filter(from: string, to: string, tags: string[] = []): string {
  const conditions: unknown[] = [
    { type: "datetime", column: "startTime", operator: ">=", value: from },
    { type: "datetime", column: "startTime", operator: "<", value: to },
    { type: "boolean", column: "isRootObservation", operator: "=", value: true },
  ];
  if (tags.length) conditions.push({ type: "arrayOptions", column: "tags", operator: "all of", value: tags });
  return JSON.stringify(conditions);
}

/** v2 returns I/O as raw strings and metadata values may be JSON strings: parse when they are JSON. */
export function parseJsonField(value: unknown): unknown {
  if (typeof value !== "string") return value ?? undefined;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function isSynthetic(row: V2Row): boolean {
  return row.id === `t-${row.traceId}`;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  const v = parseJsonField(value);
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined;
}

/**
 * Group v2 root rows by traceId and rebuild one trace each. The real root observation wins
 * over the synthetic `t-` row for every field it has; the synthetic row fills the gaps.
 */
export function tracesFromRows(rows: V2Row[]): ReadTrace[] {
  const groups = new Map<string, V2Row[]>();
  for (const r of rows) {
    if (!r?.traceId) continue;
    const g = groups.get(r.traceId) ?? [];
    g.push(r);
    groups.set(r.traceId, g);
  }
  const out: ReadTrace[] = [];
  for (const [traceId, group] of groups) {
    const real = group.find((r) => !isSynthetic(r));
    const synth = group.find(isSynthetic);
    const pick = <T>(f: (r: V2Row) => T | null | undefined): T | undefined =>
      (real ? f(real) ?? undefined : undefined) ?? (synth ? f(synth) ?? undefined : undefined);
    const metadata = { ...(asRecord(synth?.metadata) ?? {}), ...(asRecord(real?.metadata) ?? {}) };
    const tags = [...new Set(group.flatMap((r) => r.tags ?? []))];
    const starts = group.map((r) => r.startTime).filter((s): s is string => Boolean(s)).sort();
    out.push({
      id: traceId,
      name: pick((r) => r.traceName || null) ?? pick((r) => r.name) ?? "",
      userId: pick((r) => r.userId),
      tags,
      metadata: Object.keys(metadata).length ? (metadata as LangfuseTrace["metadata"]) : undefined,
      input: pick((r) => parseJsonField(r.input)),
      output: pick((r) => parseJsonField(r.output)),
      timestamp: starts[0],
      rootObservationId: real?.id,
    });
  }
  return out;
}

async function getJson(target: ReadTarget, path: string, params: URLSearchParams): Promise<unknown> {
  const f = target.fetch ?? fetch;
  const auth = Buffer.from(`${target.publicKey}:${target.secretKey}`).toString("base64");
  const url = `${target.baseUrl.replace(/\/+$/, "")}${path}?${params.toString()}`;
  let res: Response;
  try {
    res = await f(url, {
      headers: { Authorization: `Basic ${auth}`, Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (err) {
    const e = err as Error & { cause?: { code?: string } };
    throw new ReadError(undefined, e?.cause?.code ?? e?.name ?? String(err));
  }
  const body = await res.text();
  if (!res.ok) throw new ReadError(res.status, body.slice(0, 200) || res.statusText);
  try {
    return body ? JSON.parse(body) : null;
  } catch {
    throw new ReadError(undefined, `non-JSON response from ${path}`);
  }
}

/**
 * Every root row in [READ_FROM, now], following `meta.cursor` until it is absent. A page may
 * be empty while a cursor is still present, so emptiness never ends the walk; a repeated
 * cursor or more than V2_MAX_PAGES pages is an error, never a loop. Returns null when the
 * server has no Observations v2 (404 on the first request).
 */
export async function readV2Rows(target: ReadTarget, opts: ReadOptions = {}): Promise<V2Row[] | null> {
  const to = new Date((opts.now ?? new Date()).getTime() + 60 * 60_000).toISOString();
  const filter = v2Filter(READ_FROM, to, opts.tags);
  const rows: V2Row[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  for (let page = 1; page <= V2_MAX_PAGES; page++) {
    const params = new URLSearchParams({
      fields: V2_FIELDS,
      expandMetadata: RLS_METADATA_KEYS.join(","),
      filter,
      limit: String(V2_LIMIT),
    });
    if (cursor) params.set("cursor", cursor);
    let d: { data?: V2Row[]; meta?: { cursor?: string | null } } | null;
    try {
      d = (await getJson(target, V2_PATH, params)) as typeof d;
    } catch (e) {
      if (page === 1 && (e as ReadError).status === 404) return null;
      throw e;
    }
    rows.push(...(d?.data ?? []));
    const next = d?.meta?.cursor;
    if (!next) return rows;
    if (seen.has(next)) throw new ReadError(undefined, `Observations v2 repeated cursor on page ${page}; stopping`);
    seen.add(next);
    cursor = next;
  }
  throw new ReadError(undefined, `Observations v2 still had pages after ${V2_MAX_PAGES} x ${V2_LIMIT}`);
}

/** The legacy trace list (self-hosted v3 only), by page number until totalPages. */
export async function readLegacyTraces(target: ReadTarget, opts: ReadOptions = {}): Promise<ReadTrace[]> {
  const out: ReadTrace[] = [];
  const seen = new Set<string>();
  for (let page = 1; page <= LEGACY_MAX_PAGES; page++) {
    const params = new URLSearchParams({ page: String(page), limit: String(LEGACY_LIMIT) });
    for (const t of opts.tags ?? []) params.append("tags", t);
    const d = ((await getJson(target, LEGACY_PATH, params)) ?? {}) as { data?: ReadTrace[]; meta?: { totalPages?: number } };
    const rows = d.data ?? [];
    const fresh = rows.filter((t) => !seen.has(t.id));
    if (rows.length && !fresh.length) throw new ReadError(undefined, `legacy trace list repeated page ${page}; stopping`);
    for (const t of fresh) seen.add(t.id);
    out.push(...fresh);
    if (!rows.length || page >= (d.meta?.totalPages ?? 0)) return out;
  }
  throw new ReadError(undefined, `legacy trace list still had pages after ${LEGACY_MAX_PAGES} x ${LEGACY_LIMIT}`);
}

/** The project's traces: Observations v2, or the legacy list only when v2 does not exist. */
export async function readTraces(target: ReadTarget, opts: ReadOptions = {}): Promise<TraceRead> {
  const rows = await readV2Rows(target, opts);
  if (rows) return { traces: tracesFromRows(rows), source: "observations-v2" };
  return { traces: await readLegacyTraces(target, opts), source: "legacy-traces" };
}
