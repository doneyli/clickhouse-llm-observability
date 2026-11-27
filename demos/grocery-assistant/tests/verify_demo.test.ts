/**
 * verify_demo.ts offline: results, summary and exit code, the .env rule, the manifest
 * parse, run classification, and full runs against a fake Langfuse (v4 and a 3.x server).
 * No network, no Langfuse, no model calls, no keys.
 */
import { strict as assert } from "node:assert";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, test } from "node:test";

import * as v from "../scripts/verify_demo.js";

// ---------------------------------------------------------------- helpers ---
let logged: string[] = [];
const realLog = console.log;
beforeEach(() => {
  logged = [];
  console.log = (...args: unknown[]) => void logged.push(args.join(" "));
  v.RESULTS.length = 0;
});
afterEach(() => {
  console.log = realLog;
  v.RESULTS.length = 0;
});

const NOW = new Date("2026-11-27T12:00:00Z");
const hoursAgo = (h: number) => new Date(NOW.getTime() - h * 3_600_000).toISOString();
const ITEM_IDS = v.EXPECTED_ITEM_IDS;

type Route = (url: URL) => unknown;

/** A fake fetch: each route maps a pathname to a JSON body; unknown paths are 404. */
function fakeFetch(routes: Record<string, Route>, seen: string[] = []): v.FetchLike {
  return async (raw) => {
    const url = new URL(raw);
    seen.push(url.pathname + url.search);
    const route = routes[url.pathname];
    if (!route) return { ok: false, status: 404, text: async () => '{"message":"not found"}' };
    const body = route(url);
    return { ok: true, status: 200, text: async () => JSON.stringify(body) };
  };
}

function goodRoot(i: number, session = "cmp-good-1", h = 2) {
  return { traceId: `g${i}`, name: v.TRACE_NAME, sessionId: session, isRootObservation: true, startTime: hoursAgo(h + i / 100), tags: ["grocery-assistant", "compare:good"] };
}
function brokenRoot(i: number, h = 2.1) {
  return { traceId: `b${i}`, name: `chat: hello ${i}`, sessionId: null, isRootObservation: true, startTime: hoursAgo(h + i / 100), tags: ["grocery-assistant", "compare:broken"] };
}

/** A v4 project where everything the acts need is in place. */
function readyRoutes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return {
    "/api/public/projects": () => ({ data: [{ id: "proj1", name: "grocery-assistant" }] }),
    "/api/public/health": () => ({ status: "OK", version: "4.88.0" }),
    [`/api/public/v2/datasets/${v.DATASET_NAME}`]: () => ({ id: "ds1", name: v.DATASET_NAME }),
    "/api/public/dataset-items": () => ({ data: ITEM_IDS.map((id) => ({ id, status: "ACTIVE" })), meta: { totalPages: 1 } }),
    "/api/public/v2/observations": (url) => {
      const traceId = url.searchParams.get("traceId");
      if (traceId?.startsWith("g")) return { data: [{ type: "SPAN" }, { type: "GENERATION", input: '{"x":1}' }], meta: {} };
      if (traceId?.startsWith("b")) return { data: [{ type: "SPAN" }, { type: "GENERATION", input: "null" }], meta: {} };
      return {
        data: [goodRoot(0), goodRoot(1), brokenRoot(0), brokenRoot(1), goodRoot(0, "cmp-collapsed-1", 3)],
        meta: {},
      };
    },
    "/api/public/experiments": () => ({ data: [{ name: "deterministic-board-2026-11-27T10-00-00-000Z" }], meta: {} }),
    "/api/public/v3/scores": () => ({ data: [{ name: v.SESSION_SCORE, subject: { kind: "session", id: "cmp-good-1" } }], meta: {} }),
    ...overrides,
  };
}

/** A demo folder with a .env, the manifest and installed dependencies. */
function demoDir(env = "LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk-file\nLANGFUSE_BASE_URL=https://lf.example\nANTHROPIC_API_KEY=x\n") {
  const dir = mkdtempSync(join(tmpdir(), "grocery-verify-"));
  if (env !== "") writeFileSync(join(dir, ".env"), env);
  writeFileSync(join(dir, "demo.yaml"), "name: grocery-assistant\nlangfuse:\n  target: either\n  project: grocery-assistant\n");
  mkdirSync(join(dir, "node_modules", "@langfuse", "vercel-ai-sdk"), { recursive: true });
  return dir;
}

