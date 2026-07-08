import type { LangfuseTrace } from "./types";

const host = process.env.LANGFUSE_BASE_URL ?? process.env.LANGFUSE_HOST ?? "http://localhost:3001";
const pk = process.env.LANGFUSE_PUBLIC_KEY ?? "";
const sk = process.env.LANGFUSE_SECRET_KEY ?? "";
const auth = Buffer.from(`${pk}:${sk}`).toString("base64");

const PAGE_SIZE = 50;
const MAX_PAGES = 100;
const TTL_MS = 60_000;

interface CacheEntry {
  value: unknown;
  expiresAt: number;
}

const cache = new Map<string, CacheEntry>();

function cacheGet<T>(key: string): T | undefined {
  const entry = cache.get(key);
  if (!entry) return undefined;
  if (Date.now() > entry.expiresAt) {
    cache.delete(key);
    return undefined;
  }
  return entry.value as T;
}

function cacheSet(key: string, value: unknown): void {
  cache.set(key, { value, expiresAt: Date.now() + TTL_MS });
}

interface LangfuseApiResponse {
  data: LangfuseTrace[];
  meta: {
    totalPages: number;
    page: number;
    limit: number;
    total: number;
  };
}

async function fetchPage(page: number): Promise<LangfuseApiResponse> {
  const url = `${host}/api/public/traces?page=${page}&limit=${PAGE_SIZE}`;
  const res = await fetch(url, {
    headers: { Authorization: `Basic ${auth}` },
    // No cache at the fetch layer — we manage TTL ourselves.
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Langfuse API error: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<LangfuseApiResponse>;
}

export async function listTraces(): Promise<LangfuseTrace[]> {
  const cached = cacheGet<LangfuseTrace[]>("traces");
  if (cached) return cached;

  const traces: LangfuseTrace[] = [];

  try {
    let page = 1;
    let totalPages = 1;

    while (page <= totalPages && page <= MAX_PAGES) {
      const resp = await fetchPage(page);
      traces.push(...resp.data);
      totalPages = resp.meta.totalPages;
      page += 1;
    }
  } catch (err) {
    console.error("[langfuse-client] listTraces failed:", err);
    return [];
  }

  cacheSet("traces", traces);
  return traces;
}
