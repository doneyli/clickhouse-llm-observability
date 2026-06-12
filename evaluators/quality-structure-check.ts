/**
 * Quality Structure Check — code evaluator (experiments)
 *
 * Target: experiment runs on the `coding-assistant-quality` dataset.
 * Complements the LLM-as-a-judge quality rubric with the objective half
 * of "is this a good coding answer": does it include code when the item
 * calls for it, in the right language, covering the key terms from the
 * reference answer? Code handles that half deterministically, so judge
 * spend goes only where semantic judgment is genuinely needed.
 *
 * Scores:
 *   code-block-present (BOOLEAN) — fenced code block present when the item expects code
 *   language-match     (BOOLEAN) — code fence language matches item metadata
 *   keyword-coverage   (NUMERIC) — fraction of reference-answer key terms present
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

function asText(value: any): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

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

// Categories whose answers should contain a code block.
const CODE_CATEGORIES = new Set([
  "impl",
  "debugging",
  "refactoring",
  "testing",
  "sql-conversion",
  "devops",
  "migrations",
]);

// Fence aliases per dataset language tag.
const LANGUAGE_ALIASES: Record<string, string[]> = {
  python: ["python", "py"],
  javascript: ["javascript", "js", "typescript", "ts", "jsx", "tsx"],
  sql: ["sql", "clickhouse", "postgresql", "mysql"],
  dockerfile: ["dockerfile", "docker"],
};

const STOPWORDS = new Set(
  ("the a an and or of to in on for with from by is are be should must use " +
    "using used it its this that as at not no any all can will when which " +
    "answer response must mention mentions explain explains include includes " +
    "correct correctly properly").split(" "),
);

function keyTerms(text: string): string[] {
  const seen = new Set<string>();
  for (const raw of text.toLowerCase().split(/[^a-z0-9_.]+/)) {
    const term = raw.replace(/^[._]+|[._]+$/g, "");
    if (term.length >= 4 && !STOPWORDS.has(term)) seen.add(term);
  }
  return Array.from(seen);
}

function evaluate(ctx: EvaluationContext): EvaluationResult {
  const output = asText(ctx.observation.output);
  const outputLower = output.toLowerCase();
  const expected = asObject(ctx.experiment?.itemExpectedOutput);
  const meta = asObject(ctx.experiment?.itemMetadata);

  const category = String(meta.category || "");
  const language = String(meta.language || "agnostic").toLowerCase();
  const expectsCode = CODE_CATEGORIES.has(category);

  const fences = Array.from(output.matchAll(/```([a-zA-Z0-9_+-]*)\n/g)).map(
    (m) => (m[1] || "").toLowerCase(),
  );
  const hasCode = fences.length > 0;

  const aliases = LANGUAGE_ALIASES[language];
  const languageMatch =
    !expectsCode || language === "agnostic" || !aliases
      ? true
      : fences.some((f) => aliases.includes(f));

  const reference = asText(expected.reference_answer || expected.criteria);
  const terms = keyTerms(reference);
  const covered = terms.filter((t) => outputLower.includes(t));
  const coverage = terms.length === 0 ? 1 : covered.length / terms.length;

  return {
    scores: [
      {
        name: "code-block-present",
        value: !expectsCode || hasCode,
        dataType: "BOOLEAN",
        comment: expectsCode
          ? hasCode
            ? `Found ${fences.length} fenced code block(s) for category '${category}'.`
            : `Category '${category}' expects code, but no fenced code block found.`
          : `Category '${category || "n/a"}' does not require code.`,
      },
      {
        name: "language-match",
        value: languageMatch,
        dataType: "BOOLEAN",
        comment: languageMatch
          ? language === "agnostic" || !expectsCode
            ? "No specific language required."
            : `Code fence language matches '${language}'.`
          : `Expected ${language} code, fences found: ${fences.join(", ") || "(untagged)"}.`,
      },
      {
        name: "keyword-coverage",
        value: Math.round(coverage * 100) / 100,
        dataType: "NUMERIC",
        comment:
          terms.length === 0
            ? "No reference terms available for this item."
            : `${covered.length}/${terms.length} key terms from the reference answer present. Missing: ${terms
                .filter((t) => !outputLower.includes(t))
                .slice(0, 10)
                .join(", ")}`,
        metadata: { term_count: terms.length, covered_count: covered.length },
      },
    ],
  };
}
