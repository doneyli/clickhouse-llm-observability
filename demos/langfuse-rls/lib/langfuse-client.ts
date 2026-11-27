// The app's only Langfuse read: every trace in the project, for the policy engine to filter.
//
// It reads Observations API v2 and rebuilds each trace from its root observation
// (lib/trace-read.ts). The legacy `GET /api/public/traces` is used only when v2 answers 404,
// which happens on the self-hosted v3 server and nowhere else. Results are cached for 60 s.
import { readTraces, type ReadSource } from "./trace-read";
import type { LangfuseTrace } from "./types";

const host = process.env.LANGFUSE_BASE_URL ?? process.env.LANGFUSE_HOST ?? "http://localhost:3001";
const publicKey = process.env.LANGFUSE_PUBLIC_KEY ?? "";
const secretKey = process.env.LANGFUSE_SECRET_KEY ?? "";

const TTL_MS = 60_000;

let cache: { value: LangfuseTrace[]; expiresAt: number } | undefined;
let lastSource: ReadSource | undefined;

export async function listTraces(): Promise<LangfuseTrace[]> {
  if (cache && Date.now() <= cache.expiresAt) return cache.value;

  let traces: LangfuseTrace[];
  try {
    const read = await readTraces({ baseUrl: host, publicKey, secretKey });
    if (read.source !== lastSource) {
      console.info(`[langfuse-client] reading traces via ${read.source === "observations-v2"
        ? "Observations API v2" : "the legacy trace list (Observations v2 returned 404: self-hosted v3 server)"}`);
      lastSource = read.source;
    }
    traces = read.traces;
  } catch (err) {
    console.error("[langfuse-client] listTraces failed:", err);
    return [];
  }

  cache = { value: traces, expiresAt: Date.now() + TTL_MS };
  return traces;
}