// ------------------------------------------------------------------ tests ---
test("check records PASS, FAIL and WARN and prints one line each", () => {
  assert.equal(v.check(true, "a"), true);
  assert.equal(v.check(false, "b"), false);
  assert.equal(v.check(false, "c", { required: false, hint: "do x" }), false);
  assert.deepEqual(v.RESULTS.map((r) => r.status), ["PASS", "FAIL", "WARN"]);
  assert.equal(logged[0], "  PASS  a");
  assert.equal(logged[1], "  FAIL  b");
  assert.equal(logged[2], "  WARN  c  ->  do x");
});

test("summary is READY when only optional checks fail, NOT READY otherwise", () => {
  v.check(true, "a");
  v.check(false, "b", { required: false });
  assert.deepEqual(v.summary(), ["READY (1/2 checks passed, 1 warning)", 0]);
  v.check(false, "c");
  v.check(false, "d", { required: false });
  assert.deepEqual(v.summary(), ["NOT READY (1/4 checks passed, 1 required failed, 2 warnings)", 1]);
});

test("parseEnv handles comments, quotes and export", () => {
  const text = "# comment\n\nexport A=1\nB=\"two words\"  \nC=three # trailing\nD='x # y'\nE=\nnot a line\n";
  assert.deepEqual(v.parseEnv(text), { A: "1", B: "two words", C: "three", D: "x # y", E: "" });
});

test("the demo's .env overrides shell LANGFUSE_* and drops the ones it does not set", () => {
  const dir = demoDir("LANGFUSE_PUBLIC_KEY=pk-file\n");
  const env: NodeJS.ProcessEnv = { LANGFUSE_PUBLIC_KEY: "pk-shell", LANGFUSE_HOST: "https://shell.example", OTHER: "kept" };
  assert.equal(v.loadEnv(join(dir, ".env"), env), true);
  assert.deepEqual(env, { LANGFUSE_PUBLIC_KEY: "pk-file", OTHER: "kept" });
});

test("a missing .env still drops shell LANGFUSE_* keys", () => {
  const env: NodeJS.ProcessEnv = { LANGFUSE_SECRET_KEY: "sk-shell", OTHER: "kept" };
  assert.equal(v.loadEnv(join(tmpdir(), "no-such-dir", ".env"), env), false);
  assert.deepEqual(env, { OTHER: "kept" });
});

test("manifestValue reads nested and top-level keys", () => {
  const text = "name: grocery-assistant  # c\nlangfuse:\n  target: either\n  project: \"grocery-assistant\"\ncommands:\n  run: npm run demo\n";
  assert.equal(v.manifestValue(text, "langfuse", "project"), "grocery-assistant");
  assert.equal(v.manifestValue(text, "", "name"), "grocery-assistant");
  assert.equal(v.manifestValue(text, "commands", "run"), "npm run demo");
  assert.equal(v.manifestValue(text, "langfuse", "missing"), undefined);
});

test("expected dataset items follow seed-dataset.ts: one per conversation", () => {
  assert.equal(ITEM_IDS.length, 5);
  assert.ok(ITEM_IDS.every((id) => id.startsWith("grocery-")));
  assert.ok(ITEM_IDS.includes("grocery-unverified-cart-claim"));
});

test("apiFlavour falls back to v1 observations on a 3.x server only", () => {
  assert.equal(v.apiFlavour("3.221.1"), "v1");
  assert.equal(v.apiFlavour("4.88.0"), "v2");
  assert.equal(v.apiFlavour(undefined), "v2");
});

test("sessionOf reads the v4 column first, then the v3 OTel attribute", () => {
  assert.equal(v.sessionOf({ sessionId: "s4" }), "s4");
  assert.equal(v.sessionOf({ metadata: { attributes: { "session.id": "s3" } } }), "s3");
  assert.equal(v.sessionOf({ sessionId: "", metadata: { attributes: {} } }), undefined);
});

