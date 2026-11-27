// scripts/seed-traces.ts offline, against the fake Langfuse: strict credentials, idempotent
// reseeding, drift repair, failure propagation, pagination and old-seeder duplicates.
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { canonical, type Fixture, fixtureDrift, loadFixtures, parseFixtures } from "../scripts/fixtures";
import { MAX_PAGES, plan, seed, traceEvent } from "../scripts/seed-traces";
import { ENV_TEXT, FakeLangfuse, HOSTILE_SHELL } from "./fake_langfuse";

const FIXTURES = loadFixtures();
const MANIFEST = "name: langfuse-rls\nlangfuse:\n  project: RLS Demo\n";
const NOW = new Date("2026-11-27T10:00:00.000Z");

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

function seeded(): void {
  for (const f of FIXTURES) lf.add({ ...f, timestamp: "2026-11-01T00:00:00.000Z" });
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

describe("idempotent seeding", () => {
  it("creates all 30 in an empty project under their stable ids, with the fixture content", async () => {
    const { code, text } = await runSeed();
    expect(code).toBe(0);
    expect(text).toMatch(/Done: 30 created, 0 updated, 0 unchanged, 0 failed\./);
    expect(lf.writes().map((c) => c.path)).toEqual(["/api/public/ingestion"]);
    for (const f of FIXTURES) expect(fixtureDrift(f, lf.traces.get(f.id)!)).toEqual([]);
  });

  it("writes nothing on two unchanged reruns, and the project still holds exactly 30", async () => {
    await runSeed();
    const writesAfterFirst = lf.writes().length;
    for (let i = 0; i < 2; i++) {
      const { code, text } = await runSeed();
      expect(code).toBe(0);
      expect(text).toMatch(/Done: 0 created, 0 updated, 30 unchanged, 0 failed\./);
    }
    expect(lf.writes()).toHaveLength(writesAfterFirst);
    expect(lf.traces.size).toBe(30);
  });

  it("repairs a drifted trace in place, names the fields, and keeps its timestamp", async () => {
    seeded();
    const t = lf.traces.get("rls-demo-05")!;
    lf.traces.set("rls-demo-05", { ...t, metadata: { ...(t.metadata ?? {}), classification: "general" }, output: { response: "edited" } });
    const { code, text } = await runSeed();
    expect(code).toBe(0);
    expect(text).toMatch(/updated {4}rls-demo-05 .*\(metadata, output\)/);
    expect(text).toMatch(/Done: 0 created, 1 updated, 29 unchanged, 0 failed\./);
    const repaired = lf.traces.get("rls-demo-05")!;
    expect(fixtureDrift(FIXTURES[4], repaired)).toEqual([]);
    expect(repaired.timestamp).toBe("2026-11-01T00:00:00.000Z");
    expect(lf.traces.size).toBe(30);
  });

  it("re-creates a deleted fixture and leaves the others alone", async () => {
    seeded();
    lf.traces.delete("rls-demo-30");
    const { text } = await runSeed();
    expect(text).toMatch(/created {4}rls-demo-30/);
    expect(text).toMatch(/Done: 1 created, 0 updated, 29 unchanged/);
    const batch = (lf.writes()[0].body as { batch: Array<{ body: { id: string } }> }).batch;
    expect(batch.map((e) => e.body.id)).toEqual(["rls-demo-30"]);
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
    lf.traces.set("rls-demo-02", { ...t, tags: [...t.tags, "reviewed"], metadata: { ...(t.metadata ?? {}), note: "x" } });
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

  it("a failed read writes nothing", async () => {
    lf.listStatus = 500;
    const { code, text } = await runSeed();
    expect(code).toBe(1);
    expect(text).toMatch(/reading Langfuse: HTTP 500.*Nothing was written/);
    expect(lf.writes()).toHaveLength(0);
  });
});

describe("reading the project", () => {
  it("follows every page of tagged traces and ignores untagged ones", async () => {
    seeded();
    for (let i = 0; i < 230; i++) lf.add({ id: `other-${i}`, name: `Other ${i}`, tags: ["rls-demo"] });
    lf.add({ id: "unrelated", name: "someone else's trace", tags: [] });
    const { text } = await runSeed();
    expect(text).toMatch(/30 unchanged/);
    expect((text.match(/ kept {7}other-/g) ?? []).length).toBe(230);
    const pages = lf.calls.filter((c) => c.path === "/api/public/traces").map((c) => c.params.get("page"));
    expect(pages).toEqual(["1", "2", "3"]);
  });

  it("stops with an error instead of looping when a page repeats", async () => {
    for (let i = 0; i < 150; i++) lf.add({ id: `t-${i}`, tags: ["rls-demo"] });
    lf.repeatPages = true;
    const { code, text } = await runSeed();
    expect(code).toBe(1);
    expect(text).toMatch(/repeated page 2/);
    expect(lf.writes()).toHaveLength(0);
    expect(MAX_PAGES).toBeGreaterThan(1);
  });
});

describe("old-seeder duplicates", () => {
  function withLegacy(): void {
    seeded();
    FIXTURES.forEach((f, i) => lf.add({ ...f, id: `9f1c-legacy-${i}` })); // same content, random ids
    lf.add({ id: "foreign", name: "A tagged trace that is not a fixture", tags: ["rls-demo"] });
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

  it("an update event keeps the existing timestamp; a create uses now", () => {
    expect(traceEvent(FIXTURES[0], NOW, "2026-01-01T00:00:00.000Z", "e1").body.timestamp).toBe("2026-01-01T00:00:00.000Z");
    const created = traceEvent(FIXTURES[0], NOW, undefined, "e2");
    expect(created).toMatchObject({ id: "e2", type: "trace-create", timestamp: NOW.toISOString(), body: { id: "rls-demo-01", timestamp: NOW.toISOString() } });
  });

  it("compares JSON fields regardless of key order or string encoding", () => {
    expect(canonical({ b: 1, a: [1, { d: 2, c: 3 }] })).toBe(canonical({ a: [1, { c: 3, d: 2 }], b: 1 }));
    const f = FIXTURES[0];
    expect(fixtureDrift(f, { ...f, input: JSON.stringify(f.input), metadata: JSON.stringify(f.metadata) })).toEqual([]);
  });
});
