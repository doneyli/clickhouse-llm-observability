// An in-memory Langfuse (the public-API routes the app, the seeder and the check use) and the
// demo app's routes on top of it, for offline tests. It validates what it is sent the way the
// real API does: auth, the query parameters each route accepts, request bodies, and, for
// Observations v2, field groups, the filter grammar and cursors.
//
// Two server generations:
//   server = "v4"  Observations v2 is served; the legacy trace list is gone (410, as on Cloud),
//                  so any fallback to it fails loudly. v2 returns observation rows with only the
//                  field groups requested; a trace without observations comes back as one
//                  synthetic row `t-<traceId>` carrying the trace context but no input, output
//                  or metadata (v4 has no trace-level I/O).
//   server = "v3"  Observations v2 404s (self-hosted 3.221.1); the legacy trace list works.
import { PERSONAS } from "../lib/personas";
import { evaluateBatch } from "../lib/rls-policy";
import { readTraces } from "../lib/trace-read";
import type { Fetch } from "../scripts/langfuse_http";

export const LF = "http://lf.test";
export const APP = "http://app.test";
export const KEYS = { pk: "pk-lf-demo", sk: "sk-lf-demo" };

export interface StoredTrace {
  id: string;
  timestamp: string;
  name?: string;
  userId?: string;
  tags: string[];
  metadata?: Record<string, unknown>;
  input?: unknown;
  output?: unknown;
}

export interface StoredObservation {
  id: string;
  traceId: string;
  type: string;
  startTime: string;
  endTime?: string;
  parentObservationId?: string | null;
  name?: string;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown>;
}

export interface Call {
  method: string;
  path: string;
  params: URLSearchParams;
  body?: unknown;
}

const FIELD_GROUPS = ["core", "basic", "time", "io", "metadata", "model", "usage", "prompt", "metrics", "trace_context"];
const V2_PARAMS = ["fields", "expandMetadata", "filter", "limit", "cursor"];
const OPERATORS: Record<string, string[]> = {
  datetime: [">", "<", ">=", "<="],
  boolean: ["=", "<>"],
  arrayOptions: ["any of", "none of", "all of"],
};
const COLUMNS: Record<string, string> = { startTime: "datetime", isRootObservation: "boolean", tags: "arrayOptions" };

export class FakeLangfuse {
  server: "v4" | "v3" = "v4";
  project = { id: "p1", name: "RLS Demo" };
  traces = new Map<string, StoredTrace>();
  observations = new Map<string, StoredObservation>();
  calls: Call[] = [];
  // Fault injection.
  failTraceIds = new Set<string>();       // ingestion: events for these trace ids come back in `errors`
  ingestionStatus = 207;                  // anything else: the whole batch fails
  listStatus = 200;                       // the read: v2 on a v4 server, the legacy list on v3
  v2EmptyPages = 0;                       // v2: this many empty pages, each with a cursor, before the data
  v2RepeatCursor = false;                 // v2: always hand back the first cursor
  repeatPages = false;                    // legacy list: page 1 for every page
  deleteStatus = 200;
  // The app.
  appDown = false;
  brokenPages = new Set<string>();        // app paths that return 500

  /** A trace as the old seeder left it: trace-level fields only, no observations. */
  add(t: Partial<StoredTrace> & { id: string }): void {
    this.traces.set(t.id, { timestamp: "2026-08-17T12:00:00.000Z", tags: [], ...t });
  }

  /** A trace plus the root observation the current seeder writes (`<id>-root`). */
  addWithRoot(t: Partial<StoredTrace> & { id: string }): void {
    this.add(t);
    const s = this.traces.get(t.id)!;
    this.observations.set(`${t.id}-root`, {
      id: `${t.id}-root`, traceId: t.id, type: "SPAN", startTime: s.timestamp, endTime: s.timestamp,
      name: s.name, input: s.input, output: s.output, metadata: s.metadata,
    });
  }

  root(traceId: string): StoredObservation | undefined {
    return this.observations.get(`${traceId}-root`);
  }

  writes(): Call[] {
    return this.calls.filter((c) => c.method !== "GET");
  }

  reads(path: string): Call[] {
    return this.calls.filter((c) => c.method === "GET" && c.path === path);
  }

