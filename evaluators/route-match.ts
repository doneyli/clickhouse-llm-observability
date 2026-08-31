/**
 * Route Match — code evaluator (routing accuracy)
 *
 * Target: the router's `route-query` observation, primarily on EXPERIMENT runs
 * of the `query-router-accuracy` dataset (offline). Works online too once a
 * ground-truth route is available on the item.
 *
 * Why code, not an LLM judge: route equality is a deterministic check. A string
 * comparison never hallucinates, costs nothing per evaluation, and gives the
 * same verdict every time — exactly what you want for a routing-accuracy metric.
 * (The soft/ambiguous cases are covered separately by the categorical
 * `route-plausibility` LLM judge — see scripts/seed-router-judge.sh.)
 *
 * Scores:
 *   route-match  (BOOLEAN)     — chosen route === expected route (only when an
 *                                expected route exists, e.g. an experiment item)
 *   route-chosen (CATEGORICAL) — the route the router actually chose (always)
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
  if (typeof value === "object") return value;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return typeof parsed === "object" && parsed !== null ? parsed : {};
    } catch {
      return {};
    }
  }
  return {};
}

function routeOf(value: any): string {
  const obj = asObject(value);
  if (typeof obj.route === "string") return obj.route;
  // Fall back to a bare string route (e.g. output === "analytics_sql").
  if (typeof value === "string") return value.trim();
  return "";
}

function evaluate(ctx: EvaluationContext): EvaluationResult {
  const chosen = routeOf(ctx.observation.output);

  const scores: Score[] = [
    {
      name: "route-chosen",
      value: chosen || "unknown",
      dataType: "CATEGORICAL",
      comment: `Router chose '${chosen || "unknown"}'.`,
    },
  ];

  // route-match only makes sense when ground truth exists (experiment item).
  if (ctx.experiment !== undefined && ctx.experiment.itemExpectedOutput != null) {
    const expected = routeOf(ctx.experiment.itemExpectedOutput);
    const correct = expected !== "" && chosen === expected;
    scores.push({
      name: "route-match",
      value: correct,
      dataType: "BOOLEAN",
      comment: `Router chose '${chosen}', expected '${expected}'.`,
      metadata: { chosen, expected },
    });
  }

  return { scores };
}
