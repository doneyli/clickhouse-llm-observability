import { DEMO_POLICIES } from "./policies";
import type { ClearanceLevel, Classification, EvaluationResult, LangfuseTrace, Subject } from "./types";

const CLEARANCE_RANK: Record<ClearanceLevel, number> = {
  "ceo-only":   2,
  "restricted": 1,
  "general":    0,
};

function classificationRank(c: Classification): number {
  return CLEARANCE_RANK[c as ClearanceLevel] ?? -1;
}

const ALLOW_CLEARANCE = DEMO_POLICIES.find((p) => p.id === "allow-clearance-ge-classification")!;
const DENY_DEFAULT = DEMO_POLICIES.find((p) => p.id === "deny-default")!;

export function evaluate(subject: Subject, trace: LangfuseTrace): EvaluationResult {
  const meta = trace.metadata;

  // Traces without RLS metadata are visible to all (no classification = public).
  if (!meta?.classification || !meta?.team) {
    return { allow: true, matchedRule: "no-metadata", reason: "Trace has no RLS metadata — treated as public." };
  }

  // Clearance is a hard ceiling. A subject may only see traces at or below
  // their clearance level. Team is metadata for display/audit only — it never
  // grants access on its own, so it can never override the classification gate.
  if (CLEARANCE_RANK[subject.clearance] >= classificationRank(meta.classification)) {
    return {
      allow: true,
      matchedRule: ALLOW_CLEARANCE.id,
      reason: ALLOW_CLEARANCE.reason,
    };
  }

  // deny-default
  return {
    allow: false,
    matchedRule: DENY_DEFAULT.id,
    reason: DENY_DEFAULT.reason,
  };
}

export function evaluateBatch(
  subject: Subject,
  traces: LangfuseTrace[],
): Array<LangfuseTrace & { _rls: EvaluationResult }> {
  return traces.map((trace) => ({ ...trace, _rls: evaluate(subject, trace) }));
}
