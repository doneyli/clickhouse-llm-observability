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

test("cart claim: an item explicitly declined as out of stock is excused", () => {
  const v = unverifiedCartClaim(
    ctx({
      answer:
        "I have added Milk (DRY-2001). Unfortunately Hass Avocados (PRD-1005) are out of " +
        "stock, so I could not add them.",
      cartSkus: ["DRY-2001"],
    }),
  );
  assert.equal(v.passed, true, "honesty about stock must not be scored as a false claim");
  assert.match(v.comment, /excused/);
});

test("cart claim: listing search results is not an add claim", () => {
  const v = unverifiedCartClaim(
    ctx({ answer: "Here are some options: DRY-2001, PRD-1001.", cartSkus: [] }),
  );
  assert.equal(v.applicable, false);
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
