/**
 * Env isolation (135-2): src/env-file.ts directly, and the seeder's real import path
 * (scripts/seed-dataset.ts -> src/instrumentation.ts -> src/env.ts) in a child process
 * run against a throwaway copy of the demo, with hostile shell exports. Every case
 * fails or resolves before any Langfuse call; the copies point nowhere reachable.
 * No network, no keys.
 */
import { strict as assert } from "node:assert";
import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { loadDemoEnv } from "../src/env-file.js";

const DEMO = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TSX = join(DEMO, "node_modules", ".bin", "tsx");

const HOSTILE = {
  LANGFUSE_PUBLIC_KEY: "pk-shell-other-project",
  LANGFUSE_SECRET_KEY: "sk-shell-other-project",
  LANGFUSE_BASE_URL: "https://shell.invalid",
  LANGFUSE_HOST: "https://shell.invalid",
  LANGFUSE_PROJECT_NAME: "some-other-project",
};

function tmpEnv(text: string | undefined): string {
  const dir = mkdtempSync(join(tmpdir(), "grocery-env-"));
  if (text !== undefined) writeFileSync(join(dir, ".env"), text);
  return join(dir, ".env");
}

// ---------------------------------------------------------- loadDemoEnv ---
test("a missing .env throws and still clears every inherited LANGFUSE_*", () => {
  const env: NodeJS.ProcessEnv = { ...HOSTILE, ANTHROPIC_API_KEY: "shell-model-key" };
  assert.throws(() => loadDemoEnv(tmpEnv(undefined), env), /No \.env at .*Shell-exported LANGFUSE_\* are ignored/);
  assert.deepEqual(env, { ANTHROPIC_API_KEY: "shell-model-key" });
});

test("a partial .env throws for the missing key instead of borrowing the shell's", () => {
  const env: NodeJS.ProcessEnv = { ...HOSTILE };
  assert.throws(() => loadDemoEnv(tmpEnv("LANGFUSE_PUBLIC_KEY=pk-file\n"), env), /Missing LANGFUSE_SECRET_KEY in /);
  assert.equal(env["LANGFUSE_SECRET_KEY"], undefined);
  assert.equal(env["LANGFUSE_PUBLIC_KEY"], "pk-file");
});

test("a full .env wins, unset LANGFUSE_* stay unset, and other settings are kept", () => {
  const env: NodeJS.ProcessEnv = { ...HOSTILE, ANTHROPIC_API_KEY: "shell-model-key", AGENT_MODEL: "shell-model" };
  loadDemoEnv(tmpEnv("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk-file\nAGENT_MODEL=file-model\n"), env);
  assert.equal(env["LANGFUSE_PUBLIC_KEY"], "pk-file");
  assert.equal(env["LANGFUSE_SECRET_KEY"], "sk-file");
  assert.equal(env["LANGFUSE_BASE_URL"], undefined, "the shell host must not survive");
  assert.equal(env["LANGFUSE_HOST"], undefined);
  assert.equal(env["LANGFUSE_PROJECT_NAME"], undefined, "the shell project name must not survive");
  assert.equal(env["AGENT_MODEL"], "file-model");
  assert.equal(env["ANTHROPIC_API_KEY"], "shell-model-key");
});

test("an empty key in the .env counts as missing", () => {
  assert.throws(() => loadDemoEnv(tmpEnv("LANGFUSE_PUBLIC_KEY=\nLANGFUSE_SECRET_KEY=sk\n"), {}), /Missing LANGFUSE_PUBLIC_KEY/);
});

// ------------------------------------------- the real seed import path ---
/** A copy of the demo's code with a real node_modules link, and the given .env. */
function demoCopy(envText: string | undefined): string {
  const dir = mkdtempSync(join(tmpdir(), "grocery-demo-"));
  cpSync(join(DEMO, "src"), join(dir, "src"), { recursive: true });
  mkdirSync(join(dir, "scripts"));
  cpSync(join(DEMO, "scripts", "seed-dataset.ts"), join(dir, "scripts", "seed-dataset.ts"));
  cpSync(join(DEMO, "package.json"), join(dir, "package.json"));
  symlinkSync(join(DEMO, "node_modules"), join(dir, "node_modules"), "dir");
  if (envText !== undefined) writeFileSync(join(dir, ".env"), envText);
  return dir;
}

function runIn(dir: string, script: string) {
  const res = spawnSync(TSX, [join(dir, script)], {
    cwd: dir,
    env: { PATH: process.env["PATH"] ?? "", HOME: process.env["HOME"] ?? "", ...HOSTILE, ANTHROPIC_API_KEY: "shell-model-key" },
    encoding: "utf8",
    timeout: 60_000,
  });
  return { status: res.status, out: `${res.stdout}\n${res.stderr}` };
}

test("seed-dataset.ts with no .env exits non-zero before any API use", () => {
  const dir = demoCopy(undefined);
  const { status, out } = runIn(dir, "scripts/seed-dataset.ts");
  assert.notEqual(status, 0);
  assert.match(out, /No \.env at /);
  assert.doesNotMatch(out, /Langfuse project verified|shell\.invalid/);
});

test("seed-dataset.ts with a partial .env exits non-zero, naming the missing key", () => {
  const dir = demoCopy("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_BASE_URL=http://127.0.0.1:9\n");
  const { status, out } = runIn(dir, "scripts/seed-dataset.ts");
  assert.notEqual(status, 0);
  assert.match(out, /Missing LANGFUSE_SECRET_KEY/);
  assert.doesNotMatch(out, /Langfuse project verified/);
});

test("through the import path, the .env's values win and the shell's other LANGFUSE_* are gone", () => {
  const dir = demoCopy("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk-file\n");
  writeFileSync(
    join(dir, "scripts", "probe.ts"),
    'import { LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, EXPECTED_PROJECT } from "../src/env.js";\n' +
      "console.log(JSON.stringify({ base: LANGFUSE_BASE_URL, pk: LANGFUSE_PUBLIC_KEY, project: EXPECTED_PROJECT, " +
      'host: process.env["LANGFUSE_HOST"] ?? null, model: process.env["ANTHROPIC_API_KEY"] }));\n',
  );
  const { status, out } = runIn(dir, "scripts/probe.ts");
  assert.equal(status, 0, out);
  const line = out.split("\n").find((l) => l.startsWith("{"));
  assert.deepEqual(JSON.parse(line ?? "{}"), {
    base: "http://localhost:3001",
    pk: "pk-file",
    project: "grocery-assistant",
    host: null,
    model: "shell-model-key",
  });
});
