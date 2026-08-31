/**
 * One-shot prep: everything a presenter needs in the project before they talk.
 *
 * Order matters, and it is the order of the argument rather than the order of
 * the code: the comparison lands FIRST, because "the app worked and only one of
 * these two runs was measurable" is the claim everything else rests on. Scores
 * and dataset runs are only interesting once the audience accepts that the
 * trace shape was the difference.
 *
 * On `--quick` and what "the experiment" means here: the four conversations that
 * compare-traces does not use are run BY the experiment, out of the dataset, so
 * "run the remaining conversations" and "run the experiment" are one step rather
 * than two. Doing both would play those conversations twice for no extra
 * evidence. `--quick` therefore drops that step, leaving the two comparison
 * sessions to be scored — enough to present the trace-shape argument and the
 * session score, without the dataset run's spend.
 */
import "../src/instrumentation.js";

import { pathToFileURL } from "node:url";

import { flushTraces } from "../src/instrumentation.js";
import { LANGFUSE_BASE_URL, verifyProject } from "../src/env.js";
import { CONVERSATIONS } from "../src/conversations.js";
import { sessionUrl } from "./run-conversation.js";
import { DATASET_NAME, seedDataset } from "./seed-dataset.js";
import { compareTraces } from "./compare-traces.js";
import { runExperiment } from "./run-experiment.js";
import { scoreLiveSessions, SCORE_NAME } from "./score-live-sessions.js";

const BOLD = "[1m";
const DIM = "[2m";
const RED = "[31m";
const OFF = "[0m";

function step(n: number, title: string): void {
  console.log("");
  console.log(`${BOLD}══ ${n}. ${title}${OFF}`);
}

async function main(): Promise<void> {
  const quick = process.argv.slice(2).includes("--quick");

  console.log("");
  console.log(`${BOLD}Northwind Grocers — Ask AI demo prep${OFF}`);
  console.log(`  ${quick ? "quick mode (no dataset run)" : "full mode"}   target ${LANGFUSE_BASE_URL}`);

  step(1, "verify the project");
  await verifyProject();

  step(2, `seed the ${DATASET_NAME} dataset`);
  await seedDataset();

  step(3, "the comparison: same conversation, two instrumentations");
  const comparison = await compareTraces();

  if (!quick) {
    step(4, "the dataset run: every conversation, graded");
    await runExperiment();
  } else {
    console.log("");
    console.log(`${DIM}Skipping the dataset run (--quick). Run it later: tsx scripts/run-experiment.ts${OFF}`);
  }

  step(quick ? 4 : 5, "session-level scores");
  // Enough headroom for the two comparison sessions plus one per conversation.
  await scoreLiveSessions(quick ? 4 : CONVERSATIONS.length + 4);

  // -------------------------------------------------------- the walkthrough ---
  console.log("");
  console.log(`${BOLD}════ What to open in Langfuse, in this order ════${OFF}`);
  console.log("");
  console.log(
    `${BOLD} 1.${OFF} Traces, filtered to tag ${BOLD}compare:broken${OFF} — every row a different name,`,
  );
  console.log(`    every Input and Output column blank. Nothing here can be filtered or targeted.`);
  console.log(`    ${DIM}${LANGFUSE_BASE_URL} → Tracing → Traces${OFF}`);
  console.log("");
  console.log(
    `${BOLD} 2.${OFF} Open one of those traces and expand the generation. Input and output are null —`,
  );
  console.log(`    this is the defect that blocks every evaluator, not just an inconvenience.`);
  console.log("");
  console.log(`${BOLD} 3.${OFF} The good session, top to bottom. One trace per turn, each showing that`);
  console.log(`    turn's question and answer only.`);
  console.log(`    ${DIM}${await sessionUrl(comparison.goodSessionId)}${OFF}`);
  console.log("");
  console.log(`${BOLD} 4.${OFF} The broken session, for contrast — it is EMPTY. Those traces carry no`);
  console.log(`    sessionId at all, because it was written to metadata instead of propagated.`);
  console.log(`    ${DIM}${await sessionUrl(comparison.brokenSessionId)}${OFF}`);
  console.log("");
  console.log(`${BOLD} 5.${OFF} Scores → ${BOLD}${SCORE_NAME}${OFF}. A score attached to a SESSION, with no`);
  console.log(`    traceId. No managed evaluator can produce this: the question spans turns.`);
  console.log("");
  if (!quick) {
    console.log(`${BOLD} 6.${OFF} Datasets → ${BOLD}${DATASET_NAME}${OFF} → the newest run. Five conversations,`);
    console.log(`    four deterministic evaluators, and the pass rates printed above with their`);
    console.log(`    real denominators.`);
    console.log("");
  }
  console.log(
    `${DIM}Talk track: the two runs called the same model with the same tools and answered ` +
      `equally well. Only one of them can be measured.${OFF}`,
  );
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
