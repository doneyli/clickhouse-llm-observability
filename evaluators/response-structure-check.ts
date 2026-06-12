/**
 * Response Structure Check — code evaluator (live observations)
 *
 * Target: GENERATION observations on demo traces (text-to-sql, vector-rag).
 * Why code, not LLM-as-a-judge: structural defects — empty responses,
 * unclosed code fences, leaked prompt-template placeholders, truncation —
 * are mechanical properties. Code checks them exactly; a judge would just
 * approximate the same regexes at 1000x the cost.
 *
 * Scores:
 *   output-present     (BOOLEAN) — non-empty response
 *   structure-clean    (BOOLEAN) — no unclosed fences / template leaks / truncation
 *   response-length    (NUMERIC) — character count (spot drift on a dashboard)
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
  // LangChain generations often arrive as objects; pull common text fields.
  if (typeof value === "object") {
    const candidate =
      value.content ?? value.text ?? value.output ?? value.completion;
    if (typeof candidate === "string") return candidate;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function evaluate(ctx: EvaluationContext): EvaluationResult {
  const output = asText(ctx.observation.output).trim();
  const issues: string[] = [];

  if (output.length === 0) {
    return {
      scores: [
        {
          name: "output-present",
          value: false,
          dataType: "BOOLEAN",
          comment: "Response is empty.",
        },
        {
          name: "structure-clean",
          value: false,
          dataType: "BOOLEAN",
          comment: "Empty response.",
        },
        { name: "response-length", value: 0, dataType: "NUMERIC" },
      ],
    };
  }

  // Unclosed code fence: odd number of ``` markers.
  const fenceCount = (output.match(/```/g) || []).length;
  if (fenceCount % 2 !== 0) issues.push("unclosed code fence");

  // Leaked prompt-template placeholders like {context} or {{question}}.
  if (/\{\{?\s*(context|question|query|input|chat_history)\s*\}?\}/i.test(output)) {
    issues.push("prompt template placeholder leaked into output");
  }

  // Likely truncation: ends mid-sentence on a connector word or comma.
  if (/[,:;]\s*$|\b(the|a|an|and|or|to|of|in|with)\s*$/i.test(output)) {
    issues.push("response appears truncated");
  }

  return {
    scores: [
      {
        name: "output-present",
        value: true,
        dataType: "BOOLEAN",
      },
      {
        name: "structure-clean",
        value: issues.length === 0,
        dataType: "BOOLEAN",
        comment:
          issues.length === 0
            ? "No structural defects detected."
            : `Defects: ${issues.join("; ")}.`,
        metadata: { issues },
      },
      {
        name: "response-length",
        value: output.length,
        dataType: "NUMERIC",
        comment: `${output.length} characters.`,
      },
    ],
  };
}