  fetch: Fetch = async (url, init = {}) => {
    const u = new URL(url);
    const method = (init.method ?? "GET").toUpperCase();
    const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
    if (u.origin === APP) return this.app(u);
    if (u.origin !== LF) throw Object.assign(new TypeError("fetch failed"), { cause: { code: "ENOTFOUND" } });
    this.calls.push({ method, path: u.pathname, params: u.searchParams, body });
    const auth = (init.headers as Record<string, string> | undefined)?.Authorization;
    if (auth !== `Basic ${Buffer.from(`${KEYS.pk}:${KEYS.sk}`).toString("base64")}`) return json({ message: "Invalid credentials" }, 401);
    const route = `${method} ${u.pathname}`;
    switch (route) {
      case "GET /api/public/projects":
        return only(u, []) ?? json({ data: [this.project] });
      case "GET /api/public/v2/observations":
        if (this.server === "v3") return json({ message: "Route not found" }, 404);
        return only(u, V2_PARAMS) ?? this.v2(u);
      case "GET /api/public/traces":
        if (this.server === "v4") return json({ message: "This endpoint has been removed. Use /api/public/v2/observations." }, 410);
        return only(u, ["page", "limit", "tags"]) ?? this.list(u);
      case "POST /api/public/ingestion":
        return only(u, []) ?? this.ingest(body);
      case "DELETE /api/public/traces":
        return only(u, []) ?? this.delete(body);
      default:
        return json({ message: `no route ${route}` }, 404);
    }
  };

  // --- Observations v2 (server v4) --------------------------------------------------------

  /** Every observation row v4 would hold: real observations, or the synthetic row of a bare trace. */
  private v2Rows(): Array<StoredObservation & { synthetic?: boolean }> {
    const byTrace = new Map<string, StoredObservation[]>();
    for (const o of this.observations.values()) byTrace.set(o.traceId, [...(byTrace.get(o.traceId) ?? []), o]);
    const rows: Array<StoredObservation & { synthetic?: boolean }> = [];
    for (const t of this.traces.values()) {
      const obs = byTrace.get(t.id) ?? [];
      if (obs.length) rows.push(...obs);
      else rows.push({ id: `t-${t.id}`, traceId: t.id, type: "SPAN", startTime: t.timestamp, synthetic: true });
    }
    return rows;
  }

  private v2(u: URL): Response {
    const p = u.searchParams;
    if (this.listStatus !== 200) return json({ message: "boom" }, this.listStatus);
    const fields = (p.get("fields") ?? "core,basic").split(",").map((f) => f.trim());
    const badGroup = fields.find((f) => !FIELD_GROUPS.includes(f));
    if (badGroup) return json({ message: `invalid field group: ${badGroup}` }, 400);
    const limit = Number(p.get("limit") ?? "50");
    if (!Number.isInteger(limit) || limit < 1 || limit > 1000) return json({ message: "limit must be 1..1000" }, 400);
    let conditions: Array<{ type?: string; column?: string; operator?: string; value?: unknown }>;
    try {
      conditions = JSON.parse(p.get("filter") ?? "[]");
      if (!Array.isArray(conditions)) throw new Error();
    } catch {
      return json({ message: "filter must be a JSON array" }, 400);
    }
    for (const c of conditions) {
      const type = COLUMNS[c.column ?? ""];
      if (!type || c.type !== type || !OPERATORS[type].includes(c.operator ?? "")) {
        return json({ message: `invalid filter condition ${JSON.stringify(c)}` }, 400);
      }
      if (type === "datetime" && Number.isNaN(Date.parse(String(c.value)))) return json({ message: "invalid datetime" }, 400);
      if (type === "boolean" && typeof c.value !== "boolean") return json({ message: "boolean value required" }, 400);
      if (type === "arrayOptions" && !Array.isArray(c.value)) return json({ message: "array value required" }, 400);
    }
    if (!conditions.some((c) => c.column === "startTime" && (c.operator === ">=" || c.operator === ">"))) {
      return json({ message: "fake: every read must be bounded with a startTime lower bound" }, 400);
    }
    let state = { o: 0, e: this.v2EmptyPages };
    const cursor = p.get("cursor");
    if (cursor !== null) {
      try {
        state = JSON.parse(Buffer.from(cursor, "base64").toString("utf-8"));
        if (typeof state.o !== "number" || typeof state.e !== "number") throw new Error();
      } catch {
        return json({ message: "invalid cursor" }, 400);
      }
    }
    const enc = (s: { o: number; e: number }) => Buffer.from(JSON.stringify(s)).toString("base64");
    if (state.e > 0) return json({ data: [], meta: { cursor: enc({ o: state.o, e: state.e - 1 }) } });

    const expand = new Set((p.get("expandMetadata") ?? "").split(",").filter(Boolean));
    const rows = this.v2Rows()
      .filter((o) => conditions.every((c) => matches(c, o, this.traces.get(o.traceId))))
      .sort((a, b) => b.startTime.localeCompare(a.startTime) || a.id.localeCompare(b.id));
    const slice = rows.slice(state.o, state.o + limit);
    const next = this.v2RepeatCursor ? enc({ o: 0, e: 0 }) : state.o + limit < rows.length ? enc({ o: state.o + limit, e: 0 }) : null;
    return json({ data: slice.map((o) => project(o, this.traces.get(o.traceId), fields, expand, this.project.id)), meta: { cursor: next } });
  }

