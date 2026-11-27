/**
 * A fake Langfuse public API for the offline tests, strict about the contract.
 *
 * Each route declares the query parameters the real endpoint accepts (from the
 * Langfuse OpenAPI schema, or for the v1 routes the 3.x server's), and the fake
 * answers 400 for an unknown parameter, an unknown field group, a missing required
 * parameter or a limit over the endpoint's maximum. Observations v2 and Scores v3
 * rows are projected to the requested field groups, so a reader that forgets
 * `basic` or `subject` gets what the real API would give it: nothing.
 */
import type { FetchLike } from "../scripts/verify_demo.js";

type Row = Record<string, unknown>;

export type Spec = {
  params: string[];
  required?: string[];
  maxLimit?: number;
  fieldGroups?: string[];
  /** At most one of these parameters may be sent (e.g. Scores v3 traceId / sessionId / experimentId). */
  exclusive?: string[];
  /** Parameter -> a parameter it requires (e.g. Scores v3 observationId needs traceId). */
  requires?: Record<string, string>;
  /** Returns the full result set (for paged routes) or a body (for `raw` routes). */
  rows: (q: URLSearchParams, path: string) => Row[] | Row | undefined;
  paging?: "cursor" | "page" | "none";
  /** Projects one row to the requested field groups. */
  project?: (row: Row, groups: Set<string>) => Row;
};

const OBS_GROUPS: Record<string, string[]> = {
  core: ["id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type"],
  basic: ["name", "level", "statusMessage", "version", "environment", "bookmarked", "public", "userId", "sessionId", "isRootObservation"],
  time: ["completionStartTime", "createdAt", "updatedAt"],
  io: ["input", "output"],
  metadata: ["metadata"],
  model: ["model", "internalModelId", "modelParameters"],
  usage: ["usageDetails", "costDetails", "totalCost", "usagePricingTierName"],
  prompt: ["promptId", "promptName", "promptVersion"],
  metrics: ["latency", "timeToFirstToken"],
  trace_context: ["tags", "release", "traceName"],
};

/** Observations v2: `core` always; no `fields` means core + basic. */
export function projectObservation(row: Row, groups: Set<string>): Row {
  const keep = new Set(["core", ...(groups.size ? groups : ["basic"])].flatMap((g) => OBS_GROUPS[g] ?? []));
  return Object.fromEntries(Object.entries(row).filter(([k]) => keep.has(k)));
}

const SCORE_CORE = ["id", "projectId", "name", "value", "dataType", "source", "timestamp", "environment", "createdAt", "updatedAt"];
const SCORE_GROUPS: Record<string, string[]> = {
  details: ["comment", "configId", "metadata"],
  subject: ["subject"],
  annotation: ["authorUserId", "queueId"],
};

/** Scores v3: core is always returned; `fields` names extra groups only. */
export function projectScore(row: Row, groups: Set<string>): Row {
  const keep = new Set([...SCORE_CORE, ...[...groups].flatMap((g) => SCORE_GROUPS[g] ?? [])]);
  return Object.fromEntries(Object.entries(row).filter(([k]) => keep.has(k)));
}

export const V2_OBS_PARAMS = ["fields", "expandMetadata", "limit", "cursor", "parseIoAsJson", "name", "userId", "sessionId", "type", "traceId", "level", "parentObservationId", "isRootObservation", "environment", "fromStartTime", "toStartTime", "version", "filter"];
export const V1_OBS_PARAMS = ["page", "limit", "name", "userId", "type", "traceId", "parentObservationId", "environment", "fromStartTime", "toStartTime", "version"];
export const SCORES_V3_PARAMS = ["limit", "cursor", "fields", "id", "name", "source", "dataType", "environment", "configId", "queueId", "authorUserId", "value", "valueMin", "valueMax", "traceId", "sessionId", "observationId", "experimentId", "fromTimestamp", "toTimestamp"];
export const EXPERIMENTS_PARAMS = ["fields", "limit", "scoreLimit", "cursor", "fromStartTime", "toStartTime", "id", "name", "datasetId", "filter"];
export const EXPERIMENT_ITEMS_PARAMS = ["fields", "limit", "scoreLimit", "cursor", "fromStartTime", "toStartTime", "experimentId", "experimentName", "experimentItemId", "datasetId", "filter"];
export const DATASET_ITEMS_PARAMS = ["datasetName", "sourceTraceId", "sourceObservationId", "version", "page", "limit"];

