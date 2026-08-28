/**
 * Create or refresh the `grocery-conversations` dataset from src/conversations.ts.
 *
 * The fixtures live in code and the dataset is derived from them, never the other
 * way round. That direction matters: a conversation edited in the UI is invisible
 * to code review, and the whole value of these five fixtures is that each one is
 * engineered to make a specific failure reachable — a change to that engineering
 * is a change that should show up in a diff.
 *
 * Re-running this is safe. Dataset items are UPSERTED on their `id` (see the note
 * on `CreateDatasetItemRequest.id`: "Dataset items are upserted on their id"), so
 * a stable `grocery-<conversation id>` means the second run edits five items
 * rather than creating five more. Random ids here would silently double the
 * dataset every time someone re-seeded, and every later pass rate would be
 * computed over a corrupted denominator.
 *
 * API surface used, read from node_modules/@langfuse/client/dist/index.d.ts —
 * the JS names do NOT match the Python SDK's:
 *   langfuse.api.datasets.create({ name, description, metadata })
 *   langfuse.dataset.createItem({ datasetName, id, input, expectedOutput, metadata })
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
import { CONVERSATIONS, CONVERSATION_CRITERIA } from "../src/conversations.js";

const BOLD = "[1m";
const DIM = "[2m";
const RED = "[31m";
const OFF = "[0m";

export const DATASET_NAME = "grocery-conversations";

/** Stable, human-readable, and unique project-wide — the three things an upsert key needs. */
export function datasetItemId(conversationId: string): string {
  return `grocery-${conversationId}`;
}

export function langfuseClient(): LangfuseClient {
  return new LangfuseClient({
    publicKey: LANGFUSE_PUBLIC_KEY,
    secretKey: LANGFUSE_SECRET_KEY,
    baseUrl: LANGFUSE_BASE_URL,
  });
}

export async function seedDataset(): Promise<void> {
  const langfuse = langfuseClient();

  // POST /api/public/v2/datasets upserts on name, so this is the idempotent path
  // for "make sure the dataset exists" — no get-then-create dance needed.
  await langfuse.api.datasets.create({
    name: DATASET_NAME,
    description:
      "Five multi-turn Northwind Grocers shopper conversations, each engineered so one " +
      "specific failure is reachable. Graded by the deterministic evaluators in " +
      "src/evaluators/deterministic.ts.",
    metadata: {
      source: "demos/grocery-assistant/src/conversations.ts",
      conversationCount: CONVERSATIONS.length,
    },
  });
  console.log(`${BOLD}dataset${OFF} ${DATASET_NAME} ${DIM}(created or already present)${OFF}`);

  for (const conversation of CONVERSATIONS) {
    const criteria = CONVERSATION_CRITERIA[conversation.failureMode];
    if (!criteria) {
      throw new Error(
        `No criteria for failure mode '${conversation.failureMode}'. ` +
          `Add it to CONVERSATION_CRITERIA in src/conversations.ts.`,
      );
    }

    await langfuse.dataset.createItem({
      datasetName: DATASET_NAME,
      id: datasetItemId(conversation.id),
      // The shopper's turns ARE the input: the whole conversation is the unit of
      // evaluation here, not a single question, because three of the five
      // failures only exist across turns.
      input: { turns: conversation.turns, userId: conversation.userId },
      expectedOutput: { failureMode: conversation.failureMode, criteria },
      metadata: { conversationId: conversation.id, failureMode: conversation.failureMode },
    });

    console.log(
      `  ${datasetItemId(conversation.id).padEnd(42)} ` +
        `${String(conversation.turns.length).padStart(2)} turns  ${DIM}${conversation.failureMode}${OFF}`,
    );
  }

  await langfuse.flush();

  // Read it back. A seeder that reports success without checking is how a demo
  // opens on an empty dataset.
  const fetched = await langfuse.dataset.get(DATASET_NAME);
  console.log("");
  console.log(
    `${BOLD}verified${OFF} ${DATASET_NAME} holds ${fetched.items.length} item(s) ` +
      `${DIM}(expected ${CONVERSATIONS.length})${OFF}`,
  );
  if (fetched.items.length !== CONVERSATIONS.length) {
    console.log(
      `${DIM}  A higher count means items exist that src/conversations.ts no longer defines. ` +
        `They are archived from the UI, not by re-seeding.${OFF}`,
    );
  }
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
