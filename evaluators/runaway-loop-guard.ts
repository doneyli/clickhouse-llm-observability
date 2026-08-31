/**
 * Runaway Loop Guard — code evaluator (live observations)
 *
 * Target: the AGENT root observation of `tune-clickhouse-query` traces
 * (demos/slow-query-tuner). Why code, not LLM-as-a-judge: whether a run ended on
 * a backstop is a deterministic fact recorded in the trace, not a judgement.
 *
 * The autonomous-loop failure mode is a run that never self-terminates and gets
 * stopped by a cap (error_max_turns / error_max_budget_usd / error_watchdog).
 * This flags exactly those so the turn-count Monitor and dashboards can slice on
 * them ("self-assessment failed, the backstop did the stopping").
 *
 * Scores:
 *   cap_terminated       (BOOLEAN)     — run ended on a backstop, not on finish
 *   termination_class    (CATEGORICAL) — self_completed | self_gave_up |
 *                                        implicit | blocked | killed | capped | unknown
 */

type EvaluationContext = {
  observation: { input: any; output: any; metadata: any };
  experiment: { itemExpectedOutput: any; itemMetadata: any } | undefined;
};

type Score = {
  name: string;
  value: number | string | boolean;
  dataType: "NUMERIC" | "CATEGORICAL" | "BOOLEAN" | "TEXT";
  comment?: string;
  metadata?: Record<string, unknown>;
};

type EvaluationResult = { scores: Score[] };

function asObject(value: any): Record<string, any> {
  if (value == null) return {};
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      return {};
    }
  }
  return typeof value === "object" ? value : {};
}

function classOf(reason: string): string {
  if (reason.startsWith("error_")) return "capped";
  if (reason === "self_completed") return "self_completed";
  if (reason === "self_gave_up") return "self_gave_up";
  if (reason === "self_completed_implicit") return "implicit";
  if (reason === "blocked_hitl_denied") return "blocked";
  if (reason === "killed") return "killed";
  return "unknown";
}

function evaluate(ctx: EvaluationContext): EvaluationResult {
  const meta = asObject(ctx.observation.metadata);
  const out = asObject(ctx.observation.output);
  const reason: string =
    meta.termination_reason || out.termination_reason || "unknown";

  const capped = reason.startsWith("error_");

  return {
    scores: [
      {
        name: "cap_terminated",
        value: capped,
        dataType: "BOOLEAN",
        comment: capped
          ? `Run stopped by a backstop (${reason}) — self-assessment failed.`
          : `Run self-terminated (${reason}).`,
      },
      {
        name: "termination_class",
        value: classOf(reason),
        dataType: "CATEGORICAL",
        comment: `termination_reason=${reason}`,
        metadata: {
          turns_used: meta.turns_used,
          cost_usd: meta.cost_usd,
        },
      },
    ],
  };
}
