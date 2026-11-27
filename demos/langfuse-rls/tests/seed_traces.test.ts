// scripts/seed-traces.ts offline, against the fake Langfuse: strict credentials, idempotent
// reseeding with root observations, drift repair, failure propagation, cursor paging, the v3
// fallback, and old-seeder duplicates. The fake defaults to a v4 server.
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { readTraces, V2_LIMIT, V2_MAX_PAGES } from "../lib/trace-read";
import { canonical, type Fixture, fixtureDrift, loadFixtures, parseFixtures } from "../scripts/fixtures";
import { plan, rootEvent, rootObservationId, seed, traceEvent } from "../scripts/seed-traces";
import { ENV_TEXT, FakeLangfuse, HOSTILE_SHELL, KEYS, LF } from "./fake_langfuse";

const FIXTURES = loadFixtures();
const MANIFEST = "name: langfuse-rls\nlangfuse:\n  project: RLS Demo\n";
const NOW = new Date("2026-11-27T10:00:00.000Z");
const SEEDED_AT = "2026-11-01T00:00:00.000Z";

function envFile(text: string | null): string {
  const path = join(mkdtempSync(join(tmpdir(), "rls-seed-")), ".env");
  if (text !== null) writeFileSync(path, text);
  return path;
}

let lf: FakeLangfuse;
beforeEach(() => {
  lf = new FakeLangfuse();
});

async function runSeed(opts: { env?: string | null; shell?: Record<string, string>; argv?: string[]; fixtures?: Fixture[] } = {}) {
  const out: string[] = [];
  const code = await seed({
    argv: opts.argv ?? [], envPath: envFile(opts.env === undefined ? ENV_TEXT : opts.env), manifest: MANIFEST,
    env: { ...(opts.shell ?? {}) }, fixtures: opts.fixtures ?? FIXTURES, fetch: lf.fetch, out: (l) => out.push(l), now: NOW,
  });
  return { code, out, text: out.join("\n") };
}

/** The project as the current seeder leaves it: 30 traces, each with its root observation. */
function seeded(): void {
  for (const f of FIXTURES) lf.addWithRoot({ ...f, timestamp: SEEDED_AT });
}

/** The project as the old seeder left it: trace-create only, no observations. */
function seededByOldSeeder(): void {
  for (const f of FIXTURES) lf.add({ ...f, timestamp: SEEDED_AT });
}

/** What the app would read now. */
async function appView() {
  return readTraces({ baseUrl: LF, publicKey: KEYS.pk, secretKey: KEYS.sk, fetch: lf.fetch }, { now: NOW });
}

type Ev = { type: string; body: { id: string; traceId?: string; timestamp?: string; startTime?: string } };
function batches(): Ev[][] {
  return lf.writes().filter((c) => c.path === "/api/public/ingestion").map((c) => (c.body as { batch: Ev[] }).batch);
}

describe("the committed fixtures", () => {
  it("have stable ids rls-demo-01..30 and pass validation", () => {
    expect(FIXTURES.map((f) => f.id)).toEqual(Array.from({ length: 30 }, (_, i) => `rls-demo-${String(i + 1).padStart(2, "0")}`));
  });

  it("reject a duplicate id, a missing tag and an unknown classification", () => {
    const one = FIXTURES[0];
    expect(() => parseFixtures(JSON.stringify([one, one]))).toThrow(/duplicate id rls-demo-01/);
    expect(() => parseFixtures(JSON.stringify([{ ...one, tags: [] }]))).toThrow(/tags must include rls-demo/);
    expect(() => parseFixtures(JSON.stringify([{ ...one, metadata: { ...one.metadata, classification: "secret" } }])))
      .toThrow(/known classification/);
    expect(() => parseFixtures(JSON.stringify([{ ...one, id: "abc" }]))).toThrow(/rls-demo-NN/);
  });
});

