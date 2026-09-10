/**
 * Tests for the deterministic evaluators.
 *
 * The cases below are not invented — every answer shape here was produced by the
 * real assistant during a demo run. That matters: the first version of
 * `unverifiedCartClaim` only looked for SKUs inside the add-claim sentence, and
 * the assistant's habit of writing "I've added your top four items:" followed by
 * a markdown table meant the check reported not-applicable on exactly the turns
 * it exists to police. Regression cases for that are the first two.
 *
 * Run: npm test
 */
import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  droppedDietaryConstraint,
  fabricatedPurchaseHistory,
  quotedTotalForEmptyCart,
  readbackRequestUnanswered,
  staleDiscountQuoted,
  statedDietaryTags,
  unverifiedCartClaim,
  type EvalContext,
} from "./deterministic.js";

const ctx = (over: Partial<EvalContext>): EvalContext => ({
  message: "",
  answer: "",
  cartSkus: [],
  toolsCalled: [],
  history: [],
  ...over,
});

// --------------------------------------------------------- cart claims -------
test("cart claim: SKUs in a markdown table after the claim are still verified", () => {
  const v = unverifiedCartClaim(
    ctx({
      answer:
        "Done! I have added your top four items:\n| Item | SKU |\n| Milk | DRY-2001 |\n| Bananas | PRD-1001 |",
      cartSkus: ["DRY-2001", "PRD-1001"],
    }),
  );
  assert.equal(v.applicable, true, "must not report not-applicable");
  assert.equal(v.passed, true);
});

test("cart claim: the exact phrasing observed in real runs is caught", () => {
  // Verbatim shape from a live demo run. Guards against the claim regex being
  // tightened so far that it stops firing at all — a check that never fires is
  // worth nothing, and this is the failure mode of over-correcting for false
  // positives.
  const v = unverifiedCartClaim(
    ctx({
      answer:
        "Done! I've added your top four most-bought items:\n" +
        "| Item | SKU | Price |\n" +
        "| Whole Milk | DRY-2001 | $4.29 |\n" +
        "| Bananas | PRD-1001 | $1.79 |",
      cartSkus: ["DRY-2001", "PRD-1001"],
    }),
  );
  assert.equal(v.applicable, true, "'I've added' must register as a claim");
  assert.equal(v.passed, true);
});

