// Minimal JSON-over-HTTP for the seeder and the readiness check. Node built-ins only (fetch).
// Every failure becomes an ApiError: HTTP errors carry the status, network errors carry none.
import { type ReadOptions, readTraces, type TraceRead } from "../lib/trace-read";
import type { LangfuseSettings } from "./demo_env";

export type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

export const TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  constructor(readonly status: number | undefined, message: string) {
    super(status ? `HTTP ${status}: ${message}` : message);
  }
}

function networkMessage(err: unknown): string {
  const e = err as Error & { cause?: { code?: string } };
  return e?.cause?.code ?? e?.name ?? String(err);
}

/** One request; JSON in, JSON out. Non-2xx and network failures throw ApiError. */
export async function requestJson(fetchFn: Fetch, url: string, init: RequestInit = {}): Promise<unknown> {
  let res: Response;
  try {
    res = await fetchFn(url, {
      ...init,
      headers: { Accept: "application/json", ...(init.headers as Record<string, string> | undefined) },
      signal: init.signal ?? AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (err) {
    throw new ApiError(undefined, networkMessage(err));
  }
  const body = await res.text();
  if (!res.ok) throw new ApiError(res.status, body.slice(0, 200) || res.statusText);
  try {
    return body ? JSON.parse(body) : null;
  } catch {
    throw new ApiError(undefined, `non-JSON response from ${url}`);
  }
}

/** Authenticated calls against one Langfuse project's public API. */
export class LangfuseHttp {
  private readonly auth: string;

  constructor(readonly settings: LangfuseSettings, private readonly fetchFn: Fetch = fetch) {
    this.auth = Buffer.from(`${settings.publicKey}:${settings.secretKey}`).toString("base64");
  }

  get base(): string {
    return this.settings.baseUrl;
  }

  url(path: string, params: Record<string, string | number | string[] | undefined> = {}): string {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined) continue;
      for (const item of Array.isArray(v) ? v : [v]) q.append(k, String(item));
    }
    const qs = q.toString();
    return `${this.base}${path}${qs ? `?${qs}` : ""}`;
  }

  request(method: string, path: string, opts: { params?: Record<string, string | number | string[] | undefined>; body?: unknown } = {}): Promise<unknown> {
    const headers: Record<string, string> = { Authorization: `Basic ${this.auth}` };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    return requestJson(this.fetchFn, this.url(path, opts.params), {
      method,
      headers,
      ...(opts.body !== undefined ? { body: JSON.stringify(opts.body) } : {}),
    });
  }

  get(path: string, params?: Record<string, string | number | string[] | undefined>): Promise<unknown> {
    return this.request("GET", path, { params });
  }

  /** The project's traces, through the same read the app makes (lib/trace-read.ts). */
  traces(opts: ReadOptions = {}): Promise<TraceRead> {
    return readTraces({ ...this.settings, fetch: this.fetchFn }, opts);
  }

  /** The project these keys belong to (a project key sees exactly one). */
  async project(): Promise<{ id?: string; name?: string }> {
    const d = (await this.get("/api/public/projects")) as { data?: Array<{ id?: string; name?: string }> } | null;
    return d?.data?.[0] ?? {};
  }
}
