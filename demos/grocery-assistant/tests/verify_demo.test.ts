/**
 * verify_demo.ts offline: results, summary and exit code, the .env rule, the manifest
 * parse, run classification, and full runs against a contract-strict fake Langfuse
 * (tests/fake-langfuse.ts) on a v4 and a 3.x server. No network, no Langfuse, no
 * model calls, no keys.
 */
import { strict as assert } from "node:assert";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, test } from "node:test";

import * as v from "../scripts/verify_demo.js";
import {
  DATASET_ITEMS_PARAMS,
  EXPERIMENTS_PARAMS,
  EXPERIMENT_ITEMS_PARAMS,
  SCORES_V3_PARAMS,
  V1_OBS_PARAMS,
  V2_OBS_PARAMS,
  fakeLangfuse,
  projectObservation,
  projectScore,
  type FakeOptions,
  type Spec,
} from "./fake-langfuse.js";

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
const out = () => logged.join("\n");

const NOW = new Date("2026-11-27T12:00:00Z");
const hoursAgo = (h: number) => new Date(NOW.getTime() - h * 3_600_000).toISOString();
const ITEMS = v.EXPECTED_ITEMS;

type Row = Record<string, unknown>;

function goodRoot(i: number, session = "cmp-good-1", h = 2): Row {
  return {
    id: `og${i}-${session}`, traceId: `g${i}-${session}`, type: "SPAN", name: v.TRACE_NAME, sessionId: session,
    isRootObservation: true, parentObservationId: null, startTime: hoursAgo(h + i / 100), tags: ["grocery-assistant", "compare:good"],
  };
}
function brokenRoot(i: number, h = 2.1): Row {
  return {
    id: `ob${i}`, traceId: `b${i}`, type: "SPAN", name: `chat: hello ${i}`, sessionId: null,
    isRootObservation: true, parentObservationId: null, startTime: hoursAgo(h + i / 100), tags: ["grocery-assistant", "compare:broken"],
  };
}

type World = {
  version: string;
  projects: Row[];
  datasetItems: Row[];
  roots: Row[];
  traceObs: (traceId: string) => Row[];
  experiments: Row[];
  experimentItems: Row[];
  scores: Row[];
  v1Runs: Row[];
  v1RunItems: Row[];
};

function readyWorld(over: Partial<World> = {}): World {
  return {
    version: "4.88.0",
    projects: [{ id: "proj1", name: "grocery-assistant" }],
    datasetItems: ITEMS.map((i) => ({ ...i, status: "ACTIVE" })),
    roots: [goodRoot(0), goodRoot(1), brokenRoot(0), brokenRoot(1), goodRoot(0, "cmp-collapsed-1", 3)],
    traceObs: (traceId) =>
      traceId.startsWith("g")
        ? [{ id: "s", traceId, type: "SPAN", input: '{"message":"hi"}' }, { id: "gen", traceId, type: "GENERATION", input: '{"x":1}' }]
        : [{ id: "s", traceId, type: "SPAN" }, { id: "gen", traceId, type: "GENERATION", input: "null" }],
    experiments: [
      { id: "exp-old", name: "deterministic-board-2026-11-20", startTime: hoursAgo(200), itemCount: 5, datasetId: "ds1" },
      { id: "exp1", name: "deterministic-board-2026-11-27", startTime: hoursAgo(5), itemCount: 5, datasetId: "ds1" },
      { id: "other", name: "someone-elses-run", startTime: hoursAgo(1), itemCount: 1, datasetId: "ds1" },
    ],
    experimentItems: ITEMS.map((i, n) => ({ id: `ei${n}`, traceId: `t${n}`, experimentId: "exp1", experimentItemId: i.id, scores: [{ name: "unverified-cart-claim" }] })),
    scores: [{ id: "sc1", name: v.SESSION_SCORE, value: 1, dataType: "NUMERIC", subject: { kind: "session", id: "cmp-good-1" } }],
    v1Runs: [{ name: "deterministic-board-x", createdAt: hoursAgo(5) }],
    v1RunItems: ITEMS.map((i) => ({ datasetItemId: i.id })),
    ...over,
  };
}