  // --- Legacy trace list (server v3) ------------------------------------------------------

  private list(u: URL): Response {
    const p = u.searchParams;
    if (this.listStatus !== 200) return json({ message: "boom" }, this.listStatus);
    const page = Number(p.get("page") ?? "1");
    const limit = Number(p.get("limit") ?? "50");
    if (!Number.isInteger(page) || page < 1 || !Number.isInteger(limit) || limit < 1 || limit > 100) {
      return json({ message: "invalid page or limit" }, 400);
    }
    const tags = p.getAll("tags");
    const rows = [...this.traces.values()].filter((t) => tags.every((tag) => t.tags.includes(tag)));
    const totalPages = Math.ceil(rows.length / limit);
    const start = ((this.repeatPages ? 1 : page) - 1) * limit;
    return json({ data: rows.slice(start, start + limit), meta: { page, limit, totalItems: rows.length, totalPages } });
  }

  // --- Writes -----------------------------------------------------------------------------

  private ingest(body: unknown): Response {
    const batch = (body as { batch?: unknown[] } | undefined)?.batch;
    if (!Array.isArray(batch)) return json({ message: "batch required" }, 400);
    if (this.ingestionStatus !== 207) return json({ message: "ingestion down" }, this.ingestionStatus);
    const successes: Array<{ id: string; status: number }> = [];
    const errors: Array<{ id: string; status: number; message: string }> = [];
    for (const raw of batch) {
      const ev = raw as { id?: string; type?: string; timestamp?: string; body?: Record<string, unknown> };
      const b = ev.body ?? {};
      const traceId = ev.type === "span-create" ? b.traceId : b.id;
      const valid = ev.id && ev.timestamp && typeof b.id === "string"
        && (ev.type === "trace-create" || (ev.type === "span-create" && typeof b.traceId === "string" && typeof b.startTime === "string"));
      if (!valid) {
        errors.push({ id: ev.id ?? "?", status: 400, message: "invalid event" });
        continue;
      }
      if (this.failTraceIds.has(String(traceId))) {
        errors.push({ id: ev.id!, status: 500, message: "storage error" });
        continue;
      }
      if (ev.type === "trace-create") {
        const prev = this.traces.get(b.id as string);
        this.traces.set(b.id as string, { ...(prev ?? {}), ...(b as unknown as StoredTrace), tags: (b.tags as string[]) ?? prev?.tags ?? [] });
      } else {
        const prev = this.observations.get(b.id as string);
        this.observations.set(b.id as string, { ...(prev ?? {}), ...(b as unknown as StoredObservation), type: "SPAN" });
        if (!this.traces.has(b.traceId as string)) this.add({ id: b.traceId as string, timestamp: b.startTime as string });
      }
      successes.push({ id: ev.id!, status: 201 });
    }
    return json({ successes, errors }, 207);
  }

  private delete(body: unknown): Response {
    const ids = (body as { traceIds?: unknown } | undefined)?.traceIds;
    if (!Array.isArray(ids) || !ids.length) return json({ message: "traceIds required" }, 400);
    if (this.deleteStatus !== 200) return json({ message: "delete failed" }, this.deleteStatus);
    for (const id of ids) {
      this.traces.delete(String(id));
      for (const o of [...this.observations.values()]) if (o.traceId === String(id)) this.observations.delete(o.id);
    }
    return json({ message: "Traces deleted" });
  }

