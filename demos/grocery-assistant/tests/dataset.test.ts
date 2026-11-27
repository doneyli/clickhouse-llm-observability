/**
 * The dataset reconcile (src/dataset.ts) against a fake store: created, updated and
 * unchanged are reported truthfully, an unchanged run writes nothing, drift in any
 * owned field is repaired, foreign items are left alone, and partial and total
 * failures are counted rather than hidden. No network, no keys.
 */
import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  DATASET_NAME,
  canonical,
  desiredDataset,
  desiredItems,
  reconcileDataset,
  type DatasetDefinition,
  type DatasetStore,
  type ExistingDataset,
  type ExistingItem,
  type ItemDefinition,
} from "../src/dataset.js";
import { CONVERSATIONS } from "../src/conversations.js";

type Writes = { datasets: DatasetDefinition[]; items: ItemDefinition[] };

function store(
  initial: { dataset?: ExistingDataset; items?: ExistingItem[] },
  fail: { getDataset?: Error; upsertDataset?: Error; upsertItem?: (id: string) => Error | undefined } = {},
): { store: DatasetStore; writes: Writes } {
  let dataset = initial.dataset;
  const items = new Map((initial.items ?? []).map((i) => [i.id, i]));
  const writes: Writes = { datasets: [], items: [] };
  return {
    writes,
    store: {
      async getDataset(name) {
        assert.equal(name, DATASET_NAME);
        if (fail.getDataset) throw fail.getDataset;
        return dataset;
      },
      async listItems() {
        return [...items.values()];
      },
      async upsertDataset(definition) {
        if (fail.upsertDataset) throw fail.upsertDataset;
        writes.datasets.push(definition);
        dataset = { description: definition.description, metadata: definition.metadata };
      },
      async upsertItem(_name, item) {
        const err = fail.upsertItem?.(item.id);
        if (err) throw err;
        writes.items.push(item);
        items.set(item.id, { ...item });
      },
    },
  };
}

/** The project as a previous seed left it. */
function seeded(): { dataset: ExistingDataset; items: ExistingItem[] } {
  const d = desiredDataset();
  return { dataset: { description: d.description, metadata: { ...d.metadata } }, items: desiredItems().map((i) => ({ ...i })) };
}

const outcomes = (lines: Array<{ outcome: string }>) => lines.map((l) => l.outcome);

test("an empty project gets the dataset and every item, reported as created", async () => {
  const { store: s, writes } = store({});
  const report = await reconcileDataset(s);
  assert.deepEqual(outcomes(report.lines), ["created", ...CONVERSATIONS.map(() => "created")]);
  assert.equal(writes.items.length, 5);
  assert.ok(writes.items.every((i) => i.status === "ACTIVE" && i.id.startsWith("grocery-")));
  assert.equal(report.failed, 0);
});

test("an unchanged project is reported unchanged and gets no writes at all", async () => {
  const { store: s, writes } = store(seeded());
  const report = await reconcileDataset(s);
  assert.deepEqual(outcomes(report.lines), Array(6).fill("unchanged"));
  assert.deepEqual(writes, { datasets: [], items: [] });
});

test("key order from the API does not read as drift", async () => {
  const reversed = (v: unknown): unknown =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).reverse().map(([k, x]) => [k, reversed(x)]))
      : v;
  const initial = seeded();
  initial.items = initial.items.map((i) => reversed(i) as ExistingItem);
  assert.notEqual(JSON.stringify(initial.items[0]), JSON.stringify(desiredItems()[0]), "the order really differs");
  const { store: s, writes } = store(initial);
  await reconcileDataset(s);
  assert.equal(writes.items.length, 0);
  assert.equal(canonical({ b: 1, a: { d: 2, c: 3 } }), canonical({ a: { c: 3, d: 2 }, b: 1 }));
});

