// lib/trace-read.ts and the app's lib/langfuse-client.ts offline: the Observations v2 read
// (field groups, filter, cursor), rebuilding traces from root rows, the 404-only legacy
// fallback, and the fake's own strictness, so the other suites cannot pass on a lenient fake.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readTraces, ReadError, tracesFromRows, type V2Row, V2_PATH } from "../lib/trace-read";
import { loadFixtures } from "../scripts/fixtures";
import { FakeLangfuse, KEYS, LF } from "./fake_langfuse";

const FIXTURES = loadFixtures();
const NOW = new Date("2026-11-27T10:00:00.000Z");
let lf: FakeLangfuse;

beforeEach(() => {
  lf = new FakeLangfuse();
  for (const f of FIXTURES) lf.addWithRoot({ ...f });
});

const target = () => ({ baseUrl: LF, publicKey: KEYS.pk, secretKey: KEYS.sk, fetch: lf.fetch });
const auth = { Authorization: `Basic ${Buffer.from(`${KEYS.pk}:${KEYS.sk}`).toString("base64")}` };
const bounded = JSON.stringify([{ type: "datetime", column: "startTime", operator: ">=", value: "2026-01-01T00:00:00Z" }]);

async function v2(params: Record<string, string>): Promise<{ status: number; body: { data?: Array<Record<string, unknown>> } }> {
  const res = await lf.fetch(`${LF}${V2_PATH}?${new URLSearchParams(params)}`, { headers: auth });
  return { status: res.status, body: await res.json() };
}

describe("the fake Observations v2 is as strict as the real one", () => {
  it("rejects unknown parameters, field groups, filter conditions and limits", async () => {
    expect((await v2({ filter: bounded, parseIoAsJson: "true" })).status).toBe(400);
    expect((await v2({ filter: bounded, isRootObservation: "true" })).status).toBe(400);
    expect((await v2({ filter: bounded, fields: "core,everything" })).status).toBe(400);
    expect((await v2({ filter: bounded, limit: "1001" })).status).toBe(400);
    expect((await v2({ filter: bounded, cursor: "not-a-cursor" })).status).toBe(400);
    expect((await v2({ filter: JSON.stringify([{ type: "string", column: "tags", operator: "=", value: "x" }]) })).status).toBe(400);
    expect((await v2({})).status).toBe(400); // unbounded read
  });

  it("returns only the requested field groups, I/O as raw strings, and truncates metadata unless expanded", async () => {
    const r = lf.root("rls-demo-01")!;
    lf.observations.set(r.id, { ...r, metadata: { ...r.metadata, topic: "x".repeat(300) } });
    const core = (await v2({ filter: bounded, fields: "core" })).body.data![0];
    expect(Object.keys(core).sort()).toEqual(["endTime", "id", "parentObservationId", "projectId", "startTime", "traceId", "type"]);
    const rows = (await v2({ filter: bounded, fields: "core,io,metadata", limit: "100" })).body.data!;
    const row = rows.find((x) => x.id === r.id)!;
    expect(typeof row.input).toBe("string");
    expect((row.metadata as Record<string, string>).topic).toHaveLength(200);
    const expanded = (await v2({ filter: bounded, fields: "core,metadata", expandMetadata: "topic", limit: "100" })).body.data!;
    expect((expanded.find((x) => x.id === r.id)!.metadata as Record<string, string>).topic).toHaveLength(300);
  });
});

describe("readTraces on a v4 server", () => {
  it("rebuilds every fixture from its root observation, with full metadata, and never calls the legacy list", async () => {
    const r = lf.root("rls-demo-02")!;
    lf.observations.set(r.id, { ...r, metadata: { ...r.metadata, topic: "y".repeat(300) } });
    const read = await readTraces(target(), { now: NOW });
    expect(read.source).toBe("observations-v2");
    const t = read.traces.find((x) => x.id === "rls-demo-01")!;
    expect(t).toMatchObject({ name: FIXTURES[0].name, userId: FIXTURES[0].userId, input: FIXTURES[0].input,
      output: FIXTURES[0].output, metadata: FIXTURES[0].metadata, rootObservationId: "rls-demo-01-root" });
    expect(read.traces.find((x) => x.id === "rls-demo-02")!.metadata!.topic).toHaveLength(300);
    expect(lf.reads("/api/public/traces")).toHaveLength(0);
  });

  it("a 404 after the first page is an error, not a reason to fall back", async () => {
    lf.v2EmptyPages = 1;
    const real = lf.fetch;
    let n = 0;
    lf.fetch = async (url, init) => (String(url).includes(V2_PATH) && ++n === 2 ? new Response("{}", { status: 404 }) : real(url, init));
    await expect(readTraces(target(), { now: NOW })).rejects.toThrow(ReadError);
    expect(lf.reads("/api/public/traces")).toHaveLength(0);
  });
});

describe("tracesFromRows", () => {
  it("prefers the real root and fills gaps from the synthetic t- row", () => {
    const rows: V2Row[] = [
      { id: "t-a", traceId: "a", startTime: "2026-11-01T00:00:00Z", traceName: "Trace A", userId: "alice", tags: ["rls-demo"],
        input: null, output: null, metadata: {} },
      { id: "a-root", traceId: "a", startTime: "2026-11-01T00:00:01Z", name: "root", traceName: "", userId: null,
        input: '{"prompt":"p"}', output: '"plain"', metadata: { classification: "general" } },
      { id: "t-b", traceId: "b", startTime: "2026-11-02T00:00:00Z", traceName: "Trace B", tags: [] },
    ];
    const [a, b] = tracesFromRows(rows);
    expect(a).toMatchObject({ id: "a", name: "Trace A", userId: "alice", tags: ["rls-demo"], input: { prompt: "p" },
      output: "plain", metadata: { classification: "general" }, timestamp: "2026-11-01T00:00:00Z", rootObservationId: "a-root" });
    expect(b).toMatchObject({ id: "b", name: "Trace B", metadata: undefined, input: undefined, rootObservationId: undefined });
  });
});

describe("the app's listTraces (lib/langfuse-client.ts)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  async function appList() {
    vi.stubEnv("LANGFUSE_BASE_URL", LF);
    vi.stubEnv("LANGFUSE_PUBLIC_KEY", KEYS.pk);
    vi.stubEnv("LANGFUSE_SECRET_KEY", KEYS.sk);
    vi.stubGlobal("fetch", lf.fetch);
    vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.resetModules();
    const { listTraces } = await import("../lib/langfuse-client");
    return listTraces();
  }

  it("reads Observations v2 on a v4 server and returns the 30 fixtures with their RLS metadata", async () => {
    const traces = await appList();
    expect(traces).toHaveLength(30);
    expect(traces.every((t) => t.metadata?.classification)).toBe(true);
    expect(lf.reads(V2_PATH)).toHaveLength(1);
    expect(lf.reads("/api/public/traces")).toHaveLength(0);
  });

  it("falls back to the legacy list on a v3 server", async () => {
    lf.server = "v3";
    const traces = await appList();
    expect(traces).toHaveLength(30);
    expect(lf.reads(V2_PATH)).toHaveLength(1);
    expect(lf.reads("/api/public/traces").length).toBeGreaterThan(0);
  });

  it("returns no traces, rather than throwing, when the read fails", async () => {
    lf.listStatus = 500;
    expect(await appList()).toEqual([]);
  });
});
