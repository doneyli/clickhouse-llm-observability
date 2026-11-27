/**
 * Load this demo's `.env` as the only source of LANGFUSE_* settings.
 *
 * `dotenv` with `override: true` only replaces the variables the file sets, so a
 * partial `.env` would quietly keep a shell-exported key, host or project name for
 * another project, and every write would land there. This clears every inherited
 * LANGFUSE_* first, requires the file, applies it over the shell, and checks the
 * required keys before anything can call Langfuse. Other variables (model keys,
 * model names) still come from the shell when the file does not set them.
 *
 * No side effects at import: src/env.ts calls it, and the tests call it directly.
 */
import { existsSync, readFileSync } from "node:fs";

import { parse } from "dotenv";

export const REQUIRED_LANGFUSE_KEYS = ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"] as const;

const SETUP_HINT = "Run ./scripts/provision-project.sh, or cp .env.example .env and fill it in";

export function loadDemoEnv(path: string, env: NodeJS.ProcessEnv = process.env): void {
  for (const key of Object.keys(env)) if (key.startsWith("LANGFUSE_")) delete env[key];
  if (!existsSync(path)) {
    throw new Error(`No .env at ${path}. ${SETUP_HINT}. Shell-exported LANGFUSE_* are ignored.`);
  }
  for (const [key, value] of Object.entries(parse(readFileSync(path)))) env[key] = value;
  const missing = REQUIRED_LANGFUSE_KEYS.filter((key) => !env[key]);
  if (missing.length > 0) {
    throw new Error(`Missing ${missing.join(", ")} in ${path}. ${SETUP_HINT}, then add your model key.`);
  }
}