test("a changed conversation updates only its item, on the same id", async () => {
  const initial = seeded();
  initial.items[2] = { ...initial.items[2]!, input: { turns: ["an older version of the fixture"], userId: "u" } };
  const { store: s, writes } = store(initial);
  const report = await reconcileDataset(s);
  const changed = report.lines.filter((l) => l.outcome !== "unchanged");
  assert.deepEqual(changed.map((l) => [l.id, l.outcome, l.detail]), [[initial.items[2]!.id, "updated", "input"]]);
  assert.deepEqual(writes.items.map((i) => i.id), [initial.items[2]!.id]);
});

test("metadata-only, expected-output-only and status drift are each repaired", async () => {
  const initial = seeded();
  initial.items[0] = { ...initial.items[0]!, metadata: { conversationId: "stale" } };
  initial.items[1] = { ...initial.items[1]!, expectedOutput: { failureMode: "x" } };
  initial.items[3] = { ...initial.items[3]!, status: "ARCHIVED" };
  const { store: s, writes } = store(initial);
  const report = await reconcileDataset(s);
  const byId = new Map(report.lines.map((l) => [l.id, l]));
  assert.equal(byId.get(initial.items[0]!.id)?.detail, "metadata");
  assert.equal(byId.get(initial.items[1]!.id)?.detail, "expectedOutput");
  assert.equal(byId.get(initial.items[3]!.id)?.detail, "status ARCHIVED -> ACTIVE");
  assert.equal(writes.items.length, 3);
  assert.equal(writes.datasets.length, 0, "the dataset itself did not change");
});

test("dataset description drift updates the dataset", async () => {
  const initial = seeded();
  initial.dataset = { description: "old words", metadata: initial.dataset.metadata };
  const { store: s, writes } = store(initial);
  const report = await reconcileDataset(s);
  assert.deepEqual([report.lines[0]!.outcome, report.lines[0]!.detail], ["updated", "description"]);
  assert.equal(writes.datasets.length, 1);
});

test("items the fixtures do not define are left alone and reported", async () => {
  const initial = seeded();
  initial.items.push({ id: "someone-elses-item", status: "ACTIVE", input: {} }, { id: "old-archived", status: "ARCHIVED" });
  const { store: s, writes } = store(initial);
  const report = await reconcileDataset(s);
  assert.deepEqual(report.extraItems, ["someone-elses-item"]);
  assert.equal(writes.items.length, 0);
});

test("a partial write failure is counted, and the other items are still written", async () => {
  const ids = desiredItems().map((i) => i.id);
  const { store: s, writes } = store({}, { upsertItem: (id) => (id === ids[1] ? new Error("HTTP 500") : undefined) });
  const report = await reconcileDataset(s);
  assert.equal(report.failed, 1);
  assert.equal(report.lines.find((l) => l.id === ids[1])?.outcome, "failed");
  assert.equal(writes.items.length, 4);
});

test("a failed dataset create stops before any item, and is counted", async () => {
  const { store: s, writes } = store({}, { upsertDataset: new Error("HTTP 403") });
  const report = await reconcileDataset(s);
  assert.equal(report.failed, 1);
  assert.deepEqual(outcomes(report.lines), ["failed"]);
  assert.equal(writes.items.length, 0);
});

test("every write failing is reported as every write failing", async () => {
  const { store: s } = store(
    { dataset: { description: "old", metadata: {} } },
    { upsertDataset: new Error("HTTP 500"), upsertItem: () => new Error("HTTP 500") },
  );
  const report = await reconcileDataset(s);
  assert.equal(report.failed, 6);
});

test("a read failure other than 404 throws: nothing truthful can be reported", async () => {
  const { store: s, writes } = store({}, { getDataset: new Error("HTTP 500") });
  await assert.rejects(reconcileDataset(s), /HTTP 500/);
  assert.deepEqual(writes, { datasets: [], items: [] });
});

test("a fixture with no criteria is an error before anything is written", () => {
  assert.throws(() => desiredItems([{ ...CONVERSATIONS[0]!, failureMode: "nope" }]), /No criteria for failure mode 'nope'/);
});
