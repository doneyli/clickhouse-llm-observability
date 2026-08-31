/**
 * Consensus Margin Guard — code evaluator (live observations)
 *
 * Target: the `tally-votes` observation on `support-triage-parallel` traces.
 * Why code, not LLM-as-a-judge: the vote margin is already a number the app
 * wrote onto the aggregator's metadata. A deterministic check costs nothing,
 * never hallucinates, and is demoably different from the `correlated-vote-risk`
 * LLM judge that reads the *content* of the samples.
 *
 * The app writes the full tally onto `tally-votes` metadata
 * (`{votes: {"sig-a": 3, "sig-b": 1}, invalid, winner, margin, tie_break_used}`),
 * so this evaluator reads it directly — no need to pull in the N child
 * observations (an observation-level evaluator cannot do that).
 *
 * Scores:
 *   consensus_margin_ok (BOOLEAN)     — winning margin >= 2 (a comfortable win)
 *   consensus_margin    (NUMERIC)     — the margin itself, for charting
 *   consensus_shape     (CATEGORICAL) — unanimous | clear | narrow | tie | none
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

const MARGIN_THRESHOLD = 2;

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

function evaluate(ctx: EvaluationContext): EvaluationResult {
  const meta = asObject(ctx.observation.metadata);
  const votes = asObject(meta.votes);
  const counts = Object.values(votes)
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v))
    .sort((a, b) => b - a);

  const top = counts[0] ?? 0;
  const second = counts[1] ?? 0;
  // Prefer the app-recorded margin; fall back to computing it from the tally.
  const margin =
    typeof meta.margin === "number" ? meta.margin : top - second;
  const validCount = counts.reduce((a, b) => a + b, 0);
  const tieBreakUsed = Boolean(meta.tie_break_used);

  let shape: string;
  if (validCount === 0) shape = "none";
  else if (tieBreakUsed || (counts.length > 1 && top === second)) shape = "tie";
  else if (counts.length <= 1 && top === validCount) shape = "unanimous";
  else if (margin >= MARGIN_THRESHOLD) shape = "clear";
  else shape = "narrow";

  const ok = margin >= MARGIN_THRESHOLD;

  return {
    scores: [
      {
        name: "consensus_margin_ok",
        value: ok,
        dataType: "BOOLEAN",
        comment: `Winning margin ${margin} (threshold ${MARGIN_THRESHOLD}); shape=${shape}.`,
      },
      {
        name: "consensus_margin",
        value: margin,
        dataType: "NUMERIC",
        comment: `top ${top} vs second ${second} across ${validCount} valid votes.`,
        metadata: { votes, tie_break_used: tieBreakUsed },
      },
      {
        name: "consensus_shape",
        value: shape,
        dataType: "CATEGORICAL",
        comment: `Vote shape derived from the tally metadata.`,
      },
    ],
  };
}