describe("credentials", () => {
  it("fails before any request when .env is missing, even with valid keys exported in the shell", async () => {
    const { code, text } = await runSeed({ env: null, shell: HOSTILE_SHELL });
    expect(code).toBe(1);
    expect(text).toMatch(/not found/);
    expect(lf.calls).toHaveLength(0);
  });

  it("fails before any request when .env leaves a key out, and does not borrow it from the shell", async () => {
    const { code, text } = await runSeed({ env: ENV_TEXT.replace(/LANGFUSE_SECRET_KEY=.*\n/, ""), shell: HOSTILE_SHELL });
    expect(code).toBe(1);
    expect(text).toMatch(/does not set LANGFUSE_SECRET_KEY/);
    expect(lf.calls).toHaveLength(0);
  });

  it("writes nothing when the keys map to another project", async () => {
    lf.project.name = "LLM Observability Demo";
    const { code, text } = await runSeed();
    expect(code).toBe(1);
    expect(text).toMatch(/map to project 'LLM Observability Demo', want 'RLS Demo'/);
    expect(lf.writes()).toHaveLength(0);
  });

  it("writes nothing on wrong keys (401)", async () => {
    const { code } = await runSeed({ env: ENV_TEXT.replace("sk-lf-demo", "sk-wrong") });
    expect(code).toBe(1);
    expect(lf.writes()).toHaveLength(0);
  });
});

describe("idempotent seeding (v4 server, Observations v2)", () => {
  it("creates all 30 with a root observation each, and the app reads back the fixture content", async () => {
    const { code, text } = await runSeed();
    expect(code).toBe(0);
    expect(text).toMatch(/Done: 30 created, 0 updated, 0 unchanged, 0 failed\./);
    expect(text).toMatch(/up to 15 minutes/);
    expect(lf.writes().map((c) => c.path)).toEqual(["/api/public/ingestion"]);
    const [batch] = batches();
    expect(batch).toHaveLength(60);
    expect(batch.filter((e) => e.type === "span-create").map((e) => e.body.id)).toEqual(FIXTURES.map((f) => rootObservationId(f.id)));
    const read = await appView();
    expect(read.source).toBe("observations-v2");
    expect(read.traces).toHaveLength(30);
    for (const f of FIXTURES) {
      const t = read.traces.find((x) => x.id === f.id)!;
      expect(fixtureDrift(f, t)).toEqual([]);
      expect(t.rootObservationId).toBe(rootObservationId(f.id));
    }
    expect(lf.reads("/api/public/traces")).toHaveLength(0);
  });

  it("writes nothing on two unchanged reruns, and the project still holds exactly 30 traces and 30 roots", async () => {
    await runSeed();
    const writesAfterFirst = lf.writes().length;
    for (let i = 0; i < 2; i++) {
      const { code, text } = await runSeed();
      expect(code).toBe(0);
      expect(text).toMatch(/Done: 0 created, 0 updated, 30 unchanged, 0 failed\./);
    }
    expect(lf.writes()).toHaveLength(writesAfterFirst);
    expect(lf.traces.size).toBe(30);
    expect(lf.observations.size).toBe(30);
  });

  it("repairs traces the old seeder left without a root observation, then reports them unchanged", async () => {
    seededByOldSeeder();
    const before = await appView();
    expect(before.traces.every((t) => t.metadata === undefined && t.input === undefined)).toBe(true); // the review's case
    const { code, text } = await runSeed();
    expect(code).toBe(0);
    expect(text).toMatch(/updated {4}rls-demo-01 .*\(metadata, input, output, root\)/);
    expect(text).toMatch(/Done: 0 created, 30 updated, 0 unchanged, 0 failed\./);
    for (const f of FIXTURES) {
      expect(lf.traces.get(f.id)!.timestamp).toBe(SEEDED_AT);
      expect(lf.root(f.id)!.startTime).toBe(SEEDED_AT);
    }
    const after = await appView();
    for (const f of FIXTURES) expect(fixtureDrift(f, after.traces.find((t) => t.id === f.id)!)).toEqual([]);
    expect((await runSeed()).text).toMatch(/30 unchanged/);
  });

  it("repairs a drifted root observation in place, names the fields, and keeps its timestamp", async () => {
    seeded();
    const r = lf.root("rls-demo-05")!;
    lf.observations.set(r.id, { ...r, metadata: { ...(r.metadata ?? {}), classification: "general" }, output: { response: "edited" } });
    const { code, text } = await runSeed();
    expect(code).toBe(0);
    expect(text).toMatch(/updated {4}rls-demo-05 .*\(metadata, output\)/);
    expect(text).toMatch(/Done: 0 created, 1 updated, 29 unchanged, 0 failed\./);
    expect(lf.root("rls-demo-05")).toMatchObject({ startTime: SEEDED_AT, output: FIXTURES[4].output });
    expect(lf.traces.get("rls-demo-05")!.timestamp).toBe(SEEDED_AT);
    expect(lf.traces.size).toBe(30);
  });

  it("re-creates a deleted fixture and leaves the others alone", async () => {
    seeded();
    lf.traces.delete("rls-demo-30");
    lf.observations.delete(rootObservationId("rls-demo-30"));
    const { text } = await runSeed();
    expect(text).toMatch(/created {4}rls-demo-30/);
    expect(text).toMatch(/Done: 1 created, 0 updated, 29 unchanged/);
    expect(batches()[0].map((e) => [e.type, e.body.id])).toEqual([["trace-create", "rls-demo-30"], ["span-create", "rls-demo-30-root"]]);
  });

  it("applies a changed fixture definition as an update", async () => {
    seeded();
    const changed = FIXTURES.map((f) => (f.id === "rls-demo-01" ? { ...f, name: "Renamed in the fixtures" } : f));
    const { text } = await runSeed({ fixtures: changed });
    expect(text).toMatch(/updated {4}rls-demo-01 {2}Renamed in the fixtures \(name\)/);
    expect(lf.traces.get("rls-demo-01")!.name).toBe("Renamed in the fixtures");
  });

  it("does not treat a metadata key or tag someone added as drift", async () => {
    seeded();
    const t = lf.traces.get("rls-demo-02")!;
    lf.traces.set("rls-demo-02", { ...t, tags: [...t.tags, "reviewed"] });
    const r = lf.root("rls-demo-02")!;
    lf.observations.set(r.id, { ...r, metadata: { ...(r.metadata ?? {}), note: "x" } });
    const { text } = await runSeed();
    expect(text).toMatch(/30 unchanged/);
  });
});