export type FakeOptions = {
  /** Page size the fake serves for cursor routes (independent of `limit`, as the real API may). */
  pageSize?: number;
  /** Serve this many empty pages (with a cursor) before the first row. */
  emptyPagesFirst?: number;
  /** Return the same cursor forever. */
  repeatCursor?: boolean;
  /** Paths (exact) that answer with this status. */
  fail?: Record<string, number>;
};

export type Fake = { fetch: FetchLike; seen: string[]; rejected: string[] };

export function fakeLangfuse(routes: Record<string, Spec>, opts: FakeOptions = {}): Fake {
  const seen: string[] = [];
  const rejected: string[] = [];
  const { pageSize = 2, emptyPagesFirst = 0, repeatCursor = false, fail = {} } = opts;

  const reply = (status: number, body: unknown) => ({
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  });

  const fetch: FetchLike = async (raw) => {
    const url = new URL(raw);
    seen.push(url.pathname + url.search);
    if (fail[url.pathname]) return reply(fail[url.pathname]!, { message: "injected failure" });
    const spec = Object.entries(routes).find(([pattern]) => matches(pattern, url.pathname))?.[1];
    if (!spec) return reply(404, { message: "Not found", error: "LangfuseNotFoundError" });

    const q = url.searchParams;
    const bad = [...q.keys()].filter((k) => !spec.params.includes(k));
    const missing = (spec.required ?? []).filter((k) => !q.has(k));
    const limit = q.get("limit");
    const groups = new Set((q.get("fields") ?? "").split(",").filter(Boolean));
    const badGroups = spec.fieldGroups ? [...groups].filter((g) => !spec.fieldGroups!.includes(g)) : [];
    const overLimit = spec.maxLimit !== undefined && limit !== null && Number(limit) > spec.maxLimit;
    const clash = (spec.exclusive ?? []).filter((k) => q.has(k));
    const unmet = Object.entries(spec.requires ?? {}).filter(([k, needs]) => q.has(k) && !q.has(needs));
    if (bad.length || missing.length || badGroups.length || overLimit || clash.length > 1 || unmet.length) {
      rejected.push(url.pathname + url.search);
      return reply(400, { message: "invalid request", bad, missing, badGroups, overLimit, clash, unmet });
    }

    const result = spec.rows(q, url.pathname);
    if (result === undefined) return reply(404, { message: "Not found", error: "LangfuseNotFoundError" });
    if (!Array.isArray(result)) return reply(200, result);
    const rows = spec.project ? result.map((r) => spec.project!(r, groups)) : result;

    if (spec.paging === "page") {
      const size = Number(limit ?? 50);
      const page = Number(q.get("page") ?? 1);
      const totalPages = Math.max(1, Math.ceil(rows.length / size));
      return reply(200, { data: rows.slice((page - 1) * size, page * size), meta: { page, limit: size, totalItems: rows.length, totalPages } });
    }
    if (spec.paging === "cursor") {
      // Cursor "e<n>" = still serving empty pages; "c<offset>" = offset into rows.
      const cursor = q.get("cursor");
      if (repeatCursor) return reply(200, { data: rows.slice(0, pageSize), meta: { cursor: "same" } });
      let empties = emptyPagesFirst;
      let offset = 0;
      if (cursor?.startsWith("e")) empties = Number(cursor.slice(1));
      else if (cursor?.startsWith("c")) {
        empties = 0;
        offset = Number(cursor.slice(1));
      }
      if (empties > 0) return reply(200, { data: [], meta: { cursor: empties > 1 ? `e${empties - 1}` : "c0" } });
      const page = rows.slice(offset, offset + pageSize);
      const next = offset + pageSize < rows.length ? `c${offset + pageSize}` : undefined;
      return reply(200, { data: page, meta: next ? { cursor: next } : {} });
    }
    return reply(200, { data: rows });
  };
  return { fetch, seen, rejected };
}

function matches(pattern: string, path: string): boolean {
  const re = new RegExp(`^${pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\\\{[^}]+\\\}/g, "[^/]+")}$`);
  return re.test(path);
}
