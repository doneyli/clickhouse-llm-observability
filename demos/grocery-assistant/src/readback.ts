/**
 * Reading observations back, on whichever API the server has.
 *
 * Observations API v2 is the current read path (Langfuse Cloud, v4 servers). A
 * self-hosted v3 server answers it with 404 ("only available in a Langfuse v4 write
 * mode"), and this repo's stack pins 3.221.1, so on a 3.x server — and only there —
 * reads fall back to the v1 observation list, `GET /api/public/observations`. The
 * legacy `/api/public/traces` endpoints are not used on either path.
 *
 * Shape differences the callers must not care about:
 *   v2 — input/output are serialized JSON strings; `sessionId` is a column on every
 *        observation (field group `basic`); cursor paging via `meta.cursor`, and a
 *        page may be empty while a cursor is still returned.
 *   v1 — input/output are parsed objects; the session is the propagated OTel
 *        attribute in `metadata.attributes["session.id"]`; page paging via
 *        `meta.totalPages`.
 *
 * No credentials are read here: callers pass a `get` bound to their own client, so
 * this module is testable offline.
 */

/** GET a public-API path (with query string). `undefined` means 404. */
export type Get = <T>(path: string) => Promise<T | undefined>;

export type ApiFlavour = "v1" | "v2";

/** Major 3 has no Observations v2. Anything else is treated as v4-or-later. */
export function apiFlavourFor(serverVersion: string | undefined): ApiFlavour {
  return serverVersion?.startsWith("3.") ? "v1" : "v2";
}

type Params = Record<string, string | number | boolean | undefined>;
type CursorPage<T> = { data?: T[]; meta?: { cursor?: string | null; nextCursor?: string | null } };
type NumberedPage<T> = { data?: T[]; meta?: { totalPages?: number; page?: number } };

function query(params: Params): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) qs.set(k, String(v));
  return qs.toString();
}

/**
 * Follow `meta.cursor` (or `meta.nextCursor`) to the end. Empty pages do not stop the
 * walk — only a missing cursor does — and the walk is bounded: at most `maxPages`
 * requests, and a cursor the server repeats is an error rather than a loop.
 * Running out of pages is an error too, unless `sample` says a bounded sample is
 * what the caller wants (then it returns what it has).
 */
export async function cursorAll<T>(
  get: Get,
  path: string,
  params: Params,
  opts: { limit?: number; maxPages?: number; cap?: number; sample?: boolean } = {},
): Promise<T[]> {
  const { limit = 100, maxPages = 50, cap = Number.POSITIVE_INFINITY, sample = false } = opts;
  const out: T[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  for (let page = 0; page < maxPages; page += 1) {
    const body = await get<CursorPage<T>>(`${path}?${query({ ...params, limit, cursor })}`);
    if (!body) return out;
    out.push(...(body.data ?? []));
    const next = body.meta?.cursor ?? body.meta?.nextCursor ?? undefined;
    if (!next || out.length >= cap) return out.slice(0, cap);
    if (seen.has(next)) throw new Error(`${path}: the server returned cursor ${next} twice`);
    seen.add(next);
    cursor = next;
  }
  if (sample) return out.slice(0, cap);
  throw new Error(`${path}: more than ${maxPages} pages; narrow the query`);
}

/** Follow `meta.totalPages` (v1 observations, dataset items, legacy dataset runs), bounded. */
export async function pagesAll<T>(
  get: Get,
  path: string,
  params: Params,
  opts: { limit?: number; maxPages?: number } = {},
): Promise<T[]> {
  const { limit = 100, maxPages = 50 } = opts;
  const out: T[] = [];
  for (let page = 1; page <= maxPages; page += 1) {
    const body = await get<NumberedPage<T>>(`${path}?${query({ ...params, limit, page })}`);
    if (!body) return out;
    const rows = body.data ?? [];
    out.push(...rows);
    if (rows.length === 0 || page >= (body.meta?.totalPages ?? 1)) return out;
  }
  return out;
}

export type RawObservation = {
  id: string;
  traceId?: string | null;
  type: string;
  name?: string | null;
  startTime?: string;
  parentObservationId?: string | null;
  isRootObservation?: boolean;
  input?: unknown;
  output?: unknown;
  metadata?: unknown;
  /** v4 only: the propagated session, promoted to a column on the observation. */
  sessionId?: string | null;
};

function sessionAttributeOf(metadata: unknown): string | undefined {
  if (typeof metadata !== "object" || metadata === null) return undefined;
  const attrs = (metadata as { attributes?: Record<string, unknown> }).attributes;
  const value = attrs?.["session.id"];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** The observation's session, wherever this server generation keeps it: v4's column first. */
export function sessionOf(o: Pick<RawObservation, "sessionId" | "metadata">): string | undefined {
  if (typeof o.sessionId === "string" && o.sessionId.length > 0) return o.sessionId;
  return sessionAttributeOf(o.metadata);
}

/** Every observation of one trace. `v2Fields` must include `io` when the caller reads input/output. */
export async function traceObservations(
  get: Get,
  flavour: ApiFlavour,
  traceId: string,
  v2Fields = "core,basic,io,metadata",
): Promise<RawObservation[]> {
  if (flavour === "v2") {
    return cursorAll<RawObservation>(get, "/api/public/v2/observations", { traceId, fields: v2Fields }, { limit: 1000 });
  }
  return pagesAll<RawObservation>(get, "/api/public/observations", { traceId });
}

export type TurnRow = { traceId: string; sessionId: string | null; startTime: string; name: string | null };

/**
 * Recent turns named `name`, newest first, one row per trace.
 *
 * v2 lists root observations: each conversation turn played by run-conversation or
 * compare-traces is its own trace. v1 has no root filter and no session column, so
 * it lists the observations with that name and reads the session attribute; a
 * dataset-run trace that holds several turns collapses to one row.
 */
export async function recentTurns(
  get: Get,
  flavour: ApiFlavour,
  name: string,
  opts: { maxRows?: number } = {},
): Promise<TurnRow[]> {
  const { maxRows = 1000 } = opts;
  const raw =
    flavour === "v2"
      ? await cursorAll<RawObservation>(
          get,
          "/api/public/v2/observations",
          { fields: "core,basic", isRootObservation: true },
          { limit: 100, maxPages: 10, cap: maxRows, sample: true },
        )
      : await pagesAll<RawObservation>(get, "/api/public/observations", { name, type: "SPAN" }, { maxPages: 10 });
  const byTrace = new Map<string, TurnRow>();
  for (const o of raw) {
    if (!o.traceId || byTrace.has(o.traceId)) continue;
    byTrace.set(o.traceId, {
      traceId: o.traceId,
      sessionId: sessionOf(o) ?? null,
      startTime: o.startTime ?? "",
      name: o.name ?? null,
    });
  }
  return [...byTrace.values()].sort((a, b) => b.startTime.localeCompare(a.startTime));
}
