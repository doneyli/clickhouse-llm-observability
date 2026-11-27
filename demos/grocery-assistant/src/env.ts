/**
 * Environment + Langfuse credentials for the grocery-assistant demo.
 *
 * Key isolation is the landmine that costs the most time in a repo with several
 * Langfuse projects: a stray `LANGFUSE_*` export in your shell silently sends
 * every trace to a different project, and then your queries return 404 against
 * the project you *thought* you were using. So this module clears every inherited
 * `LANGFUSE_*`, loads THIS folder's .env (required) over the shell, and refuses to
 * continue without the keys (see src/env-file.ts). `verifyProject()` then confirms
 * the keys resolve to the project we expect before anything is written.
 */
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { loadDemoEnv } from "./env-file.js";

const here = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(here, "..", ".env");

// Clears inherited LANGFUSE_*, applies .env over the shell, throws when the file or a key is missing.
loadDemoEnv(envPath);

export const LANGFUSE_PUBLIC_KEY = process.env["LANGFUSE_PUBLIC_KEY"] as string;
export const LANGFUSE_SECRET_KEY = process.env["LANGFUSE_SECRET_KEY"] as string;
export const LANGFUSE_BASE_URL =
  process.env["LANGFUSE_BASE_URL"] || "http://localhost:3001";
export const EXPECTED_PROJECT =
  process.env["LANGFUSE_PROJECT_NAME"] || "grocery-assistant";

// The SDK reads these from the environment; pin them so nothing inherited can win.
process.env["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC_KEY;
process.env["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET_KEY;
process.env["LANGFUSE_BASE_URL"] = LANGFUSE_BASE_URL;

export const AGENT_MODEL = process.env["AGENT_MODEL"] ?? "claude-sonnet-4-6";
export const JUDGE_MODEL = process.env["JUDGE_MODEL"] ?? "claude-sonnet-4-6";

/** Tags on every trace, so the demo's traffic is one filter away in the UI. */
export const BASE_TAGS = ["grocery-assistant", "shopping-assistant"];

/**
 * Confirm the credentials resolve to the expected project. Cheap, and it turns a
 * confusing class of "my traces vanished" bug into one clear error at startup.
 */
export async function verifyProject(): Promise<void> {
  const auth = Buffer.from(
    `${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}`,
  ).toString("base64");
  const res = await fetch(`${LANGFUSE_BASE_URL}/api/public/projects`, {
    headers: { Authorization: `Basic ${auth}` },
  });
  if (!res.ok) {
    throw new Error(
      `Langfuse rejected these keys (HTTP ${res.status}) at ${LANGFUSE_BASE_URL}. ` +
        `Is the stack running? Try: docker compose --profile langfuse up -d`,
    );
  }
  const body = (await res.json()) as { data?: Array<{ name?: string }> };
  const names = (body.data ?? []).map((p) => p.name);
  if (!names.includes(EXPECTED_PROJECT)) {
    throw new Error(
      `These keys resolve to [${names.join(", ")}], not '${EXPECTED_PROJECT}'. ` +
        `Refusing to write to the wrong project.`,
    );
  }
  console.log(`✓ Langfuse project verified: ${EXPECTED_PROJECT} @ ${LANGFUSE_BASE_URL}`);
}
