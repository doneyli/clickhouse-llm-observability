// The one place the seeder and the readiness check get their Langfuse settings from.
//
// The demo's own .env is the only source of LANGFUSE_*: inherited shell exports are cleared
// first, so a key for another project can never be used, even when the file is missing or
// leaves a key out. Node built-ins only.
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export type Env = Record<string, string | undefined>;

export const DEMO_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const REPO_ROOT = resolve(DEMO_DIR, "..", "..");
export const ENV_PATH = resolve(DEMO_DIR, ".env");
export const MANIFEST_PATH = resolve(DEMO_DIR, "demo.yaml");

function stripComment(value: string): string {
  for (let i = 0; i < value.length; i++) {
    if (value[i] === "#" && (i === 0 || /\s/.test(value[i - 1]))) return value.slice(0, i).trimEnd();
  }
  return value.trimEnd();
}

/** Parse KEY=value lines: blank lines, comments, `export`, quotes and inline comments. */
export function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    let line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    if (line.startsWith("export ")) line = line.slice("export ".length);
    const eq = line.indexOf("=");
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    const q = value[0];
    if ((q === '"' || q === "'") && value.lastIndexOf(q) > 0) {
      out[key] = value.slice(1, value.lastIndexOf(q));
    } else {
      out[key] = stripComment(value);
    }
  }
  return out;
}

/**
 * Make `path` the only source of LANGFUSE_*: drop every inherited LANGFUSE_* first, then apply
 * the file. Shell LANGFUSE_* are dropped even when the file is missing, so a missing file can
 * only fail, never fall back to another project. Returns whether the file was loaded.
 */
export function loadEnv(path: string = ENV_PATH, env: Env = process.env): boolean {
  for (const k of Object.keys(env)) if (k.startsWith("LANGFUSE_")) delete env[k];
  if (!existsSync(path)) return false;
  Object.assign(env, parseEnv(readFileSync(path, "utf-8")));
  return true;
}

export interface LangfuseSettings {
  baseUrl: string;
  publicKey: string;
  secretKey: string;
}

/** The three values every Langfuse call needs, or the names of the ones that are missing. */
export function langfuseSettings(env: Env): { settings?: LangfuseSettings; missing: string[] } {
  const baseUrl = (env.LANGFUSE_BASE_URL || env.LANGFUSE_HOST || "").replace(/\/+$/, "");
  const publicKey = env.LANGFUSE_PUBLIC_KEY ?? "";
  const secretKey = env.LANGFUSE_SECRET_KEY ?? "";
  const missing = [
    ...(baseUrl ? [] : ["LANGFUSE_BASE_URL"]),
    ...(publicKey ? [] : ["LANGFUSE_PUBLIC_KEY"]),
    ...(secretKey ? [] : ["LANGFUSE_SECRET_KEY"]),
  ];
  return missing.length ? { missing } : { settings: { baseUrl, publicKey, secretKey }, missing };
}

/** Read `section.key` from demo.yaml without a YAML parser (top level and one level of nesting). */
export function manifestValue(text: string, section: string, key: string): string | undefined {
  let current: string | undefined;
  for (const raw of text.split(/\r?\n/)) {
    const line = stripComment(raw);
    if (!line.trim()) continue;
    const m = line.match(/^(\s*)([A-Za-z_][\w-]*):\s*(.*)$/);
    if (!m) continue;
    const value = m[3].trim().replace(/^(["'])(.*)\1$/, "$2") || undefined;
    if (!m[1]) {
      current = m[2];
      if (section === "" && m[2] === key) return value;
    } else if (current === section && m[2] === key) {
      return value;
    }
  }
  return undefined;
}

export function readManifest(path: string = MANIFEST_PATH): string {
  return existsSync(path) ? readFileSync(path, "utf-8") : "";
}

/** The project the keys must map to: an explicit override, then the .env's name, then demo.yaml. */
export function expectedProject(env: Env, manifest: string): string | undefined {
  return env.DEMO_LANGFUSE_PROJECT || env.LANGFUSE_PROJECT_NAME || manifestValue(manifest, "langfuse", "project");
}