  /** The demo app: the real read (lib/trace-read.ts) against this fake, then the real policy engine. */
  private async app(u: URL): Promise<Response> {
    if (this.appDown) throw Object.assign(new TypeError("fetch failed"), { cause: { code: "ECONNREFUSED" } });
    if (this.brokenPages.has(u.pathname)) return new Response("error", { status: 500 });
    if (u.pathname === "/api/personas") return json(PERSONAS);
    if (u.pathname === "/policies" || u.pathname === "/design" || u.pathname === "/") return new Response("<html></html>");
    if (u.pathname === "/api/traces") {
      const persona = PERSONAS.find((p) => p.id === u.searchParams.get("persona")) ?? PERSONAS[0];
      let traces: Awaited<ReturnType<typeof readTraces>>["traces"] = [];
      try {
        traces = (await readTraces({ baseUrl: LF, publicKey: KEYS.pk, secretKey: KEYS.sk, fetch: this.fetch })).traces;
      } catch {
        traces = []; // the real app logs and shows the error banner
      }
      const evaluated = evaluateBatch(persona, traces);
      const denied = evaluated.filter((t) => !t._rls.allow);
      return json({ persona, visible: evaluated.filter((t) => t._rls.allow), denied: { count: denied.length, samples: [] },
        ...(traces.length ? {} : { error: "Langfuse unreachable or no traces found." }) });
    }
    return new Response("not found", { status: 404 });
  }
}

function matches(c: { column?: string; operator?: string; value?: unknown }, o: StoredObservation, t?: StoredTrace): boolean {
  switch (c.column) {
    case "startTime": {
      const a = Date.parse(o.startTime);
      const b = Date.parse(String(c.value));
      return c.operator === ">=" ? a >= b : c.operator === ">" ? a > b : c.operator === "<" ? a < b : a <= b;
    }
    case "isRootObservation":
      return (!o.parentObservationId) === (c.operator === "=" ? c.value : !c.value);
    case "tags": {
      const tags = t?.tags ?? [];
      const want = c.value as string[];
      if (c.operator === "all of") return want.every((x) => tags.includes(x));
      if (c.operator === "any of") return want.some((x) => tags.includes(x));
      return !want.some((x) => tags.includes(x));
    }
    default:
      return false;
  }
}

/** One v2 row with only the requested field groups: absent, not null, when not requested. */
function project(o: StoredObservation & { synthetic?: boolean }, t: StoredTrace | undefined, fields: string[],
  expand: Set<string>, projectId: string): Record<string, unknown> {
  const row: Record<string, unknown> = {
    id: o.id, traceId: o.traceId, startTime: o.startTime, endTime: o.endTime ?? null, projectId,
    parentObservationId: o.parentObservationId ?? null, type: o.type,
  };
  if (fields.includes("basic")) {
    Object.assign(row, {
      name: o.synthetic ? (t?.name ?? null) : (o.name ?? null), level: "DEFAULT", statusMessage: null, version: null,
      environment: "default", bookmarked: false, public: false, userId: t?.userId ?? null, sessionId: null,
      isRootObservation: !o.parentObservationId,
    });
  }
  if (fields.includes("io")) {
    // Raw strings, as v2 returns them. A synthetic row has no I/O: v4 keeps none at trace level.
    row.input = o.synthetic || o.input === undefined ? null : JSON.stringify(o.input);
    row.output = o.synthetic || o.output === undefined ? null : JSON.stringify(o.output);
  }
  if (fields.includes("metadata")) {
    const md: Record<string, string> = {};
    for (const [k, v] of Object.entries(o.synthetic ? {} : o.metadata ?? {})) {
      const s = typeof v === "string" ? v : JSON.stringify(v);
      md[k] = expand.has(k) || s.length <= 200 ? s : s.slice(0, 200);
    }
    row.metadata = md;
  }
  if (fields.includes("trace_context")) Object.assign(row, { tags: t?.tags ?? [], release: null, traceName: t?.name ?? null });
  return row;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** 400 for any query parameter the route does not accept, as the real API's validation does. */
function only(u: URL, allowed: string[]): Response | undefined {
  const bad = [...u.searchParams.keys()].filter((k) => !allowed.includes(k));
  return bad.length ? json({ message: `unknown query parameter(s): ${bad.join(", ")}` }, 400) : undefined;
}

export const ENV_TEXT = `LANGFUSE_BASE_URL=${LF}\nLANGFUSE_PUBLIC_KEY=${KEYS.pk}\nLANGFUSE_SECRET_KEY=${KEYS.sk}\n`;
/** Shell exports pointing at another project, which must never be used. */
export const HOSTILE_SHELL = {
  LANGFUSE_BASE_URL: LF,
  LANGFUSE_PUBLIC_KEY: KEYS.pk,
  LANGFUSE_SECRET_KEY: KEYS.sk,
  LANGFUSE_HOST: "https://cloud.langfuse.com",
};
