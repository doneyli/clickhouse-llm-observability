/**
 * The evaluators to build FIRST — all four deterministic, none of them an LLM.
 *
 * The organising idea comes straight from Langfuse's guidance on writing
 * evaluators, quoting Anthropic:
 *
 *   "grade the outcome in the environment, not the claim in the transcript"
 *   — https://langfuse.com/academy/evaluate/writing-evaluators
 *
 * The example given there is an agent that says "Your refund of $200 has been
 * processed, you're all set!" while no refund exists. A judge reading the
 * transcript believes it. A check against the refunds table catches every case.
 *
 * A grocery assistant has the same shape, and a cart is the refunds table: the
 * assistant SAYS it added oat milk; the cart either contains oat milk or it does
 * not. That question is free to answer, exact, and never drifts — so it is the
 * first thing worth measuring, and it needs no judge at all.
 *
 * Why deterministic first, in Langfuse's words:
 *   "If the thing you want to evaluate is — visible in your system (a row was
 *   written, a ticket was closed, an order was placed) — a code evaluator can
 *   often settle the question exactly, and is faster and a lot cheaper to run.
 *   Prefer these over LLM-as-a-judge evaluators where you can."
 *
 * Naming follows "Name after what broke": `unverified-cart-claim` beats
 * `cart_quality`, and `fabricated-purchase-history` beats `groundedness`.
 * Every score here is BOOLEAN, because a pass/fail verdict is verifiable and a
 * 1-10 score is not — you can count how often a boolean is right.
 */
import {
  DIETARY_VOCABULARY,
  ORDER_HISTORY,
  getProduct,
  type Product,
} from "../catalog.js";
import { extractSkus } from "../assistant.js";

export type Verdict = {
  name: string;
  passed: boolean;
  /** Names the specific thing that broke. Read aloud in demos, so be precise. */
  comment: string;
  /** False when the conversation gave this evaluator nothing to check. */
  applicable: boolean;
};

/** Everything an evaluator needs about one turn or one conversation. */
export type EvalContext = {
  /** The shopper's message for this turn. */
  message: string;
  /** What the assistant replied. */
  answer: string;
  /** SKUs actually in the cart AFTER the turn. */
  cartSkus: string[];
  /** Tools the assistant called this turn. */
  toolsCalled: string[];
  /** Every prior turn, oldest first. */
  history: Array<{ role: "user" | "assistant"; content: string }>;
  /** Discount total the assistant quoted, if any, in cents. */
  quotedDiscountCents?: number;
  /** The true current discount total in cents. */
  actualDiscountCents?: number;
};

const notApplicable = (name: string, why: string): Verdict => ({
  name,
  passed: true,
  comment: why,
  applicable: false,
});

// ---------------------------------------------------------------------------
// 1. THE FIRST EVALUATOR. Outcome in the environment, not the claim.
// ---------------------------------------------------------------------------
/**
 * Sentences in which the assistant claims a completed addition.
 *
 * Deliberately narrow. An earlier, looser version matched a bare "added"
 * anywhere, which combined with the whole-answer SKU fallback below to produce a
 * confident FAIL on answers that said the *opposite*: "I haven't added anything
 * to your cart yet — we were still confirming quantities! Baby Spinach
 * (PRD-1002)…" was read as a claim to have added PRD-1002. A false failure on the
 * headline evaluator is worse than a miss, because it teaches people to distrust
 * the board.
 */
