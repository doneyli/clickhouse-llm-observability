// The 30 seeded traces: loading, validation, and how a trace in Langfuse is compared with its
// fixture. Shared by the seeder (drift repair) and the readiness check (content check).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DEMO_DIR } from "./demo_env";

export const FIXTURES_PATH = resolve(DEMO_DIR, "scripts/fixtures/traces.json");
/** Every seeded trace carries this tag; the seeder only ever reads and repairs tagged traces. */
export const DEMO_TAG = "rls-demo";

export interface Fixture {
  id: string;
  name: string;
  userId: string;
  tags: string[];
  metadata: { classification: string; team: string; topic: string } & Record<string, string>;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
}

/** The fields a seeded trace must match, as Langfuse returns them. */
export interface TraceLike {
  id: string;
  name?: string;
  userId?: string | null;
  tags?: string[];
  metadata?: unknown;
  input?: unknown;
  output?: unknown;
  timestamp?: string;
}

const CLASSIFICATIONS = ["ceo-only", "restricted", "general"];
const TEAMS = ["executive", "compliance", "analyst"];

/** Parse and validate the fixtures: stable unique ids, the demo tag, and valid RLS metadata. */
export function parseFixtures(text: string): Fixture[] {
  const raw = JSON.parse(text) as unknown;
  if (!Array.isArray(raw)) throw new Error("fixtures: expected a JSON array");
  const seen = new Set<string>();
  return raw.map((f, i) => {
    const where = `fixtures[${i}]`;
    const x = f as Partial<Fixture>;
    if (typeof x.id !== "string" || !/^rls-demo-\d{2,}$/.test(x.id)) throw new Error(`${where}: id must look like rls-demo-NN`);
    if (seen.has(x.id)) throw new Error(`${where}: duplicate id ${x.id}`);
    seen.add(x.id);
    if (typeof x.name !== "string" || !x.name) throw new Error(`${where} (${x.id}): name is required`);
    if (typeof x.userId !== "string" || !x.userId) throw new Error(`${where} (${x.id}): userId is required`);
    if (!Array.isArray(x.tags) || !x.tags.includes(DEMO_TAG)) throw new Error(`${where} (${x.id}): tags must include ${DEMO_TAG}`);
    const m = x.metadata as Fixture["metadata"] | undefined;
    if (!m || !CLASSIFICATIONS.includes(m.classification) || !TEAMS.includes(m.team) || typeof m.topic !== "string") {
      throw new Error(`${where} (${x.id}): metadata needs a known classification and team, and a topic`);
    }
    if (typeof x.input !== "object" || x.input === null || typeof x.output !== "object" || x.output === null) {
      throw new Error(`${where} (${x.id}): input and output must be objects`);
    }
    return x as Fixture;
  });
}

export function loadFixtures(path: string = FIXTURES_PATH): Fixture[] {
  return parseFixtures(readFileSync(path, "utf-8"));
}

/** JSON with sorted keys, so two values compare equal regardless of key order. */
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value as object).sort()
      .map((k) => `${JSON.stringify(k)}:${canonical((value as Record<string, unknown>)[k])}`).join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}

/** Langfuse may hand back JSON fields as strings; compare the parsed value. */
function parsed(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

/**
 * The fixture-owned fields that differ in `trace`, in a fixed order. Only fields the fixture
 * owns count: an extra tag or metadata key someone added is left alone, and is not drift
 * (an upsert would not remove it, so treating it as drift would "update" on every run).
 */
export function fixtureDrift(fixture: Fixture, trace: TraceLike): string[] {
  const drift: string[] = [];
  if (trace.name !== fixture.name) drift.push("name");
  if ((trace.userId ?? undefined) !== fixture.userId) drift.push("userId");
  const tags = new Set(trace.tags ?? []);
  if (!fixture.tags.every((t) => tags.has(t))) drift.push("tags");
  const meta = (parsed(trace.metadata) ?? {}) as Record<string, unknown>;
  const metaDiffers = ([k, v]: [string, string]) =>
    typeof meta[k] === "string" ? meta[k] !== v : canonical(meta[k]) !== canonical(v);
  if (Object.entries(fixture.metadata).some(metaDiffers)) drift.push("metadata");
  if (canonical(parsed(trace.input)) !== canonical(fixture.input)) drift.push("input");
  if (canonical(parsed(trace.output)) !== canonical(fixture.output)) drift.push("output");
  return drift;
}
