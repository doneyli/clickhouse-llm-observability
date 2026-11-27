// scripts/verify_demo.ts offline: results, summary and exit code, the .env rule, the manifest
// parse, the metric parse, and full runs against a fake Langfuse and a fake app. No network.
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { LangfuseTrace } from "../lib/types";
import {
  actLinks, Checker, type Env, expectedProject, expectedVisible, type Fetch, loadEnv, loadFixtures, manifestValue,
  metricCount, parseEnv, run, summary, traceCountQuery,
} from "../scripts/verify_demo";

const MANIFEST = "name: langfuse-rls\nlangfuse:\n  target: self-hosted\n  project: RLS Demo   # comment\ncommands:\n  verify: bash scripts/verify.sh\n";

describe("Checker and summary", () => {
  it("records pass, fail and warn and prints one line each", () => {
    const lines: string[] = [];
    const c = new Checker((l) => lines.push(l));
    expect(c.check(true, "a")).toBe(true);
    expect(c.check(false, "b")).toBe(false);
    expect(c.check(false, "c", { required: false, hint: "do x" })).toBe(false);
    expect(c.results.map((r) => r.status)).toEqual(["PASS", "FAIL", "WARN"]);
    expect(lines).toEqual(["  PASS  a", "  FAIL  b", "  WARN  c  ->  do x"]);
  });

  it("is READY when only optional checks fail", () => {
    const c = new Checker(() => {});
    c.check(true, "a");
    c.check(false, "b", { required: false });
    expect(summary(c.results)).toEqual(["READY (1/2 checks passed, 1 warning)", 0]);
  });

  it("is NOT READY when a required check fails", () => {
    const c = new Checker(() => {});
    c.check(true, "a");
    c.check(false, "b");
    c.check(false, "c", { required: false });
    c.check(false, "d", { required: false });
    expect(summary(c.results)).toEqual(["NOT READY (1/4 checks passed, 1 required failed, 2 warnings)", 1]);
  });
});

describe("environment", () => {
  it("parses comments, quotes and export", () => {
    const text = "# c\n\nexport A=1\nB=\"two words\"  \nC=three # trailing\nD='x # y'\nE=\nnot a line\nLANGFUSE_PROJECT_NAME=RLS Demo\n";
    expect(parseEnv(text)).toEqual({ A: "1", B: "two words", C: "three", D: "x # y", E: "", LANGFUSE_PROJECT_NAME: "RLS Demo" });
  });

  it("the .env overrides shell LANGFUSE_* keys and drops the rest of them", () => {
    const dir = mkdtempSync(join(tmpdir(), "rls-verify-"));
    const path = join(dir, ".env");
    writeFileSync(path, "LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_BASE_URL=http://file\n");
    const env: Env = { LANGFUSE_PUBLIC_KEY: "pk-shell", LANGFUSE_HOST: "http://shell", OTHER: "kept" };
    expect(loadEnv(path, env)).toBe(true);
    expect(env).toEqual({ LANGFUSE_PUBLIC_KEY: "pk-file", LANGFUSE_BASE_URL: "http://file", OTHER: "kept" });
  });

  it("a missing .env still drops shell LANGFUSE_* keys", () => {
    const env: Env = { LANGFUSE_SECRET_KEY: "sk-shell", OTHER: "kept" };
    expect(loadEnv(join(tmpdir(), "does-not-exist", ".env"), env)).toBe(false);
    expect(env).toEqual({ OTHER: "kept" });
  });

  it("reads nested and top-level manifest keys", () => {
    expect(manifestValue(MANIFEST, "langfuse", "project")).toBe("RLS Demo");
    expect(manifestValue(MANIFEST, "", "name")).toBe("langfuse-rls");
    expect(manifestValue(MANIFEST, "commands", "project")).toBeUndefined();
  });

  it("expects the override, then the .env's project name, then demo.yaml", () => {
    expect(expectedProject({}, MANIFEST)).toBe("RLS Demo");
    expect(expectedProject({ LANGFUSE_PROJECT_NAME: "row-level-security-RLS" }, MANIFEST)).toBe("row-level-security-RLS");
    expect(expectedProject({ LANGFUSE_PROJECT_NAME: "x", DEMO_LANGFUSE_PROJECT: "y" }, MANIFEST)).toBe("y");
  });
});

describe("expected data", () => {
  it("computes 30 / 20 / 10 from the committed fixtures with the app's policy engine", () => {
    expect(expectedVisible(loadFixtures())).toEqual({ alice: 30, bob: 20, carol: 10 });
  });

  it("parses the metric whether it comes back as a string, a number or no row", () => {
    expect(metricCount({ data: [{ count_count: "30" }] })).toBe(30);
    expect(metricCount({ data: [{ count_count: 12 }] })).toBe(12);
    expect(metricCount({ data: [] })).toBe(0);
    expect(metricCount({ data: [{ count_count: "n/a" }] })).toBeUndefined();
  });

  it("queries the traces view with a window that ends after now", () => {
    const q = JSON.parse(traceCountQuery(new Date("2026-11-27T00:00:00Z")));
    expect(q).toMatchObject({ view: "traces", metrics: [{ measure: "count", aggregation: "count" }] });
    expect(q.toTimestamp > "2026-11-27T00:00:00").toBe(true);
  });

  it("links one tab per act", () => {
    expect(actLinks("http://lf", "http://app", { projectId: "p1" }).map(([, u]) => u)).toEqual([
      "http://lf/project/p1/traces", "http://app/", "http://app/policies", "http://app/design",
    ]);
  });
});

