/**
 * Run every conversation in the `grocery-conversations` dataset through the
 * assistant and record the result as a Langfuse dataset run.
 *
 * The JS client DOES have an experiment runner — `dataset.runExperiment(...)`,
 * read from node_modules/@langfuse/client/dist/index.d.ts — so no REST fallback
 * is needed here. It creates the dataset run, one run item and trace per dataset
 * item, and one score per `Evaluation` an evaluator returns.
 *
 * The one thing this script is careful about is the DENOMINATOR.
 *
 * These evaluators are reference-free and situational: `stale-discount-quoted`
 * has nothing to say about a conversation that never quotes a discount, and
 * reports `applicable: false`. Averaging those in as passes turns "2 of 2
 * conversations that actually exercised this check" into "5 of 5", which reads
 * as five times the evidence it is. So an evaluator that was applicable to no
 * turn of a conversation emits NO score for it, and the summary prints how many
 * conversations each rate is actually over.
 */
import "../src/instrumentation.js";

import { pathToFileURL } from "node:url";

import type { Evaluation, Evaluator, ExperimentItemResult } from "@langfuse/client";

import { flushTraces } from "../src/instrumentation.js";
import { AGENT_MODEL, verifyProject } from "../src/env.js";
import { formatMoney } from "../src/catalog.js";
import { getConversation, type Conversation } from "../src/conversations.js";
import { DETERMINISTIC_EVALUATORS, type Verdict } from "../src/evaluators/deterministic.js";
import { driveConversation } from "./run-conversation.js";
import { DATASET_NAME, langfuseClient } from "./seed-dataset.js";

const BOLD = "[1m";
const DIM = "[2m";
const GREEN = "[32m";
const RED = "[31m";
const OFF = "[0m";

/** The evaluator names, taken from the evaluator functions rather than retyped. */
const EVALUATOR_NAMES = ["unverified-cart-claim", "fabricated-purchase-history", "dropped-dietary-constraint", "stale-discount-quoted"] as const;

// ------------------------------------------------------------- task shapes ---
type TurnSummary = {
  turn: number;
  shopper: string;
  assistant: string;
  toolsCalled: string[];
  cartSkus: string[];
  verdicts: Verdict[];
};

type TaskOutput = {
  conversationId: string;
  sessionId: string;
  turns: TurnSummary[];
  finalCartSkus: string[];
  finalSubtotal: string;
};

function isTaskOutput(value: unknown): value is TaskOutput {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as TaskOutput).turns) &&
    typeof (value as TaskOutput).conversationId === "string"
  );
}

/**
 * Recover the fixture a dataset item came from.
 *
 * The item carries the turns, so the conversation could be reconstructed from
 * it alone — but `failureMode` and `title` live in code, and preferring the code
 * means a run always reports against the current fixture definition rather than
 * whatever was seeded last.
 */
function conversationFromItem(input: unknown, metadata: unknown): Conversation {
  const meta = (metadata ?? {}) as { conversationId?: unknown };
  if (typeof meta.conversationId === "string") {
    const known = getConversation(meta.conversationId);
    if (known) return known;
  }
  const shape = (input ?? {}) as { turns?: unknown; userId?: unknown };
  if (!Array.isArray(shape.turns) || typeof shape.userId !== "string") {
    throw new Error(
      "Dataset item is not a grocery conversation: expected input { turns: string[], userId: string }. " +
        "Re-run `tsx scripts/seed-dataset.ts`.",
    );
  }
  return {
    id: "unknown",
    title: "unknown (item not in src/conversations.ts)",
    userId: shape.userId,
    failureMode: "unknown",
    turns: shape.turns.map(String),
  };
}

// ------------------------------------------------------------- aggregation ---
/**
 * Collapse one evaluator's per-turn verdicts into one verdict for the whole
 * conversation, or nothing at all.
 *
 * All-must-pass, not majority: a conversation where the cart was misreported on
 * one turn out of seven is a conversation that misreported the cart. Returning
 * `undefined` when no turn was applicable is what keeps the denominator honest.
 */
export function collapseVerdicts(
  name: string,
  turns: TurnSummary[],
): { passed: boolean; applicableTurns: number[]; failingTurns: number[] } | undefined {
  const applicable = turns
    .map((t) => ({ turn: t.turn, verdict: t.verdicts.find((v) => v.name === name) }))
    .filter((x): x is { turn: number; verdict: Verdict } => x.verdict?.applicable === true);

  if (applicable.length === 0) return undefined;

  const failing = applicable.filter((x) => !x.verdict.passed);
  return {
    passed: failing.length === 0,
    applicableTurns: applicable.map((x) => x.turn),
    failingTurns: failing.map((x) => x.turn),
  };
}

function buildEvaluators(): Evaluator[] {
  return EVALUATOR_NAMES.map<Evaluator>((name) => async ({ output }) => {
    if (!isTaskOutput(output)) return [];
    const collapsed = collapseVerdicts(name, output.turns);
    // No applicable turn: emit no score at all. A 1 here would be indistinguishable
    // in the UI from a check that ran and passed.
    if (!collapsed) return [];

    const failingComments = output.turns
      .filter((t) => collapsed.failingTurns.includes(t.turn))
      .flatMap((t) => t.verdicts.filter((v) => v.name === name).map((v) => `turn ${t.turn}: ${v.comment}`));

    const evaluation: Evaluation = {
      name,
      value: collapsed.passed ? 1 : 0,
      dataType: "BOOLEAN",
      comment: collapsed.passed
        ? `Passed on every applicable turn (${collapsed.applicableTurns.join(", ")}).`
        : failingComments.join(" | "),
      metadata: {
        applicableTurns: collapsed.applicableTurns,
        failingTurns: collapsed.failingTurns,
      },
    };
    return [evaluation];
  });
}

