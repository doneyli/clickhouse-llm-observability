/**
 * Environment + Langfuse credentials for the grocery-assistant demo.
 *
 * Key isolation is the landmine that costs the most time in a repo with several
 * Langfuse projects: a stray `LANGFUSE_*` export in your shell silently sends
 * every trace to a different project, and then your queries return 404 against
 * the project you *thought* you were using. So this module loads THIS folder's
 * .env with `override: true` and hard-sets the values, and `verifyProject()`
 * confirms the keys resolve to the project we expect before anything is written.
 */
import { config as loadEnv } from "dotenv";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(here, "..", ".env");

// override: true — the .env file wins over whatever is already exported.
loadEnv({ path: envPath, override: true, quiet: true });

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Missing ${name}. Run ./scripts/provision-project.sh, then add your model key to .env`,
    );
  }
  return value;
}

export const LANGFUSE_PUBLIC_KEY = required("LANGFUSE_PUBLIC_KEY");
export const LANGFUSE_SECRET_KEY = required("LANGFUSE_SECRET_KEY");
export const LANGFUSE_BASE_URL =
  process.env["LANGFUSE_BASE_URL"] ?? "http://localhost:3001";
export const EXPECTED_PROJECT =
  process.env["LANGFUSE_PROJECT_NAME"] ?? "grocery-assistant";

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
