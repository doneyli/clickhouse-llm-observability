/**
 * Chain Gate Check — code evaluator (live observations)
 *
 * Target: SPAN observations on "text-to-sql" traces.
 * Companion to the in-pipeline gates in demos/text-to-sql/gates.py: the gate
 * ENFORCES policy before the next step runs; this evaluator SCORES every gate
 * verdict at ingest so the gate-fail rate is monitorable (and each retry attempt
 * is scored on its own).
 *
 * Why code, not LLM-as-a-judge: the verdict is already computed by the gate and
 * written to the span output ({verdict, reason, ...}). We just surface it as a
 * boolean score — deterministic, free, on 100% of gate spans.
 *
 * Scores:
 *   gate-pass (BOOLEAN) — true when the gate span's output.verdict === "pass"
 *
 * Non-gate spans (retrieve-context, gate-escalation, session-root, ...) have no
 * `verdict` in their output and yield no score — a defensive no-op, mirroring the
 * "no-sql" branch of sql-safety-guard.ts.
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

// Gate spans write a structured object to output; Langfuse may hand it back as an
// object or as a JSON string. Return the object with a `verdict` key, else null.
function asVerdictObject(value: any): Record<string, any> | null {
  let obj: any = value;
  if (typeof obj === "string") {
    try {
      obj = JSON.parse(obj);
    } catch {
      return null;
    }
  }
  if (obj && typeof obj === "object" && !Array.isArray(obj) && "verdict" in obj) {
    return obj as Record<string, any>;
  }
  return null;
}

function evaluate(ctx: EvaluationContext): EvaluationResult {
  const verdict = asVerdictObject(ctx.observation.output);

  // Not a gate span (no verdict) — nothing to score.
  if (verdict === null) {
    return { scores: [] };
  }

  const passed = String(verdict.verdict).toLowerCase() === "pass";
  const meta = (ctx.observation.metadata || {}) as Record<string, any>;

  return {
    scores: [
      {
        name: "gate-pass",
        value: passed,
        dataType: "BOOLEAN",
        comment: typeof verdict.reason === "string" ? verdict.reason : "(no reason given)",
        metadata: {
          gate_type: meta.gate_type,
          attempt: meta.attempt,
          max_attempts: meta.max_attempts,
          check: verdict.check,
        },
      },
    ],
  };
}
