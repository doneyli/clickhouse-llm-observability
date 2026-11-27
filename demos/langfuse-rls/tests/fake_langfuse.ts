// An in-memory Langfuse (the handful of public-API routes the seeder and the check use) and
// the demo app's routes on top of it, for offline tests. It validates what it is sent the way
// the real API would: auth, the query parameters each route accepts, and request bodies.
import { PERSONAS } from "../lib/personas";
import { evaluateBatch } from "../lib/rls-policy";
import type { LangfuseTrace } from "../lib/types";
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

export interface Call {
  method: string;
  path: string;
  params: URLSearchParams;
  body?: unknown;
}

export class FakeLangfuse {
  project = { id: "p1", name: "RLS Demo" };
  traces = new Map<string, StoredTrace>();
  calls: Call[] = [];
  // Fault injection.
  failTraceIds = new Set<string>();       // ingestion: these trace ids come back in `errors`
  ingestionStatus = 207;                  // anything else: the whole batch fails
  listStatus = 200;                       // GET /traces
  repeatPages = false;                    // GET /traces returns page 1 for every page
  metricsStatus = 200;
  deleteStatus = 200;
  // The app.
  appDown = false;
  brokenPages = new Set<string>();        // app paths that return 500

  add(t: Partial<StoredTrace> & { id: string }): void {
    this.traces.set(t.id, { timestamp: "2026-08-17T12:00:00.000Z", tags: [], ...t });
  }

  writes(): Call[] {
    return this.calls.filter((c) => c.method !== "GET");
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
      case "GET /api/public/traces":
        return only(u, ["page", "limit", "tags"]) ?? this.list(u);
      case "GET /api/public/metrics":
        return only(u, ["query"]) ?? this.metrics(u);
      case "POST /api/public/ingestion":
        return only(u, []) ?? this.ingest(body);
      case "DELETE /api/public/traces":
        return only(u, []) ?? this.delete(body);
      default:
        return json({ message: `no route ${route}` }, 404);
    }
  };

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

  private metrics(u: URL): Response {
    if (this.metricsStatus !== 200) return json({ message: "no" }, this.metricsStatus);
    let q: Record<string, unknown>;
    try {
      q = JSON.parse(u.searchParams.get("query") ?? "");
    } catch {
      return json({ message: "query must be JSON" }, 400);
    }
    const okShape = q.view === "traces" && JSON.stringify(q.metrics) === JSON.stringify([{ measure: "count", aggregation: "count" }])
      && typeof q.fromTimestamp === "string" && typeof q.toTimestamp === "string" && Array.isArray(q.filters);
    if (!okShape) return json({ message: "invalid query" }, 400);
    return json({ data: [{ count_count: String(this.traces.size) }] });
  }

  private ingest(body: unknown): Response {
    const batch = (body as { batch?: unknown[] } | undefined)?.batch;
    if (!Array.isArray(batch)) return json({ message: "batch required" }, 400);
    if (this.ingestionStatus !== 207) return json({ message: "ingestion down" }, this.ingestionStatus);
    const successes: Array<{ id: string; status: number }> = [];
    const errors: Array<{ id: string; status: number; message: string }> = [];
    for (const raw of batch) {
      const ev = raw as { id?: string; type?: string; timestamp?: string; body?: StoredTrace };
      if (!ev.id || ev.type !== "trace-create" || !ev.timestamp || !ev.body?.id) {
        errors.push({ id: ev.id ?? "?", status: 400, message: "invalid event" });
        continue;
      }
      if (this.failTraceIds.has(ev.body.id)) {
        errors.push({ id: ev.id, status: 500, message: "storage error" });
        continue;
      }
      const prev = this.traces.get(ev.body.id);
      this.traces.set(ev.body.id, { ...(prev ?? {}), ...ev.body, tags: ev.body.tags ?? prev?.tags ?? [] } as StoredTrace);
      successes.push({ id: ev.id, status: 201 });
    }
    return json({ successes, errors }, 207);
  }

  private delete(body: unknown): Response {
    const ids = (body as { traceIds?: unknown } | undefined)?.traceIds;
    if (!Array.isArray(ids) || !ids.length) return json({ message: "traceIds required" }, 400);
    if (this.deleteStatus !== 200) return json({ message: "delete failed" }, this.deleteStatus);
    for (const id of ids) this.traces.delete(String(id));
    return json({ message: "Traces deleted" });
  }

  /** The demo app, reading this fake's traces and filtering with the real policy engine. */
  private app(u: URL): Response {
    if (this.appDown) throw Object.assign(new TypeError("fetch failed"), { cause: { code: "ECONNREFUSED" } });
    if (this.brokenPages.has(u.pathname)) return new Response("error", { status: 500 });
    if (u.pathname === "/api/personas") return json(PERSONAS);
    if (u.pathname === "/policies" || u.pathname === "/design" || u.pathname === "/") return new Response("<html></html>");
    if (u.pathname === "/api/traces") {
      const persona = PERSONAS.find((p) => p.id === u.searchParams.get("persona")) ?? PERSONAS[0];
      const evaluated = evaluateBatch(persona, [...this.traces.values()] as unknown as LangfuseTrace[]);
      const denied = evaluated.filter((t) => !t._rls.allow);
      return json({ persona, visible: evaluated.filter((t) => t._rls.allow), denied: { count: denied.length, samples: [] },
        ...(this.traces.size ? {} : { error: "Langfuse unreachable or no traces found." }) });
    }
    return new Response("not found", { status: 404 });
  }
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
