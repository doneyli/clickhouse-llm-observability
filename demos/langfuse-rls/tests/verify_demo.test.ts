// scripts/verify_demo.ts offline: results, summary and exit code, the .env rule, the manifest
// parse, and full runs against the fake Langfuse (v4 by default, v3 for the fallback) and app.
// No network.
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { type Env, expectedProject, langfuseSettings, loadEnv, manifestValue, parseEnv } from "../scripts/demo_env";
import { type Fixture, loadFixtures } from "../scripts/fixtures";
import {
  actLinks, Checker, expectedVisible, fixtureProblems, run, summary,
} from "../scripts/verify_demo";
import { APP, ENV_TEXT, FakeLangfuse, HOSTILE_SHELL, LF } from "./fake_langfuse";

const MANIFEST = "name: langfuse-rls\nlangfuse:\n  target: self-hosted\n  project: RLS Demo   # comment\ncommands:\n  verify: bash scripts/verify.sh\n";
const FIXTURES: Fixture[] = loadFixtures();

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
    const path = join(mkdtempSync(join(tmpdir(), "rls-verify-")), ".env");
    writeFileSync(path, "LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_BASE_URL=http://file\n");
    const env: Env = { LANGFUSE_PUBLIC_KEY: "pk-shell", LANGFUSE_HOST: "http://shell", LANGFUSE_SECRET_KEY: "sk-shell", OTHER: "kept" };
    expect(loadEnv(path, env)).toBe(true);
    expect(env).toEqual({ LANGFUSE_PUBLIC_KEY: "pk-file", LANGFUSE_BASE_URL: "http://file", OTHER: "kept" });
    expect(langfuseSettings(env).missing).toEqual(["LANGFUSE_SECRET_KEY"]);
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
    expect(expectedVisible(FIXTURES)).toEqual({ alice: 30, bob: 20, carol: 10 });
  });

  it("finds missing and drifted fixtures by stable id", () => {
    const visible = FIXTURES.slice(1).map((f) => (f.id === "rls-demo-02" ? { ...f, output: { response: "x" } } : f));
    expect(fixtureProblems(FIXTURES, visible)).toEqual({ missing: ["rls-demo-01"], drifted: ["rls-demo-02"] });
  });

  it("links one tab per act", () => {
    expect(actLinks("http://lf", "http://app", { projectId: "p1" }).map(([, u]) => u)).toEqual([
      "http://lf/project/p1/traces", "http://app/", "http://app/policies", "http://app/design",
    ]);
  });
});

// --- Full runs ----------------------------------------------------------------------------

let lf: FakeLangfuse;
beforeEach(() => {
  lf = new FakeLangfuse();
  for (const f of FIXTURES) lf.addWithRoot({ ...f });
});

async function runIn(opts: { env?: string | null; shell?: Env; argv?: string[] } = {}) {
  const path = join(mkdtempSync(join(tmpdir(), "rls-run-")), ".env");
  const envText = opts.env === undefined ? ENV_TEXT : opts.env;
  if (envText !== null) writeFileSync(path, envText);
  const out: string[] = [];
  const code = await run({ argv: opts.argv ?? [], envPath: path, manifest: MANIFEST, fixtures: FIXTURES, fetch: lf.fetch,
    env: { DEMO_APP_URL: APP, ...(opts.shell ?? {}) }, out: (l) => out.push(l) });
  return { code, out, text: out.join("\n") };
}

