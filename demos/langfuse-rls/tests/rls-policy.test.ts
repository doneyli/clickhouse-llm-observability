// The access-control logic under lib/: the clearance ceiling, default deny, the no-metadata
// rule, and the persona x classification matrix the demo script quotes. No services.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { getPersona, PERSONAS } from "../lib/personas";
import { DEMO_POLICIES } from "../lib/policies";
import { evaluate, evaluateBatch } from "../lib/rls-policy";
import type { Classification, LangfuseTrace, Subject, TeamName } from "../lib/types";

const alice = getPersona("alice")!;
const bob = getPersona("bob")!;
const carol = getPersona("carol")!;

function trace(classification: Classification, team: TeamName = "analyst", id = "t"): LangfuseTrace {
  return { id, name: `${classification}-${team}`, metadata: { classification, team, topic: "x" } };
}

const fixtures: LangfuseTrace[] = (
  JSON.parse(readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../scripts/fixtures/traces.json"), "utf-8")) as Omit<LangfuseTrace, "id">[]
).map((f, i) => ({ id: `f${i}`, ...f }));

describe("personas", () => {
  it("are the three documented subjects", () => {
    expect(PERSONAS.map((p) => [p.id, p.team, p.clearance])).toEqual([
      ["alice", "executive", "ceo-only"],
      ["bob", "compliance", "restricted"],
      ["carol", "analyst", "general"],
    ]);
  });

  it("getPersona returns undefined for an unknown id", () => {
    expect(getPersona("mallory")).toBeUndefined();
  });
});

describe("policies", () => {
  it("are an allow rule then a default deny, and nothing else", () => {
    expect(DEMO_POLICIES.map((p) => [p.id, p.effect])).toEqual([
      ["allow-clearance-ge-classification", "allow"],
      ["deny-default", "deny"],
    ]);
  });
});

describe("evaluate", () => {
  const matrix: Array<[Subject, Classification, boolean]> = [
    [alice, "ceo-only", true], [alice, "restricted", true], [alice, "general", true],
    [bob, "ceo-only", false], [bob, "restricted", true], [bob, "general", true],
    [carol, "ceo-only", false], [carol, "restricted", false], [carol, "general", true],
  ];

  it.each(matrix)("%o on a %s trace -> allow=%s", (subject, classification, allow) => {
    const r = evaluate(subject, trace(classification));
    expect(r.allow).toBe(allow);
    expect(r.matchedRule).toBe(allow ? "allow-clearance-ge-classification" : "deny-default");
  });

  it("never lets a team match widen access (the removed allow-own-team rule)", () => {
    // Carol is on the analyst team; analyst-owned ceo-only and restricted traces stay hidden.
    expect(evaluate(carol, trace("ceo-only", "analyst")).allow).toBe(false);
    expect(evaluate(carol, trace("restricted", "analyst")).allow).toBe(false);
    expect(evaluate(bob, trace("ceo-only", "compliance")).allow).toBe(false);
  });

  it("treats a trace without RLS metadata as public", () => {
    for (const t of [
      { id: "a", name: "no metadata" },
      { id: "b", name: "no team", metadata: { classification: "ceo-only" } },
      { id: "c", name: "no classification", metadata: { team: "executive" } },
    ] as LangfuseTrace[]) {
      expect(evaluate(carol, t)).toMatchObject({ allow: true, matchedRule: "no-metadata" });
    }
  });

  it("lets every persona see a trace with an unknown classification (fails open; README Known limits)", () => {
    const unknown = { id: "u", name: "u", metadata: { classification: "top-secret", team: "analyst", topic: "x" } };
    // classificationRank() maps an unknown label to -1, so every clearance (rank >= 0) passes the gate.
    // This pins today's behaviour so a change to it is deliberate.
    for (const p of PERSONAS) expect(evaluate(p, unknown as unknown as LangfuseTrace).allow).toBe(true);
  });

  it("returns the policy's reason text", () => {
    expect(evaluate(carol, trace("ceo-only")).reason).toBe(DEMO_POLICIES[1].reason);
    expect(evaluate(alice, trace("ceo-only")).reason).toBe(DEMO_POLICIES[0].reason);
  });
});

describe("evaluateBatch", () => {
  it("keeps order and every field, and attaches one _rls verdict per trace", () => {
    const input = [trace("general", "analyst", "1"), trace("ceo-only", "executive", "2")];
    const out = evaluateBatch(bob, input);
    expect(out.map((t) => [t.id, t.name, t._rls.allow])).toEqual([["1", "general-analyst", true], ["2", "ceo-only-executive", false]]);
    expect(input[0]).not.toHaveProperty("_rls"); // the input is not mutated
  });
});

describe("the seeded fixtures", () => {
  it("are 30 traces, 10 per classification, all tagged rls-demo", () => {
    expect(fixtures).toHaveLength(30);
    const counts: Record<string, number> = {};
    for (const f of fixtures) counts[f.metadata!.classification] = (counts[f.metadata!.classification] ?? 0) + 1;
    expect(counts).toEqual({ "ceo-only": 10, restricted: 10, general: 10 });
    expect(fixtures.every((f) => f.tags?.includes("rls-demo"))).toBe(true);
  });

  it("produce the 30 / 20 / 10 split the demo script quotes", () => {
    const visible = PERSONAS.map((p) => evaluateBatch(p, fixtures).filter((t) => t._rls.allow).length);
    expect(visible).toEqual([30, 20, 10]);
  });
});