describe("failures reach the exit code", () => {
  it("a partial write failure names the failed fixtures and exits 1", async () => {
    lf.failTraceIds = new Set(["rls-demo-03", "rls-demo-04"]);
    const { code, text } = await runSeed();
    expect(code).toBe(1);
    expect(text).toMatch(/FAILED {5}rls-demo-03 .*storage error/);
    expect(text).toMatch(/Done: 28 created, 0 updated, 0 unchanged, 2 failed\./);
  });

  it("a total write failure fails every fixture", async () => {
    lf.ingestionStatus = 500;
    const { code, text } = await runSeed();
    expect(code).toBe(1);
    expect(text).toMatch(/0 created, 0 updated, 0 unchanged, 30 failed/);
  });

  it("an event the API neither accepts nor rejects counts as failed", async () => {
    const realFetch = lf.fetch;
    lf.fetch = async (url, init) => (String(url).endsWith("/ingestion")
      ? new Response(JSON.stringify({ successes: [], errors: [] }), { status: 207 }) : realFetch(url, init));
    const { code, text } = await runSeed();
    expect(code).toBe(1);
    expect(text).toMatch(/not acknowledged/);
  });

  it("a failed read writes nothing, and a non-404 v2 failure never falls back to the legacy list", async () => {
    lf.listStatus = 500;
    const { code, text } = await runSeed();
    expect(code).toBe(1);
    expect(text).toMatch(/reading Langfuse: HTTP 500.*Nothing was written/);
    expect(lf.writes()).toHaveLength(0);
    expect(lf.reads("/api/public/traces")).toHaveLength(0);
  });
});