// --- Full runs ----------------------------------------------------------------------------

const FIXTURES: LangfuseTrace[] = loadFixtures();

interface World {
  project?: string;
  traceCount?: number;
  appUp?: boolean;
  visible?: Record<string, number>;
  appError?: string;
  metrics404?: boolean;
}

function fakeFetch(w: World): Fetch {
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
  return async (url: string) => {
    const u = new URL(url);
    if (u.host === "lf.test") {
      if (u.pathname === "/api/public/projects") return json({ data: [{ id: "p1", name: w.project ?? "RLS Demo" }] });
      if (u.pathname === "/api/public/metrics") {
        if (w.metrics404) return json({ message: "not found" }, 404);
        return json({ data: [{ count_count: String(w.traceCount ?? 30) }] });
      }
    }
    if (u.host === "app.test") {
      if (w.appUp === false) throw Object.assign(new TypeError("fetch failed"), { cause: { code: "ECONNREFUSED" } });
      if (u.pathname === "/api/personas") return json([]);
      if (u.pathname === "/api/traces") {
        const persona = u.searchParams.get("persona")!;
        const total = w.traceCount ?? 30;
        const visible = w.visible?.[persona] ?? expectedVisible(FIXTURES)[persona];
        return json({ visible: Array(visible).fill({}), denied: { count: total - visible, samples: [] },
          ...(w.appError ? { error: w.appError } : {}) });
      }
    }
    return json({}, 404);
  };
}

async function runIn(w: World, envText = "LANGFUSE_BASE_URL=http://lf.test\nLANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\n",
  argv: string[] = []): Promise<{ code: number; out: string[] }> {
  const dir = mkdtempSync(join(tmpdir(), "rls-run-"));
  const envPath = join(dir, ".env");
  if (envText) writeFileSync(envPath, envText);
  const out: string[] = [];
  const code = await run({ argv, envPath, manifest: MANIFEST, fixtures: FIXTURES, fetch: fakeFetch(w),
    env: { DEMO_APP_URL: "http://app.test", LANGFUSE_PUBLIC_KEY: "pk-shell-other-project" }, out: (l) => out.push(l) });
  return { code, out };
}

describe("run", () => {
  it("is READY when Langfuse holds the 30 fixtures and the app shows 30 / 20 / 10", async () => {
    const { code, out } = await runIn({}, undefined, ["--links"]);
    expect(out.at(-1)).toBe("\nREADY (8/8 checks passed)");
    expect(code).toBe(0);
    expect(out.some((l) => l.includes("http://lf.test/project/p1/traces"))).toBe(true);
  });

  it("fails on duplicate seeding and says to wipe and seed once", async () => {
    const { code, out } = await runIn({ traceCount: 120, visible: { alice: 120, bob: 80, carol: 40 } });
    expect(code).toBe(1);
    expect(out.find((l) => l.includes("project holds 120"))).toMatch(/FAIL.*seeder appends/);
  });

  it("stops the Langfuse checks when the keys map to another project", async () => {
    const { code, out } = await runIn({ project: "LLM Observability Demo" });
    expect(code).toBe(1);
    expect(out.some((l) => l.includes("FAIL  keys map to project 'LLM Observability Demo' (want 'RLS Demo')"))).toBe(true);
    expect(out.some((l) => l.includes("project holds"))).toBe(false);
  });

  it("accepts the project named in the .env", async () => {
    const env = "LANGFUSE_BASE_URL=http://lf.test\nLANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_PROJECT_NAME=row-level-security-RLS\n";
    const { code } = await runIn({ project: "row-level-security-RLS" }, env);
    expect(code).toBe(0);
  });

  it("names the v3 server when Metrics v1 404s", async () => {
    const { code, out } = await runIn({ metrics404: true });
    expect(code).toBe(1);
    expect(out.find((l) => l.includes("trace count read failed"))).toMatch(/self-hosted v3 server/);
  });

  it("fails when the app is down, and tells you to start it", async () => {
    const { code, out } = await runIn({ appUp: false });
    expect(code).toBe(1);
    expect(out.find((l) => l.includes("app not reachable"))).toMatch(/ECONNREFUSED.*npm run dev/);
  });

  it("fails a persona whose view disagrees with the policy", async () => {
    const { code, out } = await runIn({ visible: { carol: 30 } });
    expect(code).toBe(1);
    expect(out.some((l) => l.startsWith("  FAIL  Carol Diaz (general) sees 30/30 (want 10/30)"))).toBe(true);
  });

  it("surfaces the app's own error when it could not read Langfuse", async () => {
    const { out } = await runIn({ appError: "Langfuse unreachable or no traces found." });
    expect(out.filter((l) => l.includes("Langfuse unreachable")).length).toBe(3);
  });

  it("ignores shell keys: with no .env the key check fails", async () => {
    const { code, out } = await runIn({}, "");
    expect(code).toBe(1);
    expect(out.some((l) => l.startsWith("  FAIL  env loaded from"))).toBe(true);
    expect(out.some((l) => l.startsWith("  FAIL  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY"))).toBe(true);
  });
});