describe("run", () => {
  it("is READY when Langfuse holds the 30 fixtures and the app shows 30 / 20 / 10", async () => {
    const { code, out, text } = await runIn({ argv: ["--links"] });
    expect(out.at(-1)).toBe("\nREADY (13/13 checks passed)");
    expect(code).toBe(0);
    expect(text).toContain(`${LF}/project/p1/traces`);
    expect(text).toMatch(/PASS {2}project holds 30 trace\(s\) .*read via Observations v2/);
    expect(text).toMatch(/PASS {2}every fixture has its root observation/);
    expect(text).toMatch(/PASS {2}all 30 fixtures present under their stable ids/);
    expect(lf.reads("/api/public/traces")).toHaveLength(0);
  });

  it("is READY on the self-hosted v3 server through the legacy list, only after v2 404s", async () => {
    lf.server = "v3";
    const { code, out, text } = await runIn();
    expect(out.at(-1)).toBe("\nREADY (12/12 checks passed)");
    expect(code).toBe(0);
    expect(text).toMatch(/read via legacy trace list: Observations v2 404s on this v3 server/);
    expect(text).not.toMatch(/root observation/);
  });

  it("fails traces the old seeder left without root observations: the case the review found", async () => {
    lf.observations.clear();
    const { code, text } = await runIn();
    expect(code).toBe(1);
    expect(text).toMatch(/FAIL {2}every fixture has its root observation .*missing for rls-demo-01.*npm run seed/);
    // With no metadata to read, every trace looks public: the policy cannot split them.
    expect(text).toMatch(/FAIL {2}Bob Singh \(restricted\) sees 30\/30 \(want 20\/30\) .*carry no RLS metadata.*npm run seed/);
  });

  it("makes only reads", async () => {
    await runIn();
    expect(lf.writes()).toHaveLength(0);
  });

  it("fails before any request when .env is missing, whatever the shell exports", async () => {
    const { code, text } = await runIn({ env: null, shell: HOSTILE_SHELL });
    expect(code).toBe(1);
    expect(text).toMatch(/FAIL {2}env loaded from/);
    expect(lf.calls).toHaveLength(0);
    expect(text).toMatch(/NOT READY/);
  });

  it("fails before any request when .env leaves a key out", async () => {
    const { code, text } = await runIn({ env: ENV_TEXT.replace(/LANGFUSE_PUBLIC_KEY=.*\n/, ""), shell: HOSTILE_SHELL });
    expect(code).toBe(1);
    expect(text).toMatch(/FAIL {2}\.env does not set LANGFUSE_PUBLIC_KEY/);
    expect(lf.calls).toHaveLength(0);
  });

  it("stops the Langfuse checks when the keys map to another project", async () => {
    lf.project.name = "LLM Observability Demo";
    const { code, text } = await runIn();
    expect(code).toBe(1);
    expect(text).toContain("FAIL  keys map to project 'LLM Observability Demo' (want 'RLS Demo')");
    expect(text).not.toContain("project holds");
  });

  it("fails an empty project, with the seed hint", async () => {
    lf.traces.clear();
    const { code, text } = await runIn();
    expect(code).toBe(1);
    expect(text).toMatch(/FAIL {2}project holds 0 trace\(s\) .*npm run seed/);
  });

  it("accepts the project named in the .env", async () => {
    lf.project.name = "row-level-security-RLS";
    const { code } = await runIn({ env: `${ENV_TEXT}LANGFUSE_PROJECT_NAME=row-level-security-RLS\n` });
    expect(code).toBe(0);
  });

  it("fails on old-seeder duplicates and points at --prune-legacy", async () => {
    FIXTURES.forEach((f, i) => lf.add({ ...f, id: `legacy-${i}` }));
    const { code, text } = await runIn();
    expect(code).toBe(1);
    expect(text).toMatch(/FAIL {2}project holds 60 .*--prune-legacy/);
    expect(text).toMatch(/FAIL {2}Alice Chen \(ceo-only\) sees 60\/60 \(want 30\/30\)/);
  });

  it("fails a drifted or missing fixture even when the counts still add up", async () => {
    const r = lf.root("rls-demo-07")!;
    lf.observations.set(r.id, { ...r, output: { response: "edited by hand" } });
    lf.traces.delete("rls-demo-08");
    lf.observations.delete("rls-demo-08-root");
    lf.addWithRoot({ ...FIXTURES[7], id: "random-id" }); // same content as rls-demo-08, wrong id
    const { code, text } = await runIn();
    expect(code).toBe(1);
    expect(text).toMatch(/FAIL {2}all 30 fixtures .*; missing rls-demo-08; drifted rls-demo-07/);
  });

  it("a v2 failure other than 404 is a FAIL, never a fallback to the legacy list", async () => {
    lf.listStatus = 500;
    const { code, text } = await runIn();
    expect(code).toBe(1);
    expect(text).toMatch(/FAIL {2}trace read failed: HTTP 500/);
    expect(lf.reads("/api/public/traces")).toHaveLength(0);
  });

  it("fails when the app is down, and tells you to start it", async () => {
    lf.appDown = true;
    const { code, text } = await runIn();
    expect(code).toBe(1);
    expect(text).toMatch(/app not reachable .*ECONNREFUSED.*npm run dev/);
  });

  it("fails when an act's page is broken", async () => {
    lf.brokenPages.add("/design");
    const { code, text } = await runIn();
    expect(code).toBe(1);
    expect(text).toMatch(/FAIL {2}Act 3 page at http:\/\/app\.test\/design \(500\)/);
  });

  it("surfaces the app's own error when it could not read Langfuse", async () => {
    lf.traces.clear();
    const { text } = await runIn();
    expect((text.match(/Langfuse unreachable/g) ?? []).length).toBe(3);
    expect(text).toMatch(/FAIL {2}fixture ids and content/);
  });

  it("turns an unexpected error into a FAIL and still prints the verdict", async () => {
    const out: string[] = [];
    const path = join(mkdtempSync(join(tmpdir(), "rls-run-")), ".env");
    writeFileSync(path, ENV_TEXT);
    const bad = { id: "rls-demo-01", get metadata(): never { throw new Error("boom"); } } as unknown as Fixture;
    const code = await run({ envPath: path, manifest: MANIFEST, env: { DEMO_APP_URL: APP }, fetch: lf.fetch,
      fixtures: [bad], out: (l) => out.push(l) });
    expect(code).toBe(1);
    expect(out).toContain("  FAIL  unexpected error: boom");
    expect(out.at(-1)).toMatch(/^\nNOT READY/);
  });
});
