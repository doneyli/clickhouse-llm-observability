/**
 * Turning evaluator verdicts into Langfuse scores.
 *
 * Three things here are worth copying into a real application:
 *
 * 1. A score references EXACTLY ONE subject — an observation, a trace, a session,
 *    or a dataset run. `sessionId` with no `traceId` is the only way to attach a
 *    number to a whole conversation, and no Langfuse-managed evaluator can
 *    produce one, because the server cannot know when a conversation has ended.
 * 2. Boolean scores are `1` / `0` in the JS SDK, and `dataType` is REQUIRED for
 *    them — a bare numeric is inferred as NUMERIC, which quietly turns a pass rate
 *    into a mean of a number nobody defined.
 * 3. A verdict that was NOT APPLICABLE is not written at all. Writing it as a pass
 *    inflates the metric and — worse — makes it insensitive: a regression on the
 *    items that were never checked cannot move a number that already reads 100%.
 *    The denominator has to mean something.
 */
import { LangfuseClient } from "@langfuse/client";

import type { Verdict } from "./evaluators/deterministic.js";

let client: LangfuseClient | undefined;

export function getLangfuseClient(): LangfuseClient {
  client ??= new LangfuseClient();
  return client;
}

export type ScoreTarget =
  | { kind: "observation"; traceId: string; observationId: string }
  | { kind: "trace"; traceId: string }
  | { kind: "session"; sessionId: string };

/**
 * Write one verdict as a Langfuse score. Returns whether anything was written.
 * Not-applicable verdicts are skipped on purpose (see note 3 above).
 */
export async function recordVerdict(
  verdict: Verdict,
  target: ScoreTarget,
): Promise<boolean> {
  if (!verdict.applicable) return false;

  const base = {
    name: verdict.name,
    // Boolean scores are numeric 1/0 in the JS SDK.
    value: verdict.passed ? 1 : 0,
    dataType: "BOOLEAN" as const,
    comment: verdict.comment.slice(0, 1000),
  };

  const lf = getLangfuseClient();
  if (target.kind === "session") {
    lf.score.create({ ...base, sessionId: target.sessionId });
  } else if (target.kind === "observation") {
    // Always send BOTH ids when scoring an observation.
    lf.score.create({
      ...base,
      traceId: target.traceId,
      observationId: target.observationId,
    });
  } else {
    lf.score.create({ ...base, traceId: target.traceId });
  }
  return true;
}

export async function recordVerdicts(
  verdicts: Verdict[],
  target: ScoreTarget,
): Promise<number> {
  let written = 0;
  for (const verdict of verdicts) {
    if (await recordVerdict(verdict, target)) written += 1;
  }
  return written;
}

/**
 * A NUMERIC score on the whole conversation — the pass rate of one check across
 * every turn where it applied. `applicableTurns` is reported in the comment
 * because a rate is meaningless without its denominator: "3/3 turns" and
 * "3/12 turns" are very different claims about the same 100%.
 */
export async function recordSessionRate(args: {
  sessionId: string;
  name: string;
  passedTurns: number;
  applicableTurns: number;
  detail?: string;
}): Promise<boolean> {
  const { sessionId, name, passedTurns, applicableTurns, detail } = args;
  if (applicableTurns === 0) return false;

  const rate = passedTurns / applicableTurns;
  getLangfuseClient().score.create({
    sessionId,
    name,
    value: Number(rate.toFixed(3)),
    dataType: "NUMERIC",
    comment:
      `${passedTurns}/${applicableTurns} turn(s) where this applied passed` +
      (detail ? `. ${detail}` : "."),
  });
  return true;
}

/** Flush scores. The client buffers, so short-lived scripts must call this. */
export async function flushScores(): Promise<void> {
  await getLangfuseClient().flush();
}
