/**
 * OpenTelemetry + Langfuse wiring. MUST be imported before any AI SDK call.
 *
 * AI SDK 7 changed how telemetry works: it uses a callback-based system, and the
 * Langfuse-owned `@langfuse/vercel-ai-sdk` integration is registered ONCE at
 * startup via `registerTelemetry`. The older `experimental_telemetry: { isEnabled: true }`
 * per-call flag is the AI SDK v6 path and does nothing on 7 — a subtle way to end
 * up with no LLM spans at all, which is worth knowing because it looks exactly
 * like a broken exporter.
 *
 * The integration requires Node 22+.
 *
 * Reference: https://langfuse.com/integrations/frameworks/vercel-ai-sdk
 */
import "./env.js";

import { LangfuseSpanProcessor } from "@langfuse/otel";
import { LangfuseVercelAiSdkIntegration } from "@langfuse/vercel-ai-sdk";
import { NodeSDK } from "@opentelemetry/sdk-node";
import { registerTelemetry } from "ai";

/**
 * Exported so short-lived processes can flush before exiting. Every script here
 * is short-lived, so forgetting this is the #1 cause of "the run finished but
 * Langfuse is empty" — the batch never left the process.
 */
export const langfuseSpanProcessor = new LangfuseSpanProcessor();

const sdk = new NodeSDK({ spanProcessors: [langfuseSpanProcessor] });
sdk.start();

registerTelemetry(new LangfuseVercelAiSdkIntegration());

let flushed = false;

/** Flush pending spans. Safe to call more than once. */
export async function flushTraces(): Promise<void> {
  if (flushed) return;
  flushed = true;
  await langfuseSpanProcessor.forceFlush();
}

// Belt and braces: if a script throws before its own flush, still try to export.
process.on("beforeExit", () => {
  void flushTraces();
});
