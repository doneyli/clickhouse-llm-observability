/**
 * Create or refresh the `grocery-conversations` dataset from src/conversations.ts.
 *
 * The fixtures live in code and the dataset is derived from them, never the other
 * way round. That direction matters: a conversation edited in the UI is invisible
 * to code review, and the whole value of these five fixtures is that each one is
 * engineered to make a specific failure reachable — a change to that engineering
 * is a change that should show up in a diff.
 *
 * Re-running this is safe, and it reconciles rather than re-writes: src/dataset.ts
 * reads the dataset and its items, compares the complete definition this demo owns
 * (description and metadata; each item's input, expected output, metadata and
 * ACTIVE status) and writes only what differs, on stable `grocery-<conversation id>`
 * ids. Dataset items are UPSERTED on their `id`, so a changed fixture edits its item
 * rather than adding one — random ids here would silently double the dataset every
 * time someone re-seeded, and every later pass rate would be computed over a
 * corrupted denominator. Every artifact is reported as created, updated or
 * unchanged, and any failed write makes the run exit non-zero.
 *
 * API surface used, read from node_modules/@langfuse/core/dist/index.d.ts — the JS
 * names do NOT match the Python SDK's:
 *   langfuse.api.datasets.get(name) / .create({ name, description, metadata })
 *   langfuse.api.datasetItems.list({ datasetName, page, limit })
 *   langfuse.api.datasetItems.create({ datasetName, id, input, expectedOutput, metadata, status })
 */
import "../src/instrumentation.js";

import { pathToFileURL } from "node:url";

import { LangfuseClient } from "@langfuse/client";

import { flushTraces } from "../src/instrumentation.js";
import {
  LANGFUSE_BASE_URL,
  LANGFUSE_PUBLIC_KEY,
  LANGFUSE_SECRET_KEY,
  verifyProject,
} from "../src/env.js";
import { CONVERSATIONS } from "../src/conversations.js";
import {
  DATASET_NAME,
  datasetItemId,
  reconcileDataset,
  type DatasetStore,
  type ExistingItem,
  type ReconcileReport,
} from "../src/dataset.js";

export { DATASET_NAME, datasetItemId };

const BOLD = "\u001b[1m";
const DIM = "\u001b[2m";
const RED = "\u001b[31m";
const OFF = "\u001b[0m";

export function langfuseClient(): LangfuseClient {
  return new LangfuseClient({
    publicKey: LANGFUSE_PUBLIC_KEY,
    secretKey: LANGFUSE_SECRET_KEY,
    baseUrl: LANGFUSE_BASE_URL,
  });
}

/** The reconcile's store, backed by the public API. Only a 404 reads as "absent". */
export function apiStore(langfuse: LangfuseClient): DatasetStore {
  return {
    async getDataset(name) {
      try {
        return await langfuse.api.datasets.get(name);
      } catch (err) {
        if ((err as { statusCode?: number }).statusCode === 404) return undefined;
        throw err;
      }
    },
    async listItems(datasetName) {
      const out: ExistingItem[] = [];
      for (let page = 1; page <= 50; page += 1) {
        const res = await langfuse.api.datasetItems.list({ datasetName, page, limit: 100 });
        out.push(...res.data);
        if (res.data.length === 0 || page >= res.meta.totalPages) return out;
      }
      throw new Error(`${datasetName}: more than 50 pages of items`);
    },
    async upsertDataset(definition) {
      await langfuse.api.datasets.create(definition);
    },
    async upsertItem(datasetName, item) {
      await langfuse.api.datasetItems.create({ datasetName, ...item });
    },
  };
}

export function printReport(report: ReconcileReport): void {
  for (const line of report.lines) {
    const colour = line.outcome === "failed" ? RED : line.outcome === "unchanged" ? DIM : BOLD;
    console.log(
      `  ${colour}${line.outcome.padEnd(9)}${OFF} ${line.kind.padEnd(7)} ${line.id}` +
        (line.detail ? ` ${DIM}(${line.detail})${OFF}` : ""),
    );
  }
  const count = (o: string) => report.lines.filter((l) => l.outcome === o).length;
  console.log("");
  console.log(
    `${BOLD}${DATASET_NAME}${OFF}: ${count("created")} created, ${count("updated")} updated, ` +
      `${count("unchanged")} unchanged, ${report.failed} failed`,
  );
  if (report.extraItems.length > 0) {
    console.log(
      `${DIM}  ${report.extraItems.length} active item(s) src/conversations.ts does not define were left alone: ` +
        `${report.extraItems.join(", ")}. Archive them from the UI if they are stale.${OFF}`,
    );
  }
}

export async function seedDataset(): Promise<void> {
  const report = await reconcileDataset(apiStore(langfuseClient()));
  printReport(report);
  if (report.failed > 0) {
    throw new Error(`${report.failed} write(s) to ${DATASET_NAME} failed; see the lines marked failed`);
  }
  console.log(`${DIM}  ${CONVERSATIONS.length} conversation item(s) expected.${OFF}`);
}

async function main(): Promise<void> {
  await verifyProject();
  await seedDataset();
}

const isEntrypoint =
  process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isEntrypoint) {
  try {
    await main();
  } catch (err) {
    console.error(`\n${RED}✗${OFF} ${err instanceof Error ? err.message : String(err)}`);
    process.exitCode = 1;
  } finally {
    await flushTraces();
  }
}