describe("reading the project through Observations v2", () => {
  it("asks for root rows of tagged traces, bounded in time, with the field groups it reads", async () => {
    await runSeed();
    const [call] = lf.reads("/api/public/v2/observations");
    expect(call.params.get("fields")).toBe("core,basic,io,metadata,trace_context");
    expect(call.params.get("expandMetadata")).toBe("classification,team,topic");
    const filter = JSON.parse(call.params.get("filter")!);
    expect(filter).toContainEqual({ type: "boolean", column: "isRootObservation", operator: "=", value: true });
    expect(filter).toContainEqual({ type: "arrayOptions", column: "tags", operator: "all of", value: ["rls-demo"] });
    expect(filter.some((c: { column: string; operator: string }) => c.column === "startTime" && c.operator === ">=")).toBe(true);
  });

  it("follows meta.cursor across pages, including empty pages, and ignores untagged traces", async () => {
    seeded();
    for (let i = 0; i < V2_LIMIT + 200; i++) lf.addWithRoot({ id: `other-${i}`, name: `Other ${i}`, tags: ["rls-demo"] });
    lf.add({ id: "unrelated", name: "someone else's trace", tags: [] });
    lf.v2EmptyPages = 2;
    const { code, text } = await runSeed();
    expect(code).toBe(0);
    expect(text).toMatch(/30 unchanged/);
    expect((text.match(/ kept {7}other-/g) ?? []).length).toBe(V2_LIMIT + 200);
    const cursors = lf.reads("/api/public/v2/observations").map((c) => c.params.get("cursor"));
    expect(cursors).toHaveLength(4); // two empty pages, then two pages of data
    expect(cursors[0]).toBeNull();
  });

  it("stops with an error instead of looping when the cursor repeats", async () => {
    seeded();
    lf.v2RepeatCursor = true;
    const { code, text } = await runSeed();
    expect(code).toBe(1);
    expect(text).toMatch(/repeated cursor/);
    expect(lf.writes()).toHaveLength(0);
    expect(V2_MAX_PAGES).toBeGreaterThan(1);
  });
});

describe("the legacy fallback (self-hosted v3 server)", () => {
  beforeEach(() => {
    lf.server = "v3";
  });

  it("is used only after Observations v2 returns 404, and seeding stays idempotent", async () => {
    const first = await runSeed();
    expect(first.code).toBe(0);
    expect(first.text).toMatch(/Observations v2 returned 404 .*legacy trace list/);
    expect(first.text).toMatch(/Done: 30 created/);
    const second = await runSeed();
    expect(second.text).toMatch(/Done: 0 created, 0 updated, 30 unchanged, 0 failed\./);
    expect(lf.reads("/api/public/v2/observations")).toHaveLength(2); // one 404 per run, then no more v2 calls
    const read = await appView();
    expect(read.source).toBe("legacy-traces");
    for (const f of FIXTURES) expect(fixtureDrift(f, read.traces.find((t) => t.id === f.id)!)).toEqual([]);
  });

  it("follows every legacy page and stops if a page repeats", async () => {
    seeded();
    for (let i = 0; i < 230; i++) lf.add({ id: `other-${i}`, name: `Other ${i}`, tags: ["rls-demo"] });
    const { text } = await runSeed();
    expect(text).toMatch(/30 unchanged/);
    expect(lf.reads("/api/public/traces").map((c) => c.params.get("page"))).toEqual(["1", "2", "3"]);
    lf.repeatPages = true;
    const again = await runSeed();
    expect(again.code).toBe(1);
    expect(again.text).toMatch(/repeated page 2/);
  });
});

