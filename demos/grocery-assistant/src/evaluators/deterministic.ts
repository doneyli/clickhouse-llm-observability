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
  /** The cart's true subtotal AFTER the turn, in cents. */
  cartSubtotalCents?: number;
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

// ---------------------------------------------------------------------------
// 5. A cart total for a cart that does not hold it.
// ---------------------------------------------------------------------------
/**
 * Discovered by error analysis, not designed in. See docs/ERROR_ANALYSIS.md.
 *
 * Three turns of one conversation answered a budget question with a tidy
 * itemised table and "Subtotal so far $14.97" — having called the cart tool in
 * the same turn and received `{"cart":[],"subtotal":"$0.00"}`. The shopper was
 * told they had room under a $35 budget against a cart holding nothing.
 *
 * This is the same shape as evaluator 1: the claim is in the transcript, the
 * outcome is in the environment, and the environment is free to ask. It is
 * *not* the same check — evaluator 1 polices which SKUs were added, this one
 * polices the money figure.
 *
 * NOTE the score name is snake_case where the four above are kebab-case. The
 * name has to match the Langfuse score config exactly or the value lands outside
 * the vocabulary the dashboard aggregates, and the configs were created from the
 * error-analysis taxonomy, which the guide writes in snake_case. One vocabulary
 * beats one naming style.
 */
const QUOTED_SUBTOTAL_RE =
  /(?:sub-?total|running total|total so far|cart total)\b[^$\n]{0,40}\$\s?([\d,]+\.\d{2})/gi;

/** A discounted total is a different claim, and evaluator 4 already owns it. */
const DISCOUNTED_TOTAL_RE = /\b(?:after (?:the )?discounts?|with (?:the )?discounts?|your total)\b/i;

export function quotedCartSubtotalCents(answer: string): number | undefined {
  const hits = [...answer.matchAll(QUOTED_SUBTOTAL_RE)];
  if (hits.length === 0) return undefined;
  // The last figure is the one the shopper acts on when several are quoted.
  const raw = hits[hits.length - 1]?.[1];
  if (raw === undefined) return undefined;
  return Math.round(Number(raw.replace(/,/g, "")) * 100);
}

export function quotedTotalForEmptyCart(ctx: EvalContext): Verdict {
  const name = "quoted_total_for_empty_cart";
  if (ctx.cartSubtotalCents === undefined) {
    return notApplicable(name, "The cart subtotal for this turn was not supplied.");
  }
  const quoted = quotedCartSubtotalCents(ctx.answer);
  if (quoted === undefined) {
    return notApplicable(name, "The assistant quoted no cart subtotal this turn.");
  }
  // Defer to `stale-discount-quoted` rather than fail the same turn twice for
  // what is really one arithmetic mistake about discounts.
  if (ctx.quotedDiscountCents !== undefined || DISCOUNTED_TOTAL_RE.test(ctx.answer)) {
    return notApplicable(name, "The figure quoted is a discounted total; stale-discount-quoted owns that.");
  }
  const money = (c: number) => `$${(c / 100).toFixed(2)}`;
  if (quoted !== ctx.cartSubtotalCents) {
    return {
      name,
      passed: false,
      applicable: true,
      comment:
        `Quoted a cart subtotal of ${money(quoted)} while the cart actually holds ` +
        `${money(ctx.cartSubtotalCents)}` +
        (ctx.cartSkus.length === 0 ? " and is empty." : ` across ${ctx.cartSkus.length} item(s).`),
    };
  }
  return {
    name,
    passed: true,
    applicable: true,
    comment: `Quoted subtotal ${money(quoted)} matches the cart.`,
  };
}

// ---------------------------------------------------------------------------
// 6. A read-back that was asked for and never given.
// ---------------------------------------------------------------------------
/**
 * Also discovered by error analysis. The shopper says "read the cart back" and
 * gets a question instead — twice across pass 1, both on the final turn of a
 * conversation, which is the worst place to lose the thread.
 *
 * The subtlety that writing this check forced into the open: an empty cart
 * reported *as* empty is a correct read-back. "I haven't actually added anything
 * to the cart yet" answers the question. So the check only fires when the reply
 * neither lists cart contents nor says the cart is empty — and applying it
 * corrected one of the two hand labels that produced it. See
 * docs/ERROR_ANALYSIS.md.
 */