function routes(w: World): Record<string, Spec> {
  return {
    "/api/public/projects": { params: [], rows: () => ({ data: w.projects }) },
    "/api/public/health": { params: [], rows: () => ({ status: "OK", version: w.version }) },
    "/api/public/v2/datasets/{name}": {
      params: [],
      rows: (_q, path) => (path.endsWith(`/${v.DATASET_NAME}`) ? { id: "ds1", name: v.DATASET_NAME } : undefined),
    },
    "/api/public/dataset-items": { params: DATASET_ITEMS_PARAMS, required: ["datasetName"], paging: "page", rows: () => w.datasetItems },
    "/api/public/v2/observations": {
      params: V2_OBS_PARAMS,
      maxLimit: 1000,
      fieldGroups: ["core", "basic", "time", "io", "metadata", "model", "usage", "prompt", "metrics", "trace_context"],
      paging: "cursor",
      project: projectObservation,
      rows: (q) => {
        if (w.version.startsWith("3.")) return undefined; // "only available in a Langfuse v4 write mode"
        const traceId = q.get("traceId");
        if (traceId) return w.traceObs(traceId);
        return w.roots.filter((r) => q.get("isRootObservation") !== "true" || r.isRootObservation);
      },
    },
    "/api/public/experiments": {
      params: EXPERIMENTS_PARAMS, required: ["fromStartTime"], maxLimit: 100, fieldGroups: ["core", "metadata", "scores"], paging: "cursor",
      rows: (q) => (w.version.startsWith("3.") ? undefined : w.experiments.filter((e) => !q.get("datasetId") || e.datasetId === q.get("datasetId"))),
    },
    "/api/public/experiment-items": {
      params: EXPERIMENT_ITEMS_PARAMS, required: ["fromStartTime"], maxLimit: 100,
      fieldGroups: ["core", "dataset", "io", "metadata", "itemMetadata", "experimentMetadata", "scores"], paging: "cursor",
      project: (row, groups) => (groups.has("scores") ? row : { ...row, scores: undefined }),
      rows: (q) => w.experimentItems.filter((i) => i.experimentId === q.get("experimentId")),
    },
    "/api/public/v3/scores": {
      params: SCORES_V3_PARAMS, maxLimit: 100, fieldGroups: ["details", "subject", "annotation"], paging: "cursor", project: projectScore,
      rows: (q) =>
        w.scores.filter(
          (s) =>
            (!q.get("name") || q.get("name")!.split(",").includes(String(s.name))) &&
            (!q.get("sessionId") || (s.subject as { id?: string } | undefined)?.id === q.get("sessionId")),
        ),
    },
    // 3.x server only
    "/api/public/observations": {
      params: V1_OBS_PARAMS, paging: "page",
      rows: (q) => {
        if (!w.version.startsWith("3.")) return [];
        const traceId = q.get("traceId");
        if (traceId) return w.traceObs(traceId);
        return w.roots.map((r) => ({
          ...r, sessionId: undefined, tags: undefined, isRootObservation: undefined,
          metadata: r.sessionId ? { attributes: { "session.id": r.sessionId } } : {},
        }));
      },
    },
    "/api/public/datasets/{name}/runs": { params: ["page", "limit"], paging: "page", rows: () => w.v1Runs },
    "/api/public/datasets/{name}/runs/{run}": { params: [], rows: () => ({ name: "deterministic-board-x", datasetRunItems: w.v1RunItems }) },
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

async function run(w: World, argv: string[] = [], opts: FakeOptions = {}, env: NodeJS.ProcessEnv = {}) {
  const fake = fakeLangfuse(routes(w), opts);
  const code = await v.main(argv, { fetchImpl: fake.fetch, env, demoDir: demoDir(), now: NOW, nodeVersion: "22.11.0" });
  return { code, fake };
}

// ------------------------------------------------------- unit: helpers ---
test("check records PASS, FAIL and WARN and prints one line each", () => {
  assert.equal(v.check(true, "a"), true);
  assert.equal(v.check(false, "b"), false);
  assert.equal(v.check(false, "c", { required: false, hint: "do x" }), false);
  assert.deepEqual(v.RESULTS.map((r) => r.status), ["PASS", "FAIL", "WARN"]);
  assert.deepEqual(logged, ["  PASS  a", "  FAIL  b", "  WARN  c  ->  do x"]);
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

test("expected dataset items are the seeder's: one per conversation, on stable ids", () => {
  assert.equal(ITEMS.length, 5);
  assert.ok(v.EXPECTED_ITEM_IDS.includes("grocery-unverified-cart-claim"));
});

test("itemProblems flags missing, archived and drifted items", () => {
  const rows: v.ItemRow[] = ITEMS.map((i) => ({ ...i }));
  assert.deepEqual(v.itemProblems(rows), []);
  rows[0] = { ...rows[0]!, status: "ARCHIVED" };
  rows[1] = { ...rows[1]!, input: { turns: ["changed"] } };
  rows.pop();
  assert.deepEqual(v.itemProblems(rows), [
    `${ITEMS[0]!.id} ARCHIVED`,
    `${ITEMS[1]!.id} differs from src/conversations.ts`,
    `${ITEMS[4]!.id} missing`,
  ]);
});

test("isPresent treats JSON-string null and empty as absent", () => {
  assert.equal(v.isPresent(null), false);
  assert.equal(v.isPresent("null"), false);
  assert.equal(v.isPresent("  "), false);
  assert.equal(v.isPresent('{"a":1}'), true);
  assert.equal(v.isPresent("plain answer"), true);
});

test("classifyRoots finds the newest good, collapsed and broken runs", () => {
  const roots = [
    goodRoot(0, "cmp-good-old", 30), goodRoot(1, "cmp-good-new", 1), goodRoot(2, "cmp-good-new", 1),
    goodRoot(0, "cmp-collapsed-new", 1), brokenRoot(0), brokenRoot(1), goodRoot(9, "chat-session", 0.5),
  ].map((o) => v.toRoot(o)!);
  const runs = v.classifyRoots(roots);
  assert.equal(runs.good?.sessionId, "cmp-good-new");
  assert.equal(runs.good?.turns.length, 2);
  assert.equal(runs.collapsed?.sessionId, "cmp-collapsed-new");
  assert.equal(runs.broken?.turns.length, 2);
  assert.equal(runs.broken?.latest.traceId, "b0");
});

// ------------------------------------------------------- full runs: v4 ---
test("a ready v4 project prints READY, exits 0 and prints a link per act", async () => {
  const env: NodeJS.ProcessEnv = { LANGFUSE_PUBLIC_KEY: "pk-shell-other-project", LANGFUSE_BASE_URL: "https://shell.example" };
  const { code, fake } = await run(readyWorld(), ["--links"], {}, env);
  assert.equal(code, 0, out());
  assert.match(out(), /READY \(\d+\/\d+ checks passed\)$/);
  assert.equal(env["LANGFUSE_PUBLIC_KEY"], "pk-file", "the shell key must not win");
  assert.deepEqual(fake.rejected, [], "every request honours the API contract");
  assert.ok(fake.seen.every((p) => !p.includes("shell.example")));
  assert.match(out(), /PASS {2}newest run deterministic-board-2026-11-27: 5\/5 conversation items, 5 score\(s\)/);
  assert.match(out(), /PASS {2}conversation-cart-integrity on session cmp-good-1: 1 score\(s\)/);
  assert.match(out(), /Act 1 · the good session\s+https:\/\/lf\.example\/project\/proj1\/sessions\/cmp-good-1/);
  assert.match(out(), /Act 3 · grocery-conversations\s+https:\/\/lf\.example\/project\/proj1\/datasets\/ds1/);
  assert.ok(fake.seen.every((p) => !p.startsWith("/api/public/traces")), "no legacy trace endpoints");
});

test("Scores v3 is read with fields=subject and the session filter (135-1)", async () => {
  const { fake } = await run(readyWorld());
  const scoreReads = fake.seen.filter((p) => p.startsWith("/api/public/v3/scores"));
  assert.equal(scoreReads.length, 1);
  const q = new URL(`http://x${scoreReads[0]}`).searchParams;
  assert.equal(q.get("fields"), "subject");
  assert.equal(q.get("sessionId"), "cmp-good-1");
  assert.equal(q.get("name"), v.SESSION_SCORE);
});

test("the fake rejects the old fields=core,subject, as the real API does", async () => {
  const fake = fakeLangfuse(routes(readyWorld()));
  const res = await fake.fetch("https://lf.example/api/public/v3/scores?fields=core,subject", { headers: {} });
  assert.equal(res.status, 400);
});

test("an optional read that fails is a WARN, and the verdict stays READY (135-1)", async () => {
  const { code } = await run(readyWorld(), [], { fail: { "/api/public/v3/scores": 500 } });
  assert.equal(code, 0, out());
  assert.match(out(), /Act 4 · session score[^\n]*\n {2}WARN {2}read failed: HTTP 500/);
});

test("a required read that fails is a FAIL, and the other acts still run", async () => {
  const { code } = await run(readyWorld(), [], { fail: { "/api/public/v2/observations": 500 } });
  assert.equal(code, 1);
  assert.match(out(), /Act 1 · trace shape[^\n]*\n {2}FAIL {2}read failed: HTTP 500/);
  assert.match(out(), /PASS {2}newest run deterministic-board/, "Act 3 still ran");
  assert.match(out(), /NOT READY/);
});

test("no dataset run is NOT READY on full prep, and only warns with --quick (135-3)", async () => {
  const world = readyWorld({ experiments: [] });
  const full = await run(world);
  assert.equal(full.code, 1);
  assert.match(out(), /Scope: full prep/);
  assert.match(out(), /FAIL {2}a deterministic-board-\* run on grocery-conversations/);
  logged = [];
  v.RESULTS.length = 0;
  const quick = await run(world, ["--quick"]);
  assert.equal(quick.code, 0, out());
  assert.match(out(), /Scope: quick prep/);
  assert.match(out(), /WARN {2}a deterministic-board-\* run/);
});

test("a dataset run missing an item, or with no scores, is NOT READY", async () => {
  const partial = readyWorld();
  partial.experimentItems = partial.experimentItems.slice(1);
  assert.equal((await run(partial)).code, 1);
  assert.match(out(), /FAIL {2}newest run deterministic-board-2026-11-27: 4\/5 conversation items/);
  logged = [];
  const unscored = readyWorld();
  unscored.experimentItems = unscored.experimentItems.map((i) => ({ ...i, scores: [] }));
  assert.equal((await run(unscored)).code, 1);
  assert.match(out(), /FAIL {2}newest run [^\n]*, 0 score\(s\)/);
});

test("an archived-only dataset is NOT READY", async () => {
  const { code } = await run(readyWorld({ datasetItems: ITEMS.map((i) => ({ ...i, status: "ARCHIVED" })) }));
  assert.equal(code, 1);
  assert.match(out(), /FAIL {2}dataset grocery-conversations: 0\/5 conversation items active/);
});

test("a missing dataset names the seed command, and Act 3 then has no run", async () => {
  const fake = fakeLangfuse(routes(readyWorld()), { fail: { [`/api/public/v2/datasets/${v.DATASET_NAME}`]: 404 } });
  const code = await v.main([], { fetchImpl: fake.fetch, env: {}, demoDir: demoDir(), now: NOW, nodeVersion: "22.0.0" });
  assert.equal(code, 1);
  assert.match(out(), /FAIL {2}dataset grocery-conversations not found {2}->  run npm run seed:dataset/);
  assert.match(out(), /FAIL {2}a deterministic-board-\* run on grocery-conversations/);
});

test("empty cursor pages are followed, not taken for the end", async () => {
  const { code, fake } = await run(readyWorld(), [], { emptyPagesFirst: 2, pageSize: 1 });
  assert.equal(code, 0, out());
  assert.ok(fake.seen.some((p) => p.includes("cursor=e1")) && fake.seen.some((p) => p.includes("cursor=c0")));
});

test("a repeated cursor is a failed read, not an endless loop", async () => {
  const { code } = await run(readyWorld(), [], { repeatCursor: true });
  assert.equal(code, 1);
  assert.match(out(), /read failed: [^\n]*returned cursor same twice/);
});

test("a missing comparison run is NOT READY", async () => {
  const { code } = await run(readyWorld({ roots: [] }));
  assert.equal(code, 1);
  assert.match(out(), /FAIL {2}a 'cmp-good-\*' session/);
  assert.match(out(), /FAIL {2}a compare:broken run/);
  assert.match(out(), /WARN {2}a 'cmp-collapsed-\*' session/);
});

test("a broken run that records inputs fails: Act 1 would have nothing to show", async () => {
  const world = readyWorld();
  const good = world.traceObs;
  world.traceObs = (id) => (id.startsWith("b") ? [{ id: "gen", traceId: id, type: "GENERATION", input: '{"messages":[]}' }] : good(id));
  assert.equal((await run(world)).code, 1);
  assert.match(out(), /FAIL {2}its latest turn has 1 generation\(s\), 0 with null input/);
});

test("keys for another project, or for no project, stop before any data read", async () => {
  for (const projects of [[{ id: "p2", name: "real-estate" }], []]) {
    logged = [];
    v.RESULTS.length = 0;
    const { code, fake } = await run(readyWorld({ projects }));
    assert.equal(code, 1);
    assert.match(out(), /FAIL {2}keys map to project '(real-estate|\(none\))' \(want 'grocery-assistant'\)/);
    assert.ok(!fake.seen.some((p) => p.includes("observations") || p.includes("dataset")));
  }
});

test("a failed health read warns and reads Observations v2", async () => {
  const { code } = await run(readyWorld(), [], { fail: { "/api/public/health": 503 } });
  assert.equal(code, 0, out());
  assert.match(out(), /WARN {2}server version unknown \(HTTP 503/);
});

// ------------------------------------------------------ full runs: 3.x ---
test("a 3.x server reads v1 observations and dataset runs, never /traces or v2", async () => {
  const world = readyWorld({ version: "3.221.1", scores: [] });
  const { code, fake } = await run(world);
  assert.equal(code, 0, out());
  assert.deepEqual(fake.rejected, []);
  assert.match(out(), /reading Observations v1/);
  assert.match(out(), /PASS {2}newest run deterministic-board-x: 5\/5 conversation items \(scores not read on a 3\.x server\)/);
  assert.match(out(), /WARN {2}conversation-cart-integrity on session cmp-good-1: 0 score\(s\)/);
  assert.ok(fake.seen.every((p) => !p.startsWith("/api/public/traces")));
  assert.ok(!fake.seen.some((p) => p.startsWith("/api/public/v2/observations") || p.startsWith("/api/public/experiments")));
});

// ------------------------------------------------------- config failures ---
test("a partial .env with hostile shell exports fails before any API call", async () => {
  const fake = fakeLangfuse(routes(readyWorld()));
  const env: NodeJS.ProcessEnv = { LANGFUSE_SECRET_KEY: "sk-shell", LANGFUSE_BASE_URL: "https://shell.example" };
  const code = await v.main([], { fetchImpl: fake.fetch, env, demoDir: demoDir("LANGFUSE_PUBLIC_KEY=pk-file\n"), now: NOW, nodeVersion: "22.0.0" });
  assert.equal(code, 1);
  assert.match(out(), /FAIL {2}LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY set/);
  assert.deepEqual(fake.seen, []);
});

test("no .env and an old Node each fail with a fix, and nothing is read", async () => {
  const fake = fakeLangfuse(routes(readyWorld()));
  const code = await v.main([], {
    fetchImpl: fake.fetch, env: { LANGFUSE_PUBLIC_KEY: "pk-shell", LANGFUSE_SECRET_KEY: "sk-shell" },
    demoDir: demoDir(""), now: NOW, nodeVersion: "20.9.0",
  });
  assert.equal(code, 1);
  assert.match(out(), /FAIL {2}env loaded from .*->  run \.\/scripts\/provision-project\.sh, or cp \.env\.example \.env/);
  assert.match(out(), /FAIL {2}Node 20\.9\.0 \(want 22\+\)/);
  assert.deepEqual(fake.seen, []);
});

test("an unknown option exits 2 with the usage", async () => {
  assert.equal(await v.main(["--linkz"], { env: {}, demoDir: demoDir() }), 2);
  assert.match(out(), /unknown option\(s\): --linkz/);
});