// -------------------------------------------------------------- the summary ---
type Row = {
  name: string;
  passed: number;
  applicableItems: number;
  skippedItems: number;
  failingConversations: string[];
};

export function summarize(itemResults: ExperimentItemResult[]): Row[] {
  return EVALUATOR_NAMES.map((name) => {
    const row: Row = { name, passed: 0, applicableItems: 0, skippedItems: 0, failingConversations: [] };
    for (const item of itemResults) {
      if (!isTaskOutput(item.output)) {
        row.skippedItems += 1;
        continue;
      }
      const collapsed = collapseVerdicts(name, item.output.turns);
      if (!collapsed) {
        row.skippedItems += 1;
        continue;
      }
      row.applicableItems += 1;
      if (collapsed.passed) row.passed += 1;
      else row.failingConversations.push(`${item.output.conversationId} (turn ${collapsed.failingTurns.join(", ")})`);
    }
    return row;
  });
}

function printSummary(rows: Row[], totalItems: number): void {
  console.log("");
  console.log(`${BOLD}pass rate by evaluator${OFF} ${DIM}(over ${totalItems} conversations)${OFF}`);
  console.log(
    `${BOLD}${"evaluator".padEnd(30)} ${"passed".padEnd(8)} ${"of applicable".padEnd(14)} ${"not applicable".padEnd(15)}${OFF}`,
  );
  console.log("─".repeat(76));
  for (const row of rows) {
    const rate =
      row.applicableItems === 0
        ? `${DIM}—${OFF}`
        : `${row.passed === row.applicableItems ? GREEN : RED}${row.passed}/${row.applicableItems}${OFF}`;
    console.log(
      `${row.name.padEnd(30)} ${rate.padEnd(8 + 9)} ` +
        `${`${row.applicableItems} of ${totalItems}`.padEnd(14)} ${String(row.skippedItems).padEnd(15)}`,
    );
  }

  const failing = rows.filter((r) => r.failingConversations.length > 0);
  if (failing.length > 0) {
    console.log("");
    console.log(`${BOLD}where it failed${OFF}`);
    for (const row of failing) {
      console.log(`  ${RED}${row.name}${OFF}`);
      for (const c of row.failingConversations) console.log(`    ${c}`);
    }
  }

  const withoutEvidence = rows.filter((r) => r.applicableItems === 0);
  if (withoutEvidence.length > 0) {
    console.log("");
    console.log(
      `${DIM}No conversation exercised: ${withoutEvidence.map((r) => r.name).join(", ")}. ` +
        `These have no rate rather than a perfect one — there is no evidence either way.${OFF}`,
    );
  }
}

// ------------------------------------------------------------------- runner ---
export async function runExperiment(): Promise<void> {
  const langfuse = langfuseClient();
  const dataset = await langfuse.dataset.get(DATASET_NAME);
  if (dataset.items.length === 0) {
    throw new Error(
      `Dataset '${DATASET_NAME}' is empty. Run: tsx scripts/seed-dataset.ts`,
    );
  }

  const runName = `deterministic-board-${new Date().toISOString().replace(/[:.]/g, "-")}`;
  console.log("");
  console.log(`${BOLD}experiment${OFF} ${runName}`);
  console.log(
    `  dataset ${DATASET_NAME} ${DIM}(${dataset.items.length} items)${OFF}   ` +
      `mode good   model ${AGENT_MODEL}`,
  );

  const result = await dataset.runExperiment({
    name: "deterministic-board",
    runName,
    description:
      "Every fixture conversation through the well-instrumented assistant, graded by the " +
      "four deterministic evaluators. Reference-free, so the same checks run on live traffic.",
    metadata: { agentModel: AGENT_MODEL, instrumentation: "good" },
    // Five conversations of six to seven turns each. Unbounded concurrency here
    // is a rate-limit incident, not a speedup.
    maxConcurrency: 2,

    task: async (item) => {
      const conversation = conversationFromItem(item.input, item.metadata);
      const sessionId = `exp-${runName}-${conversation.id}`;
      const records = await driveConversation({
        conversation,
        sessionId,
        mode: "good",
        extraTags: ["experiment", `run:${runName}`],
      });

      const last = records[records.length - 1];
      const output: TaskOutput = {
        conversationId: conversation.id,
        sessionId,
        turns: records.map((r) => ({
          turn: r.turnIndex + 1,
          shopper: r.message,
          assistant: r.result.answer,
          toolsCalled: r.result.toolsCalled,
          cartSkus: r.result.cartSkus,
          verdicts: r.verdicts,
        })),
        finalCartSkus: last?.result.cartSkus ?? [],
        finalSubtotal: formatMoney(last?.result.cartSubtotalCents ?? 0),
      };
      console.log(`  ${DIM}done${OFF} ${conversation.id} ${DIM}(${records.length} turns)${OFF}`);
      return output;
    },

    evaluators: buildEvaluators(),
  });

  printSummary(summarize(result.itemResults), result.itemResults.length);

  console.log("");
  if (result.datasetRunUrl) console.log(`${BOLD}dataset run${OFF}  ${result.datasetRunUrl}`);
  else console.log(`${BOLD}dataset run${OFF}  ${runName} ${DIM}(no URL returned)${OFF}`);

  await langfuse.flush();
}

async function main(): Promise<void> {
  await verifyProject();
  if (DETERMINISTIC_EVALUATORS.length !== EVALUATOR_NAMES.length) {
    console.log(
      `${DIM}note: src/evaluators/deterministic.ts exports ${DETERMINISTIC_EVALUATORS.length} ` +
        `evaluators but this script summarises ${EVALUATOR_NAMES.length}. Update EVALUATOR_NAMES.${OFF}`,
    );
  }
  await runExperiment();
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