describe("old-seeder duplicates", () => {
  function withLegacy(): void {
    seeded();
    FIXTURES.forEach((f, i) => lf.add({ ...f, id: `9f1c-legacy-${i}` })); // same content, random ids, no root
    lf.addWithRoot({ id: "foreign", name: "A tagged trace that is not a fixture", tags: ["rls-demo"] });
    lf.add({ id: "unrelated", name: "Someone else's trace", tags: [] });
  }

  it("are reported and kept by default, and the run exits 1", async () => {
    withLegacy();
    const { code, text } = await runSeed();
    expect(code).toBe(1);
    expect((text.match(/ legacy {5}9f1c-legacy-/g) ?? []).length).toBe(30);
    expect(text).toMatch(/--prune-legacy/);
    expect(lf.writes()).toHaveLength(0);
    expect(lf.traces.size).toBe(62);
  });

  it("are deleted with --prune-legacy, and nothing else is", async () => {
    withLegacy();
    const { code, text } = await runSeed({ argv: ["--prune-legacy"] });
    expect(code).toBe(0);
    const del = lf.writes().filter((c) => c.method === "DELETE");
    expect(del).toHaveLength(1);
    expect((del[0].body as { traceIds: string[] }).traceIds.every((id) => id.startsWith("9f1c-legacy-"))).toBe(true);
    expect([...lf.traces.keys()].sort()).toEqual([...FIXTURES.map((f) => f.id), "foreign", "unrelated"].sort());
    expect(text).toMatch(/30 old-seeder duplicate\(s\) deleted/);
  });

  it("a failed delete exits 1", async () => {
    withLegacy();
    lf.deleteStatus = 500;
    const { code, text } = await runSeed({ argv: ["--prune-legacy"] });
    expect(code).toBe(1);
    expect(text).toMatch(/FAILED {5}deleting 30 old-seeder duplicates/);
  });
});

describe("plan and events", () => {
  it("sorts existing traces into create, update, unchanged, legacy and foreign", () => {
    const [a, b, c] = FIXTURES;
    const p = plan([a, b, c], [
      { ...b, id: b.id, output: { response: "changed" } },
      { ...c },
      { id: "x", name: a.name, tags: ["rls-demo"] },
      { id: "y", name: "unknown", tags: ["rls-demo"] },
    ]);
    expect(p.create.map((f) => f.id)).toEqual([a.id]);
    expect(p.update.map((u) => [u.fixture.id, u.fields])).toEqual([[b.id, ["output"]]]);
    expect(p.unchanged.map((f) => f.id)).toEqual([c.id]);
    expect(p.legacy.map((t) => t.id)).toEqual(["x"]);
    expect(p.foreign.map((t) => t.id)).toEqual(["y"]);
  });

  it("on a v2 read, a trace without its root observation is drift", () => {
    const [a] = FIXTURES;
    expect(plan([a], [{ ...a }], { checkRoot: true }).update.map((u) => u.fields)).toEqual([["root"]]);
    expect(plan([a], [{ ...a, rootObservationId: rootObservationId(a.id) }], { checkRoot: true }).unchanged).toHaveLength(1);
  });

  it("an update keeps the existing timestamp on the trace and its root; a create uses now", () => {
    expect(traceEvent(FIXTURES[0], NOW, "2026-01-01T00:00:00.000Z", "e1").body.timestamp).toBe("2026-01-01T00:00:00.000Z");
    const created = traceEvent(FIXTURES[0], NOW, undefined, "e2");
    expect(created).toMatchObject({ id: "e2", type: "trace-create", timestamp: NOW.toISOString(), body: { id: "rls-demo-01", timestamp: NOW.toISOString() } });
    expect(rootEvent(FIXTURES[0], NOW, "2026-01-01T00:00:00.000Z", "e3")).toMatchObject({
      id: "e3", type: "span-create",
      body: { id: "rls-demo-01-root", traceId: "rls-demo-01", startTime: "2026-01-01T00:00:00.000Z", metadata: FIXTURES[0].metadata },
    });
  });

  it("compares JSON fields regardless of key order or string encoding", () => {
    expect(canonical({ b: 1, a: [1, { d: 2, c: 3 }] })).toBe(canonical({ a: [1, { c: 3, d: 2 }], b: 1 }));
    const f = FIXTURES[0];
    expect(fixtureDrift(f, { ...f, input: JSON.stringify(f.input), metadata: JSON.stringify(f.metadata) })).toEqual([]);
  });
});