test("isPresent treats JSON-string null and empty as absent", () => {
  assert.equal(v.isPresent(null), false);
  assert.equal(v.isPresent("null"), false);
  assert.equal(v.isPresent("  "), false);
  assert.equal(v.isPresent('{"a":1}'), true);
  assert.equal(v.isPresent("plain answer"), true);
  assert.equal(v.isPresent({ a: 1 }), true);
});

test("classifyRoots finds the newest good, collapsed and broken runs", () => {
  const roots = [
    goodRoot(0, "cmp-good-old", 30), goodRoot(1, "cmp-good-new", 1), goodRoot(2, "cmp-good-new", 1),
    goodRoot(0, "cmp-collapsed-new", 1), brokenRoot(0), brokenRoot(1),
    { ...goodRoot(9, "chat-session", 0.5) }, // a standalone `npm run chat`: not a comparison
  ].map((o) => v.toRoot(o)!);
  const runs = v.classifyRoots(roots);
  assert.equal(runs.good?.sessionId, "cmp-good-new");
  assert.equal(runs.good?.turns.length, 2);
  assert.equal(runs.collapsed?.sessionId, "cmp-collapsed-new");
  assert.equal(runs.broken?.turns.length, 2);
  assert.equal(runs.broken?.latest.traceId, "b0");
});

test("classifyRoots finds broken roots by name on a v3 server, where roots carry no tags", () => {
  const v1Rows = [
    { traceId: "b1", name: "chat: what do I usually buy", parentObservationId: null, startTime: hoursAgo(1) },
    { traceId: "g1", name: v.TRACE_NAME, parentObservationId: null, startTime: hoursAgo(1), metadata: { attributes: { "session.id": "cmp-good-9" } } },
    { traceId: "g1", name: "ai.generateText", parentObservationId: "x", startTime: hoursAgo(1) },
  ];
  const roots = v1Rows.map(v.toRoot).filter((r): r is v.Root => r !== undefined);
  assert.equal(roots.length, 2);
  const runs = v.classifyRoots(roots);
  assert.equal(runs.broken?.latest.traceId, "b1");
  assert.equal(runs.good?.sessionId, "cmp-good-9");
});

test("a ready v4 project prints READY, exits 0 and prints a link per act", async () => {
  const seen: string[] = [];
  const env: NodeJS.ProcessEnv = { LANGFUSE_PUBLIC_KEY: "pk-shell-other-project" };
  const code = await v.main(["--links"], { fetchImpl: fakeFetch(readyRoutes(), seen), env, demoDir: demoDir(), now: NOW, nodeVersion: "22.11.0" });
  const out = logged.join("\n");
  assert.equal(code, 0, out);
  assert.match(out, /READY \(\d+\/\d+ checks passed\)$/);
  assert.equal(env["LANGFUSE_PUBLIC_KEY"], "pk-file", "the shell key must not win");
  assert.match(out, /Act 1 · the good session\s+https:\/\/lf\.example\/project\/proj1\/sessions\/cmp-good-1/);
  assert.match(out, /Act 3 · grocery-conversations\s+https:\/\/lf\.example\/project\/proj1\/datasets\/ds1/);
  assert.ok(seen.every((p) => !p.startsWith("/api/public/traces")), "no legacy trace endpoints");
  assert.ok(seen.some((p) => p.startsWith("/api/public/v2/observations")));
  assert.ok(seen.some((p) => p.startsWith("/api/public/v3/scores")));
});

test("a missing dataset item or comparison run is NOT READY", async () => {
  const routes = readyRoutes({
    "/api/public/dataset-items": () => ({ data: ITEM_IDS.slice(1).map((id) => ({ id })), meta: { totalPages: 1 } }),
    "/api/public/v2/observations": () => ({ data: [], meta: {} }),
  });
  const code = await v.main([], { fetchImpl: fakeFetch(routes), env: {}, demoDir: demoDir(), now: NOW, nodeVersion: "22.0.0" });
  const out = logged.join("\n");
  assert.equal(code, 1);
  assert.match(out, /FAIL {2}dataset grocery-conversations: 4\/5 conversation items .*missing grocery-unverified-cart-claim/);
  assert.match(out, /FAIL {2}a 'cmp-good-\*' session/);
  assert.match(out, /FAIL {2}a compare:broken run/);
  assert.match(out, /WARN {2}a 'cmp-collapsed-\*' session/);
});