// `read\b…\bback` rather than a fixed list of infixes: shoppers say "read it
// back", "read me back everything", "read back the final cart" and "read the
// cart back to me", and an enumeration of those missed the last one.
const READBACK_REQUEST_RE =
  /\b(?:read\b[^.?!\n]{0,20}\bback\b|what(?:'s| is) (?:actually )?in (?:my|the) cart|how many items|final cart|what am i (?:at|paying))\b/i;

/** Language that reports an empty cart, which is a valid read-back of one. */
const EMPTY_CART_RE =
  /\b(?:cart is (?:currently )?empty|nothing in (?:your|the) cart|haven'?t (?:actually )?added anything|no items in (?:your|the) cart|added nothing)\b/i;

export function readbackRequestUnanswered(ctx: EvalContext): Verdict {
  const name = "readback_request_unanswered";
  if (!READBACK_REQUEST_RE.test(ctx.message)) {
    return notApplicable(name, "The shopper did not ask for the cart to be read back.");
  }

  // An empty cart has exactly one correct read-back: saying it is empty. Nothing
  // else can stand in, because there are no contents to report.
  if (ctx.cartSkus.length === 0) {
    if (EMPTY_CART_RE.test(ctx.answer)) {
      return {
        name,
        passed: true,
        applicable: true,
        comment: "Cart is empty and the reply says so, which answers the question.",
      };
    }
    return {
      name,
      passed: false,
      applicable: true,
      comment:
        "Asked for the cart to be read back while the cart was empty, and the reply " +
        "never says so.",
    };
  }

  // A non-empty cart is read back either by naming something that is genuinely
  // in it, or by reporting a figure for it (a count plus a subtotal is a valid
  // read-back even with no SKU named).
  //
  // Bare SKU presence is deliberately NOT accepted. An earlier version passed on
  // any SKU in the reply, which let "Which one would you like me to add — the
  // Boneless Chicken Breast (MET-4001) or the Ground Beef 85/15 (MET-4002)?"
  // count as a read-back: two SKUs, neither in the cart, and the reply is a
  // question rather than an answer. Products offered are not products held.
  const inCart = new Set(ctx.cartSkus.map((s) => s.trim().toUpperCase()));
  const namedFromCart = extractSkus(ctx.answer).filter((s) => inCart.has(s.trim().toUpperCase()));
  const listsMoney = /\$\s?[\d,]+\.\d{2}/.test(ctx.answer);

  if (namedFromCart.length === 0 && !listsMoney) {
    return {
      name,
      passed: false,
      applicable: true,
      comment:
        `Asked for the cart to be read back and the reply reports none of it — no price, ` +
        `and none of the ${ctx.cartSkus.length} item(s) actually in the cart are named.`,
    };
  }
  return {
    name,
    passed: true,
    applicable: true,
    comment:
      namedFromCart.length > 0
        ? `Read-back given (names ${namedFromCart.join(", ")} from the cart).`
        : "Read-back given (reports a figure for the cart).",
  };
}

/**
 * The full deterministic board, in the order worth building them.
 *
 * The first four were designed with the demo. The last two came out of error
 * analysis on real traffic (docs/ERROR_ANALYSIS.md) — which is the point: the
 * designed four caught nothing in two of the five conversations, while these two
 * cover 5 of the 10 failing turns found by reading traces.
 *
 * All six are reference-free: they compare the answer against system state, not
 * against a saved expected output. That is what makes them safe to run on live
 * production traffic, where there is no ground truth — reference-based evaluators
 * structurally cannot.
 */
export const DETERMINISTIC_EVALUATORS = [
  unverifiedCartClaim,
  fabricatedPurchaseHistory,
  droppedDietaryConstraint,
  staleDiscountQuoted,
  quotedTotalForEmptyCart,
  readbackRequestUnanswered,
] as const;

export function runDeterministicEvaluators(ctx: EvalContext): Verdict[] {
  return DETERMINISTIC_EVALUATORS.map((fn) => fn(ctx));
}
