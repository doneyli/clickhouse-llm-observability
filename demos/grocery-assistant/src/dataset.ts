/**
 * The `grocery-conversations` dataset as code, and the reconcile that seeds it.
 *
 * The fixtures in src/conversations.ts are the source of truth; the dataset is
 * derived from them. Seeding reads what exists, compares the complete definition
 * this demo owns (the dataset's description and metadata; each item's input,
 * expected output, metadata and ACTIVE status, on a stable id) and writes only
 * what differs. An unchanged run writes nothing, so it adds no item versions, and
 * it reports each artifact as created, updated or unchanged.
 *
 * Items the fixtures no longer define are left alone and reported: they may be
 * someone's own data, and archiving is a deliberate act in the UI.
 *
 * No credentials or SDK here: `seed-dataset.ts` passes a store backed by the
 * Langfuse client, and the tests pass a fake one.
 */
import { CONVERSATIONS, CONVERSATION_CRITERIA, type Conversation } from "./conversations.js";

export const DATASET_NAME = "grocery-conversations";

/** Stable, human-readable, and unique project-wide — the three things an upsert key needs. */
export function datasetItemId(conversationId: string): string {
  return `grocery-${conversationId}`;
}

export type DatasetDefinition = { name: string; description: string; metadata: Record<string, unknown> };

export type ItemDefinition = {
  id: string;
  input: unknown;
  expectedOutput: unknown;
  metadata: Record<string, unknown>;
  status: "ACTIVE";
};

export function desiredDataset(conversations: Conversation[] = CONVERSATIONS): DatasetDefinition {
  return {
    name: DATASET_NAME,
    description:
      "Five multi-turn Northwind Grocers shopper conversations, each engineered so one " +
      "specific failure is reachable. Graded by the deterministic evaluators in " +
      "src/evaluators/deterministic.ts.",
    metadata: {
      source: "demos/grocery-assistant/src/conversations.ts",
      conversationCount: conversations.length,
    },
  };
}

export function desiredItems(
  conversations: Conversation[] = CONVERSATIONS,
  criteria: Record<string, string> = CONVERSATION_CRITERIA,
): ItemDefinition[] {
  return conversations.map((conversation) => {
    const criterion = criteria[conversation.failureMode];
    if (!criterion) {
      throw new Error(
        `No criteria for failure mode '${conversation.failureMode}'. ` +
          `Add it to CONVERSATION_CRITERIA in src/conversations.ts.`,
      );
    }
    return {
      id: datasetItemId(conversation.id),
      // The shopper's turns ARE the input: the whole conversation is the unit of
      // evaluation here, not a single question, because three of the five
      // failures only exist across turns.
      input: { turns: conversation.turns, userId: conversation.userId },
      expectedOutput: { failureMode: conversation.failureMode, criteria: criterion },
      metadata: { conversationId: conversation.id, failureMode: conversation.failureMode },
      status: "ACTIVE",
    };
  });
}

/** JSON with sorted keys, so two equal values compare equal however the API ordered them. */
export function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
      : v,
  );
}

/** What the store returned for one item. Fields are absent when the API omitted them. */
export type ExistingItem = { id: string; status?: string; input?: unknown; expectedOutput?: unknown; metadata?: unknown };
export type ExistingDataset = { description?: string | null; metadata?: unknown };

export type DatasetStore = {
  /** The dataset, or `undefined` when it does not exist (HTTP 404). Any other failure throws. */
  getDataset(name: string): Promise<ExistingDataset | undefined>;
  /** Every item of the dataset, all statuses. */
  listItems(datasetName: string): Promise<ExistingItem[]>;
  /** Create or update the dataset (upserts on name). */
  upsertDataset(definition: DatasetDefinition): Promise<void>;
  /** Create or update an item (upserts on id). */
  upsertItem(datasetName: string, item: ItemDefinition): Promise<void>;
};

export type Outcome = "created" | "updated" | "unchanged" | "failed";
export type ReconcileLine = { kind: "dataset" | "item"; id: string; outcome: Outcome; detail: string };
export type ReconcileReport = { lines: ReconcileLine[]; extraItems: string[]; failed: number };

function itemDrift(want: ItemDefinition, have: ExistingItem): string[] {
  const drift: string[] = [];
  if ((have.status ?? "ACTIVE") !== "ACTIVE") drift.push(`status ${have.status} -> ACTIVE`);
  if (canonical(have.input) !== canonical(want.input)) drift.push("input");
  if (canonical(have.expectedOutput) !== canonical(want.expectedOutput)) drift.push("expectedOutput");
  if (canonical(have.metadata ?? {}) !== canonical(want.metadata)) drift.push("metadata");
  return drift;
}

/**
 * Bring the dataset in line with the fixtures. Every item is attempted even when
 * an earlier write failed, and the report counts the failures; the caller decides
 * the exit code. A failure to READ (other than a missing dataset) throws: without
 * the current state there is nothing truthful to report.
 */
export async function reconcileDataset(
  store: DatasetStore,
  dataset: DatasetDefinition = desiredDataset(),
  items: ItemDefinition[] = desiredItems(),
): Promise<ReconcileReport> {
  const lines: ReconcileLine[] = [];
  const existing = await store.getDataset(dataset.name);

  const datasetDrift: string[] = [];
  if (existing) {
    if ((existing.description ?? "") !== dataset.description) datasetDrift.push("description");
    if (canonical(existing.metadata ?? {}) !== canonical(dataset.metadata)) datasetDrift.push("metadata");
  }
  const datasetOutcome: Outcome = !existing ? "created" : datasetDrift.length ? "updated" : "unchanged";
  if (datasetOutcome === "unchanged") {
    lines.push({ kind: "dataset", id: dataset.name, outcome: "unchanged", detail: "" });
  } else {
    try {
      await store.upsertDataset(dataset);
      lines.push({ kind: "dataset", id: dataset.name, outcome: datasetOutcome, detail: datasetDrift.join(", ") });
    } catch (err) {
      lines.push({ kind: "dataset", id: dataset.name, outcome: "failed", detail: (err as Error).message });
      // Items cannot be written into a dataset that does not exist.
      if (!existing) return { lines, extraItems: [], failed: 1 };
    }
  }

  const have = new Map((existing ? await store.listItems(dataset.name) : []).map((i) => [i.id, i]));
  for (const want of items) {
    const current = have.get(want.id);
    const drift = current ? itemDrift(want, current) : [];
    if (current && drift.length === 0) {
      lines.push({ kind: "item", id: want.id, outcome: "unchanged", detail: "" });
      continue;
    }
    try {
      await store.upsertItem(dataset.name, want);
      lines.push({ kind: "item", id: want.id, outcome: current ? "updated" : "created", detail: drift.join(", ") });
    } catch (err) {
      lines.push({ kind: "item", id: want.id, outcome: "failed", detail: (err as Error).message });
    }
  }

  const wanted = new Set(items.map((i) => i.id));
  const extraItems = [...have.values()]
    .filter((i) => !wanted.has(i.id) && (i.status ?? "ACTIVE") === "ACTIVE")
    .map((i) => i.id);
  return { lines, extraItems, failed: lines.filter((l) => l.outcome === "failed").length };
}