test("a broken run that records inputs fails: Act 1 would have nothing to show", async () => {
  const routes = readyRoutes();
  const obs = routes["/api/public/v2/observations"]!;
  routes["/api/public/v2/observations"] = (url) =>
    url.searchParams.get("traceId")?.startsWith("b") ? { data: [{ type: "GENERATION", input: '{"messages":[]}' }], meta: {} } : obs(url);
  const code = await v.main([], { fetchImpl: fakeFetch(routes), env: {}, demoDir: demoDir(), now: NOW, nodeVersion: "22.0.0" });
  assert.equal(code, 1);
  assert.match(logged.join("\n"), /FAIL {2}its latest turn has 1 generation\(s\), 0 with null input/);
});

test("keys for another project stop the run before any data read", async () => {
  const seen: string[] = [];
  const routes = readyRoutes({ "/api/public/projects": () => ({ data: [{ id: "p2", name: "real-estate" }] }) });
  const code = await v.main([], { fetchImpl: fakeFetch(routes, seen), env: {}, demoDir: demoDir(), now: NOW, nodeVersion: "22.0.0" });
  assert.equal(code, 1);
  assert.match(logged.join("\n"), /FAIL {2}keys map to project 'real-estate' \(want 'grocery-assistant'\)/);
  assert.ok(!seen.some((p) => p.includes("observations")));
});

test("a 3.x server reads v1 observations and dataset runs, never /traces", async () => {
  const seen: string[] = [];
  const routes: Record<string, Route> = {
    ...readyRoutes(),
    "/api/public/health": () => ({ version: "3.221.1" }),
    "/api/public/observations": (url) => {
      const traceId = url.searchParams.get("traceId");
      if (traceId === "g1") return { data: [{ type: "SPAN" }, { type: "GENERATION", input: { a: 1 } }], meta: { totalPages: 1 } };
      if (traceId === "b1") return { data: [{ type: "GENERATION", input: null }], meta: { totalPages: 1 } };
      return {
        data: [
          { traceId: "g1", name: v.TRACE_NAME, parentObservationId: null, startTime: hoursAgo(1), metadata: { attributes: { "session.id": "cmp-good-7" } } },
          { traceId: "b1", name: "chat: hi", parentObservationId: null, startTime: hoursAgo(1.1) },
        ],
        meta: { totalPages: 1 },
      };
    },
    [`/api/public/datasets/${v.DATASET_NAME}/runs`]: () => ({ data: [{ name: "deterministic-board-x" }], meta: { totalPages: 1 } }),
    "/api/public/v3/scores": () => ({ data: [], meta: {} }),
  };
  delete routes["/api/public/v2/observations"];
  const code = await v.main([], { fetchImpl: fakeFetch(routes, seen), env: {}, demoDir: demoDir(), now: NOW, nodeVersion: "22.0.0" });
  const out = logged.join("\n");
  assert.equal(code, 0, out);
  assert.match(out, /reading Observations v1/);
  assert.match(out, /WARN {2}conversation-cart-integrity on session cmp-good-7/);
  assert.ok(seen.every((p) => !p.startsWith("/api/public/traces")), "no legacy trace endpoints");
  assert.ok(!seen.some((p) => p.startsWith("/api/public/v2/observations")));
});

test("no .env and an old Node each fail with a fix", async () => {
  const dir = demoDir("");
  const code = await v.main([], { fetchImpl: fakeFetch({}), env: { LANGFUSE_PUBLIC_KEY: "pk-shell" }, demoDir: dir, now: NOW, nodeVersion: "20.9.0" });
  const out = logged.join("\n");
  assert.equal(code, 1);
  assert.match(out, /FAIL {2}env loaded from .*->  run \.\/scripts\/provision-project\.sh, or cp \.env\.example \.env/);
  assert.match(out, /FAIL {2}LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY set/);
  assert.match(out, /FAIL {2}Node 20\.9\.0 \(want 22\+\)/);
});