const ADD_CLAIM_RE =
  /\b(?:i(?:'ve| have)?\s+(?:just\s+)?(?:added|put|dropped)|i\s+added|added\s+to\s+your\s+cart|added\s+the\s+following|(?:^|\n)\s*added\b)/gi;

/**
 * Negations and offers that appear BEFORE a match and cancel it. An offer to add
 * ("shall I add", "ready to add", "I can add") is not a claim to have added, and
 * scoring it as one punishes the assistant for asking permission.
 */
const CLAIM_CANCELLERS =
  /\b(?:not|never|nothing|n't|have\s+not|has\s+not|had\s+not|did\s+not|cannot|can't|unable|shall\s+i|should\s+i|want\s+me\s+to|like\s+me\s+to|ready\s+to|happy\s+to|i\s+can|i\s+could|i\s+will|i'll|before\s+i)\s*$/i;

/** Language that means "this item was deliberately NOT added", and rightly so. */
const EXCUSE_RE =
  /\b(?:out of stock|unavailable|not added|couldn't add|could not add|didn't add|did not add|instead|substitute|alternative|sold out)\b/;

/**
 * The clause containing `index` — bounded by sentence punctuation, newlines, or a
 * contrastive conjunction ("but", "however", "though"). Clause-level scoping is
 * what stops a reason attached to one item from excusing another in the same
 * sentence.
 *
 * Note `|` is deliberately NOT a boundary. Assistants report cart changes as
 * markdown tables, and a pipe splits a ROW into cells — which isolated the SKU
 * into its own cell and cut it off from the reason sitting one cell to the right
 * on the same row. A table row is the unit here; a newline ends it.
 */
function clauseAround(text: string, index: number): string {
  const BOUNDARY = /[.!?\n;]|\bbut\b|\bhowever\b|\bthough\b|\bwhereas\b/gi;
  let start = 0;
  let end = text.length;
  for (const m of text.matchAll(BOUNDARY)) {
    const at = m.index ?? 0;
    if (at < index) start = at + m[0].length;
    else {
      end = at;
      break;
    }
  }
  return text.slice(start, end);
}

/** Add-claim sentences, with negated and hypothetical ones removed. */
function completedAddClaims(answer: string): string[] {
  return [...answer.matchAll(ADD_CLAIM_RE)]
    .filter((m) => {
      const start = m.index ?? 0;
      // Look back far enough to catch "I haven't yet added" and "shall I add".
      const before = answer.slice(Math.max(0, start - 40), start);
      return !CLAIM_CANCELLERS.test(before);
    })
    .map((m) => {
      const start = m.index ?? 0;
      // The claim plus the rest of its sentence, so SKUs named inline are seen.
      const rest = answer.slice(start);
      const end = rest.search(/[.!?\n]/);
      return end === -1 ? rest : rest.slice(0, end);
    });
}

/**
 * The assistant claimed it added something. Did the cart change accordingly?
 *
 * This is the highest-value first evaluator for a shopping assistant, and the
 * cheapest: no model call, no rubric, no calibration. It catches the failure a
 * shopper notices immediately — being told the basket contains something it does
 * not — which no amount of fluent language can paper over.
 */
export function unverifiedCartClaim(ctx: EvalContext): Verdict {
  const name = "unverified-cart-claim";
  const claims = completedAddClaims(ctx.answer);
  if (claims.length === 0) {
    return notApplicable(
      name,
      "The assistant made no claim to have added anything. (Offers to add, and " +
        "statements that nothing was added, are not claims.)",
    );
  }

  // SKUs named inside the claim sentence itself are the clearest signal. But
  // assistants very often write "I've added your top four items:" and then put
  // the SKUs in a markdown table on the following lines, which left this check
  // reporting not-applicable on exactly the turns it exists to police. So when
  // the claim sentence names nothing, fall back to every SKU in the answer.
  const inClaimSentence = [...new Set(claims.flatMap((c) => extractSkus(c)))];
  const claimedSkus =
    inClaimSentence.length > 0 ? inClaimSentence : extractSkus(ctx.answer);

  if (claimedSkus.length === 0) {
    return notApplicable(
      name,
      "The assistant claimed an addition but named no SKU anywhere, so there is nothing to " +
        "verify. An unverifiable claim is also a product problem — consider requiring a SKU " +
        "in add confirmations.",
    );
  }

  // A SKU the answer explicitly says was NOT added (out of stock, declined,
  // offered as an alternative) is not a false claim — it is the assistant being
  // honest, which is the behaviour we want. Excuse those before failing.
  const excused = claimedSkus.filter((sku) => {
    const idx = ctx.answer.indexOf(sku);
    if (idx === -1) return false;
    // Only the CLAUSE containing the mention counts. A wider window reads
    // out-of-stock language about a different item as an excuse for this one:
    // "I haven't added the avocados since they're out of stock, but I've added
    // Baby Spinach (PRD-1002)" wrongly excused the spinach at ±200 chars.
    return EXCUSE_RE.test(clauseAround(ctx.answer, idx).toLowerCase());
  });

  const missing = claimedSkus.filter(
    (sku) => !ctx.cartSkus.includes(sku) && !excused.includes(sku),
  );
  if (missing.length > 0) {
    return {
      name,
      passed: false,
      applicable: true,
      comment:
        `The assistant said it added ${missing.join(", ")}, but the cart does not contain ` +
        `${missing.length === 1 ? "it" : "them"}. Cart holds: ${ctx.cartSkus.join(", ") || "(empty)"}.`,
    };
  }
  const verified = claimedSkus.filter((sku) => !excused.includes(sku));
  return {
    name,
    passed: true,
    applicable: true,
    comment:
      `Every item claimed as added (${verified.join(", ") || "none"}) is in the cart.` +
      (excused.length
        ? ` ${excused.join(", ")} named but explicitly not added — correctly excused.`
        : ""),
  };
}

// ---------------------------------------------------------------------------
// 2. Fabricated purchase history — the failure that started this whole demo.
// ---------------------------------------------------------------------------
const HISTORY_QUESTION_RE =
  /\b(usual|usually|always buy|buy again|last time|before|previous|my history|reorder|re-order|restock)\b/i;

/**
 * When the shopper asks about what they usually buy, every product the assistant
 * presents as a past purchase must actually appear in their order history.
 *
 * An assistant with no order-history tool has nothing to answer from, so it
 * invents plausible groceries — and the answer reads perfectly. This is exactly
 * the class of failure that is invisible to a fluency judge and trivial for a
 * three-line check against the real orders.
 */
export function fabricatedPurchaseHistory(ctx: EvalContext): Verdict {
  const name = "fabricated-purchase-history";
  const asksAboutHistory =
    HISTORY_QUESTION_RE.test(ctx.message) || ctx.toolsCalled.includes("get_order_history");
  if (!asksAboutHistory) {
    return notApplicable(name, "The turn was not about past purchases.");
  }
  const everBought = new Set(ORDER_HISTORY.flatMap((o) => o.skus));
  const presented = extractSkus(ctx.answer);
  if (presented.length === 0) {
    return notApplicable(name, "No specific products were presented as past purchases.");
  }
  const neverBought = presented.filter((sku) => !everBought.has(sku));
  if (neverBought.length > 0) {
    return {
      name,
      passed: false,
      applicable: true,
      comment:
        `Presented ${neverBought.join(", ")} in answer to a question about past purchases, ` +
        `but ${neverBought.length === 1 ? "it has" : "they have"} never been ordered. ` +
        `Actually purchased: ${[...everBought].join(", ")}.`,
    };
  }
  return {
    name,
    passed: true,
    applicable: true,
    comment: `All ${presented.length} product(s) cited were genuinely purchased before.`,
  };
}

// ---------------------------------------------------------------------------
// 3. A constraint stated once, dropped later.
// ---------------------------------------------------------------------------
const DIET_PHRASES: Array<{ phrase: RegExp; tag: (typeof DIETARY_VOCABULARY)[number] }> = [
  { phrase: /\b(gluten[-\s]?free|no gluten|coeliac|celiac)\b/i, tag: "gluten_free" },
  { phrase: /\b(dairy[-\s]?free|no dairy|lactose)\b/i, tag: "dairy_free" },
  { phrase: /\bvegan\b/i, tag: "vegan" },
  { phrase: /\bvegetarian\b/i, tag: "vegetarian" },
  { phrase: /\b(nut[-\s]?free|no nuts|nut allerg)\b/i, tag: "nut_free" },
  { phrase: /\b(low[-\s]?sodium|low[-\s]?salt)\b/i, tag: "low_sodium" },
];

/** Dietary requirements the shopper has stated at any point in the conversation. */
export function statedDietaryTags(ctx: EvalContext): string[] {
  const shopperText = [
    ...ctx.history.filter((m) => m.role === "user").map((m) => m.content),
    ctx.message,
  ].join("\n");
  return DIET_PHRASES.filter(({ phrase }) => phrase.test(shopperText)).map(({ tag }) => tag);
}

/**
 * Once stated, a dietary requirement applies for the rest of the conversation
 * whether or not the shopper repeats it. This is the cross-turn failure that a
 * per-turn evaluator cannot see: turn 6 in isolation looks fine, and is only
 * wrong in the light of turn 1.
 */
export function droppedDietaryConstraint(ctx: EvalContext): Verdict {
  const name = "dropped-dietary-constraint";
  const required = statedDietaryTags(ctx);
  if (required.length === 0) {
    return notApplicable(name, "The shopper stated no dietary requirement.");
  }
  const recommended = extractSkus(ctx.answer)
    .map((sku) => getProduct(sku))
    .filter((p): p is Product => p !== undefined);
  if (recommended.length === 0) {
    return notApplicable(
      name,
      `Requirement(s) ${required.join(", ")} in force, but no specific product was recommended.`,
    );
  }
  const violations = recommended
    .filter((p) => !required.every((tag) => p.dietaryTags.includes(tag)))
    .map((p) => {
      const missing = required.filter((tag) => !p.dietaryTags.includes(tag));
      return `${p.name} (${p.sku}) is not ${missing.join(" or ")}`;
    });
  if (violations.length > 0) {
    return {
      name,
      passed: false,
      applicable: true,
      comment:
        `The shopper requires ${required.join(", ")}, stated earlier in the conversation. ` +
        violations.join("; ") +
        ".",
    };
  }
  return {
    name,
    passed: true,
    applicable: true,
    comment: `All ${recommended.length} recommendation(s) satisfy ${required.join(", ")}.`,
  };
}

// ---------------------------------------------------------------------------
// 4. A discount total that stopped being true.
// ---------------------------------------------------------------------------
/**
 * An offer that applied when it was clipped can stop applying once the basket
 * changes. Quoting the old total is a small, specific, checkable lie — and the
 * shopper finds out at checkout, which is the worst possible moment.
 */
export function staleDiscountQuoted(ctx: EvalContext): Verdict {
  const name = "stale-discount-quoted";
  if (ctx.quotedDiscountCents === undefined || ctx.actualDiscountCents === undefined) {
    return notApplicable(name, "No discount total was quoted this turn.");
  }
  if (ctx.quotedDiscountCents !== ctx.actualDiscountCents) {
    return {
      name,
      passed: false,
      applicable: true,
      comment:
        `Quoted a discount of $${(ctx.quotedDiscountCents / 100).toFixed(2)} but the offers that ` +
        `currently apply to this basket total $${(ctx.actualDiscountCents / 100).toFixed(2)}.`,
    };
  }
  return {
    name,
    passed: true,
    applicable: true,
    comment: `Quoted discount matches the offers that currently apply.`,
  };
}

/**
 * The full deterministic board, in the order worth building them.
 *
 * All four are reference-free: they compare the answer against system state, not
 * against a saved expected output. That is what makes them safe to run on live
 * production traffic, where there is no ground truth — reference-based evaluators
 * structurally cannot.
 */
export const DETERMINISTIC_EVALUATORS = [
  unverifiedCartClaim,
  fabricatedPurchaseHistory,
  droppedDietaryConstraint,
  staleDiscountQuoted,
] as const;

export function runDeterministicEvaluators(ctx: EvalContext): Verdict[] {
  return DETERMINISTIC_EVALUATORS.map((fn) => fn(ctx));
}
