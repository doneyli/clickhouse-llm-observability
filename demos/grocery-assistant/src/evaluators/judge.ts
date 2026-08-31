/**
 * The evaluator to build LAST — one LLM-as-a-judge, for the one thing the
 * deterministic four cannot reach.
 *
 * The four checks in `deterministic.ts` all answer questions about system state:
 * is it in the cart, was it really ordered, does it carry the tag, does the
 * discount still apply. Those are settled exactly and for free. This one is not:
 * "when an item was unavailable, was the shopper told plainly and offered a real
 * alternative — or was it left vague?" requires reading meaning, so it needs a
 * judge. That is the whole test for reaching for one.
 *
 * The prompt below is deliberately laid out in the five parts Langfuse
 * recommends, because the structure is the lesson:
 *   1. Context — what the system is and what it cannot do
 *   2. One precise criterion, including what to IGNORE
 *   3. Labeled examples with reasons  (kept minimal — the docs advise starting
 *      without them and adding only if accuracy is short)
 *   4. Reasoning first, verdict last  ("measurably improves judge accuracy")
 *   5. An explicit way out — `unknown` rather than a forced guess
 * — https://langfuse.com/academy/evaluate/writing-evaluators
 *
 * Verdict is a three-way label, not a 1-10 score: "scale points don't get
 * applied consistently, even for a human it is hard to say what separates a 6
 * from a 7", and a pass/fail verdict is the only kind you can later check for
 * agreement against your own labels.
 */
import { anthropic } from "@ai-sdk/anthropic";
import { generateObject } from "ai";
import { startActiveObservation } from "@langfuse/tracing";
import { z } from "zod";

import { JUDGE_MODEL } from "../env.js";
import { PRODUCTS, getProduct } from "../catalog.js";
import type { EvalContext, Verdict } from "./deterministic.js";

const VerdictSchema = z.object({
  reasoning: z
    .string()
    .describe("One or two sentences. Quote the assistant's own words where they matter."),
  verdict: z.enum(["pass", "fail", "unknown"]),
});

const CRITERION = `
# 1. Context
You are evaluating one reply from the shopping assistant for a grocery
chain. The assistant searches a live catalog. Some items are genuinely out of
stock, and the catalog often contains a suitable substitute. The assistant cannot
place orders, check other stores, or promise a restock date.

# 2. One precise criterion, including what to ignore
Criterion: when the shopper asked for an item that is OUT OF STOCK, the reply must
(a) say plainly that the item is unavailable, and (b) either offer a specific
alternative from the catalog or state that there is no good substitute.

A reply FAILS if it:
- implies the item is available, or that it has been added to the cart
- answers around the unavailability without ever saying it
- offers to "check back later", "keep an eye on it", or similar, INSTEAD of saying
  it is unavailable now
- invents a substitute that was not in the search results

Ignore entirely: tone, warmth, emoji, length, formatting, whether the shopper
seemed satisfied, and whether the substitute was the one you would have picked.
Only honesty about availability is in scope.

# 3. Labeled example
Shopper: "Add two avocados please."
Reply: "I've added those for you! Anything else?"   (avocados are out of stock)
Reasoning: claims the item was added when it is unavailable, and never mentions
the stock status.
Verdict: fail

# 4. Reasoning first, verdict last
Write your reasoning first, then the verdict.

# 5. A way out
If the reply does not concern an out-of-stock item at all, or the transcript does
not let you tell, answer "unknown". Do not guess.
`.trim();

/** SKUs the catalog currently has no stock for. */
const OUT_OF_STOCK = PRODUCTS.filter((p) => !p.inStock).map((p) => p.sku);

/**
 * Did the reply handle an unavailable item honestly?
 *
 * Cheap pre-check first: if nothing out of stock is plausibly in play, we skip the
 * model call entirely and return not-applicable. This is the "deterministic
 * pre-screen in front of a judge" pattern — Langfuse names it as the common way to
 * cut evaluation cost, and here it means most turns never pay for a judge at all.
 * Note it is a pattern in application code, not a Langfuse feature: there is no
 * built-in wiring that lets one evaluator gate another.
 */
export async function unavailabilityObscured(ctx: EvalContext): Promise<Verdict> {
  const name = "unavailability-obscured";

  const oosMentioned = OUT_OF_STOCK.filter((sku) => {
    const product = getProduct(sku);
    if (!product) return false;
    const needle = product.name.toLowerCase().split(",")[0]!.trim();
    const haystack = `${ctx.message}\n${ctx.answer}`.toLowerCase();
    return haystack.includes(needle) || ctx.answer.includes(sku);
  });

  if (oosMentioned.length === 0) {
    return {
      name,
      passed: true,
      applicable: false,
      comment: "No out-of-stock item was in play, so there was nothing to be honest about.",
    };
  }

  const outOfStockNames = oosMentioned
    .map((sku) => {
      const p = getProduct(sku)!;
      const sub = PRODUCTS.find((c) => c.substituteFor === sku && c.inStock);
      return `${p.name} (${sku}) — catalog substitute: ${sub ? `${sub.name} (${sub.sku})` : "none"}`;
    })
    .join("\n");

  // The judge runs inside its own observation so its cost and latency never read
  // as the assistant's. Evaluator spend that hides inside application spend is
  // how eval budgets get mis-attributed.
  return await startActiveObservation(`judge:${name}`, async (span) => {
    const prompt = [
      CRITERION,
      "",
      "=== OUT-OF-STOCK ITEMS IN PLAY ===",
      outOfStockNames,
      "",
      "=== SHOPPER SAID ===",
      ctx.message,
      "",
      "=== ASSISTANT REPLIED ===",
      ctx.answer,
    ].join("\n");

    span.update({ input: { criterion: name, message: ctx.message, answer: ctx.answer } });

    try {
      const { object } = await generateObject({
        model: anthropic(JUDGE_MODEL),
        schema: VerdictSchema,
        prompt,
        telemetry: { functionId: `judge-${name}` },
      });
      span.update({ output: object });

      // `unknown` is deliberately NOT a failure. A judge forced to choose when it
      // cannot tell produces noise, and noise in an eval is worse than a gap.
      return {
        name,
        passed: object.verdict !== "fail",
        applicable: object.verdict !== "unknown",
        comment:
          object.verdict === "unknown"
            ? `Judge could not tell: ${object.reasoning}`
            : object.reasoning,
      };
    } catch (error) {
      span.update({ output: { error: String(error) } });
      // Fail OPEN on infrastructure errors, and say so. A judge that silently
      // scores 0 when the API times out manufactures regressions.
      return {
        name,
        passed: true,
        applicable: false,
        comment: `Judge did not run (${String(error).slice(0, 120)}). Not counted.`,
      };
    }
  });
}

export const JUDGE_EVALUATORS = [unavailabilityObscured] as const;

export async function runJudges(ctx: EvalContext): Promise<Verdict[]> {
  return await Promise.all(JUDGE_EVALUATORS.map((fn) => fn(ctx)));
}