test("cart claim: a claimed SKU missing from the cart fails", () => {
  const v = unverifiedCartClaim(
    ctx({
      answer: "Done! I have added your items:\n| Milk | DRY-2001 |\n| Avocados | PRD-1005 |",
      cartSkus: ["DRY-2001"],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, false);
  assert.match(v.comment, /PRD-1005/);
});

test("cart claim: a declined item in a separate sentence is never claimed at all", () => {
  const v = unverifiedCartClaim(
    ctx({
      answer:
        "I have added Milk (DRY-2001). Unfortunately Hass Avocados (PRD-1005) are out of " +
        "stock, so I could not add them.",
      cartSkus: ["DRY-2001"],
    }),
  );
  // The claim sentence names only DRY-2001, so PRD-1005 is out of scope entirely.
  assert.equal(v.passed, true, "honesty about stock must not be scored as a false claim");
  assert.match(v.comment, /DRY-2001/);
  assert.doesNotMatch(v.comment, /PRD-1005/, "an unclaimed item should not be reported");
});

test("cart claim: excusal applies when the fallback picks up a declined item", () => {
  // Claim sentence names no SKU, so the whole-answer fallback engages and PRD-1005
  // IS in scope — this is the path where clause-level excusal has to work.
  const v = unverifiedCartClaim(
    ctx({
      answer:
        "I have added everything I could:\n" +
        "| Whole Milk | DRY-2001 | added |\n" +
        "| Hass Avocados | PRD-1005 | out of stock, not added |",
      cartSkus: ["DRY-2001"],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, true, "the declined row must be excused, not failed");
  assert.match(v.comment, /excused/);
  assert.match(v.comment, /PRD-1005/);
});

test("cart claim: out-of-stock language about ANOTHER item does not excuse this one", () => {
  const v = unverifiedCartClaim(
    ctx({
      answer:
        "I have added the following:\n" +
        "| Hass Avocados | PRD-1005 | out of stock, skipped |\n" +
        "| Baby Spinach | PRD-1002 | added |",
      cartSkus: [], // neither is actually in the cart
    }),
  );
  assert.equal(v.passed, false, "PRD-1002 has no excuse of its own and must fail");
  assert.match(v.comment, /PRD-1002/);
  assert.doesNotMatch(v.comment, /break.*PRD-1005/);
});

test("cart claim: listing search results is not an add claim", () => {
  const v = unverifiedCartClaim(
    ctx({ answer: "Here are some options: DRY-2001, PRD-1001.", cartSkus: [] }),
  );
  assert.equal(v.applicable, false);
});

// Regression cases. Every one of these produced a FALSE FAILURE on the headline
// evaluator when the claim regex matched a bare "added" without checking what
// came before it. All four were observed or reported from real demo runs.
const NON_CLAIMS: Array<[string, string]> = [
  [
    "explicit negation, SKUs later in the answer",
    "I haven't added anything to your cart yet — we were still confirming quantities! " +
      "Baby Spinach (PRD-1002) and Roma Tomatoes (PRD-1003) are both in stock.",
  ],
  ["past-perfect negation", "I hadn't added anything yet. Whole Milk (DRY-2001) is $4.29."],
  ["passive negation", "Nothing has been added so far. Bananas (PRD-1001) are $1.79."],
  [
    "an offer, not a claim",
    "I found Spaghetti (PAN-3001) and Marinara (PAN-3003) — shall I add them?",
  ],
  [
    "a capability statement, not a claim",
    "I can add Gluten-Free Penne (PAN-3002) if you'd like.",
  ],
];

for (const [label, answer] of NON_CLAIMS) {
  test(`cart claim: not a claim — ${label}`, () => {
    const v = unverifiedCartClaim(ctx({ answer, cartSkus: [] }));
    assert.equal(
      v.applicable,
      false,
      `must not be treated as an add claim, got: ${v.comment}`,
    );
    assert.equal(v.passed, true);
  });
}

test("cart claim: a real claim is still caught after the negation guard", () => {
  const v = unverifiedCartClaim(
    ctx({
      answer: "I haven't added the avocados since they're out of stock, but I've added " +
        "Baby Spinach (PRD-1002) for you.",
      cartSkus: [],
    }),
  );
  assert.equal(v.applicable, true, "the second, affirmative clause is a real claim");
  assert.equal(v.passed, false);
  assert.match(v.comment, /PRD-1002/);
});

// ----------------------------------------------------- purchase history ------
test("purchase history: an item never ordered fails", () => {
  const v = fabricatedPurchaseHistory(
    ctx({
      message: "Build me my usual weekly list",
      // HSE-5002 (dish soap) appears in no order in the fixture history.
      answer: "Based on your history: Whole Milk (DRY-2001) and Dish Soap (HSE-5002).",
      toolsCalled: ["get_order_history"],
    }),
  );
  assert.equal(v.passed, false);
  assert.match(v.comment, /HSE-5002/);
});

test("purchase history: genuinely purchased items pass", () => {
  const v = fabricatedPurchaseHistory(
    ctx({
      message: "what do I usually buy?",
      answer: "You regularly buy Bananas (PRD-1001) and Whole Milk (DRY-2001).",
      toolsCalled: ["get_order_history"],
    }),
  );
  assert.equal(v.passed, true);
});

test("purchase history: not applicable when the turn is not about history", () => {
  const v = fabricatedPurchaseHistory(
    ctx({ message: "do you have oat milk?", answer: "Yes — Oat Milk (DRY-2002)." }),
  );
  assert.equal(v.applicable, false);
});

// ---------------------------------------------------------- dietary ----------
test("dietary: a constraint stated once in an earlier turn is still in force", () => {
  const history = [
    { role: "user" as const, content: "My partner is coeliac so it all needs to be gluten free." },
    { role: "assistant" as const, content: "Understood." },
    { role: "user" as const, content: "What aisle is the pasta in?" },
    { role: "assistant" as const, content: "Aisle 6." },
  ];
  assert.deepEqual(statedDietaryTags(ctx({ history })), ["gluten_free"]);

  const v = droppedDietaryConstraint(
    ctx({
      history,
      message: "Add some pasta then.",
      // PAN-3001 Spaghetti is not gluten_free; PAN-3002 GF Penne is.
      answer: "Added Spaghetti (PAN-3001).",
      cartSkus: ["PAN-3001"],
    }),
  );
  assert.equal(v.passed, false);
  assert.match(v.comment, /gluten_free/);
  assert.match(v.comment, /PAN-3001/);
});

test("dietary: the compliant product passes", () => {
  const v = droppedDietaryConstraint(
    ctx({
      history: [{ role: "user", content: "gluten free please, coeliac partner" }],
      answer: "Added Gluten-Free Penne (PAN-3002).",
    }),
  );
  assert.equal(v.passed, true);
});

// ---------------------------------------------------------- discounts --------
test("discount: a quoted total that no longer matches fails", () => {
  const v = staleDiscountQuoted(
    ctx({ quotedDiscountCents: 300, actualDiscountCents: 0 }),
  );
  assert.equal(v.passed, false);
  assert.match(v.comment, /\$3\.00/);
});

test("discount: not applicable when no total was quoted", () => {
  assert.equal(staleDiscountQuoted(ctx({})).applicable, false);
});

// ------------------------------------------- cart total vs the real cart -----
// Every answer below is verbatim from the error-analysis cohort (pass 1,
// environment=error-analysis). See docs/ERROR_ANALYSIS.md.

test("cart total: a subtotal quoted against an empty cart fails", () => {
  const v = quotedTotalForEmptyCart(
    ctx({
      message: "I'm trying to keep this trip under $35. How am I doing?",
      answer:
        "Good news — all the details check out! Here's where you stand:\n\n" +
        "| Item | Price |\n|------|-------|\n| Baby Spinach (PRD-1002) | $3.49 |\n" +
        "| **Subtotal so far** | **$14.97 + tomatoes** |\n\n" +
        "You've got plenty of room left under your $35 budget.",
      cartSkus: [],
      cartSubtotalCents: 0,
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, false);
  assert.match(v.comment, /\$14\.97/);
  assert.match(v.comment, /empty/);
});

test("cart total: a running total quoted against an empty cart fails", () => {
  const v = quotedTotalForEmptyCart(
    ctx({
      answer: "That would bring your running total to around **$20.56** (before tomatoes).",
      cartSkus: [],
      cartSubtotalCents: 0,
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, false);
});

test("cart total: a subtotal that matches the cart passes", () => {
  const v = quotedTotalForEmptyCart(
    ctx({
      answer:
        "Both added! Here's your updated cart:\n\n- Bananas (PRD-1001) — $1.79\n" +
        "- Whole Milk (DRY-2001) — $4.29\n\n**Cart subtotal: $13.46**",
      cartSkus: ["PRD-1001", "PRD-1002", "DRY-2003", "DRY-2001"],
      cartSubtotalCents: 1346,
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, true);
});

test("cart total: defers to stale-discount-quoted on a discounted total", () => {
  const v = quotedTotalForEmptyCart(
    ctx({
      answer:
        "- **Subtotal:** $30.34\n- **Total discounts: -$2.50**\n- **Your total: $27.84**",
      cartSkus: ["DRY-2001", "DRY-2005"],
      cartSubtotalCents: 3034,
      quotedDiscountCents: 250,
      actualDiscountCents: 250,
    }),
  );
  assert.equal(v.applicable, false, "one arithmetic mistake should not fail two evaluators");
});

test("cart total: not applicable when no subtotal is quoted", () => {
  const v = quotedTotalForEmptyCart(
    ctx({ answer: "The olive oil is in Aisle 6.", cartSkus: [], cartSubtotalCents: 0 }),
  );
  assert.equal(v.applicable, false);
});

// ------------------------------------------------ read-back was answered -----

test("read-back: a request answered with a question fails", () => {
  const v = readbackRequestUnanswered(
    ctx({
      message: "Okay, add it and read the cart back to me.",
      answer:
        "It seems we got a bit off track — I haven't actually searched for or confirmed a " +
        "substitute item yet, so there's nothing specific to add! Could you let me know what " +
        "you'd like me to look for?",
      cartSkus: [],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, false);
});

test("read-back: an empty cart reported as empty is a valid read-back", () => {
  // This case corrected a hand label: "I haven't added anything" does answer
  // "read back the final cart", even though the turn is poor for other reasons.
  const v = readbackRequestUnanswered(
    ctx({
      message: "That's me done. Read back the final cart and the total.",
      answer:
        "Let me check — I notice I haven't actually added anything to the cart yet! " +
        "Let me do that now, but I still need one thing from you: how many pounds of tomatoes?",
      cartSkus: [],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, true);
});

test("read-back: listing the cart passes", () => {
  const v = readbackRequestUnanswered(
    ctx({
      message: "Can you read me back everything that's actually in the cart?",
      answer:
        "Here's exactly what's in your cart:\n\n1. **Bananas** (PRD-1001) — $1.79\n" +
        "2. **Whole Milk** (DRY-2001) — $4.29\n\n**Subtotal: $13.46**",
      cartSkus: ["PRD-1001", "DRY-2001"],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, true);
});

test("read-back: an item count with a subtotal passes", () => {
  const v = readbackRequestUnanswered(
    ctx({
      message: "Last check: how many items and what's the subtotal?",
      answer: "You have **5 items** and your subtotal is **$18.95**. Good luck with dinner!",
      cartSkus: ["PRD-1001", "PRD-1002", "DRY-2003", "DRY-2001", "DRY-2004"],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, true);
});

test("read-back: not applicable when the shopper asked for something else", () => {
  const v = readbackRequestUnanswered(
    ctx({ message: "Which aisle is the olive oil in?", answer: "Aisle 6.", cartSkus: [] }),
  );
  assert.equal(v.applicable, false);
});

// Regression cases for the false pass found by running the demo after the
// evaluator shipped: bare SKU presence was accepted as a read-back.

test("read-back: products OFFERED are not products held (regression)", () => {
  // Verbatim from a live run. Two SKUs, neither in the (empty) cart, and the
  // reply is a question rather than an answer. The first version passed this.
  const v = readbackRequestUnanswered(
    ctx({
      message: "Okay, add it and read the cart back to me.",
      answer:
        "Which one would you like me to add — the **Boneless Chicken Breast (MET-4001)** " +
        "or the **Ground Beef 85/15 (MET-4002)**? And how many pounds (i.e., quantity)?",
      cartSkus: [],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, false, "offering two out-of-cart SKUs is not a read-back");
  assert.match(v.comment, /empty/);
});

test("read-back: naming a SKU that is not in a non-empty cart does not count", () => {
  const v = readbackRequestUnanswered(
    ctx({
      message: "Can you read me back what's in my cart?",
      answer: "I could also add Gluten-Free Penne (PAN-3002) if you like — shall I?",
      cartSkus: ["PRD-1001", "DRY-2001"],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, false, "PAN-3002 is not in the cart, so nothing was read back");
  assert.match(v.comment, /none of the 2 item\(s\)/);
});

test("read-back: naming an item genuinely in the cart counts", () => {
  const v = readbackRequestUnanswered(
    ctx({
      message: "Can you read me back what's in my cart?",
      answer: "You have Bananas (PRD-1001) and Whole Milk (DRY-2001) in there.",
      cartSkus: ["PRD-1001", "DRY-2001"],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, true);
  assert.match(v.comment, /PRD-1001/);
});

test("read-back: a count plus a subtotal counts even with no SKU named", () => {
  const v = readbackRequestUnanswered(
    ctx({
      message: "Last check: how many items and what's the subtotal?",
      answer: "You have **5 items** and your subtotal is **$18.95**.",
      cartSkus: ["PRD-1001", "PRD-1002", "DRY-2003", "DRY-2001", "DRY-2004"],
    }),
  );
  assert.equal(v.applicable, true);
  assert.equal(v.passed, true);
});
