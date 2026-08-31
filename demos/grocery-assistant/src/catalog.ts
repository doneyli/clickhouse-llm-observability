/**
 * Synthetic catalog for Northwind Grocers — a fictional regional grocery chain.
 *
 * Everything here is invented. It is shaped to support the journeys a real
 * grocery shopping assistant covers (search and discovery, cart building,
 * buy-again from order history, offers and rewards, order status) and, more
 * importantly, to make specific FAILURES reproducible:
 *
 *   - `orderHistory` exists so "build a list from what I usually buy" can be
 *     checked against what the shopper actually bought. An assistant with no
 *     order-history tool will cheerfully invent items instead — a real failure
 *     mode, and the reason grounding is the first thing worth measuring.
 *   - `dietaryTags` exist so a constraint stated once ("gluten-free, my partner
 *     is coeliac") can be violated four turns later, and caught.
 *   - `inStock` + `substituteFor` exist so an out-of-stock item forces a choice
 *     between an honest substitution and a hallucinated availability claim.
 *   - Offers carry `requiresClip` and `minBasketCents` so a coupon can stop
 *     applying after the basket changes — correct at the moment it was clipped,
 *     wrong by the end of the conversation.
 *
 * Prices are integer cents. Money in floats is how you end up with a
 * $4.99000000001 total in a customer demo.
 */

export type Product = {
  sku: string;
  name: string;
  brand: string;
  category: string;
  priceCents: number;
  unit: string;
  aisle: string;
  inStock: boolean;
  dietaryTags: string[];
  /** SKU this item is a reasonable substitute for, when that one is out of stock. */
  substituteFor?: string;
};

/** The only dietary vocabulary the catalog understands. See tools.ts. */
export const DIETARY_VOCABULARY = [
  "gluten_free",
  "dairy_free",
  "vegan",
  "vegetarian",
  "nut_free",
  "low_sodium",
  "organic",
] as const;

export const PRODUCTS: Product[] = [
  // --- produce ---
  { sku: "PRD-1001", name: "Bananas", brand: "Northwind", category: "produce", priceCents: 179, unit: "bunch", aisle: "Produce", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "nut_free"] },
  { sku: "PRD-1002", name: "Baby Spinach", brand: "Northwind Organic", category: "produce", priceCents: 349, unit: "10 oz bag", aisle: "Produce", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "organic", "nut_free"] },
  { sku: "PRD-1003", name: "Roma Tomatoes", brand: "Northwind", category: "produce", priceCents: 249, unit: "lb", aisle: "Produce", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "nut_free"] },
  { sku: "PRD-1004", name: "Yellow Onions", brand: "Northwind", category: "produce", priceCents: 199, unit: "3 lb bag", aisle: "Produce", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "nut_free"] },
  { sku: "PRD-1005", name: "Hass Avocados", brand: "Northwind", category: "produce", priceCents: 599, unit: "4 ct", aisle: "Produce", inStock: false, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "nut_free"] },

  // --- dairy & eggs ---
  { sku: "DRY-2001", name: "Whole Milk", brand: "Valley Creamery", category: "dairy", priceCents: 429, unit: "gallon", aisle: "Dairy", inStock: true, dietaryTags: ["vegetarian", "gluten_free", "nut_free"] },
  { sku: "DRY-2002", name: "Oat Milk, Unsweetened", brand: "Harvest Lane", category: "dairy_alternative", priceCents: 469, unit: "64 oz", aisle: "Dairy", inStock: true, dietaryTags: ["vegan", "vegetarian", "dairy_free", "gluten_free"], substituteFor: "DRY-2001" },
  { sku: "DRY-2003", name: "Large Eggs, Grade A", brand: "Northwind", category: "dairy", priceCents: 389, unit: "dozen", aisle: "Dairy", inStock: true, dietaryTags: ["vegetarian", "gluten_free", "dairy_free", "nut_free"] },
  { sku: "DRY-2004", name: "Sharp Cheddar Block", brand: "Valley Creamery", category: "dairy", priceCents: 549, unit: "8 oz", aisle: "Dairy", inStock: true, dietaryTags: ["vegetarian", "gluten_free", "nut_free"] },
  { sku: "DRY-2005", name: "Greek Yogurt, Plain", brand: "Valley Creamery", category: "dairy", priceCents: 619, unit: "32 oz", aisle: "Dairy", inStock: true, dietaryTags: ["vegetarian", "gluten_free", "nut_free"] },

  // --- pantry ---
  { sku: "PAN-3001", name: "Spaghetti", brand: "Northwind", category: "pasta", priceCents: 149, unit: "16 oz", aisle: "Aisle 6", inStock: true, dietaryTags: ["vegan", "vegetarian", "dairy_free", "nut_free"] },
  { sku: "PAN-3002", name: "Gluten-Free Penne", brand: "Harvest Lane", category: "pasta", priceCents: 329, unit: "12 oz", aisle: "Aisle 6", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "nut_free"], substituteFor: "PAN-3001" },
  { sku: "PAN-3003", name: "Marinara Sauce", brand: "Northwind", category: "sauce", priceCents: 279, unit: "24 oz", aisle: "Aisle 6", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "nut_free"] },
  { sku: "PAN-3004", name: "Extra Virgin Olive Oil", brand: "Harvest Lane", category: "oil", priceCents: 899, unit: "500 ml", aisle: "Aisle 6", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "nut_free"] },
  { sku: "PAN-3005", name: "Long Grain White Rice", brand: "Northwind", category: "grain", priceCents: 419, unit: "5 lb", aisle: "Aisle 7", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "nut_free"] },
  { sku: "PAN-3006", name: "Black Beans, No Salt Added", brand: "Northwind", category: "canned", priceCents: 119, unit: "15 oz", aisle: "Aisle 7", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "low_sodium", "nut_free"] },
  { sku: "PAN-3007", name: "Creamy Peanut Butter", brand: "Harvest Lane", category: "spread", priceCents: 429, unit: "16 oz", aisle: "Aisle 8", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free"] },
  { sku: "PAN-3008", name: "Sourdough Loaf", brand: "Northwind Bakery", category: "bakery", priceCents: 449, unit: "each", aisle: "Bakery", inStock: true, dietaryTags: ["vegetarian", "dairy_free", "nut_free"] },
  { sku: "PAN-3009", name: "Gluten-Free Sandwich Bread", brand: "Harvest Lane", category: "bakery", priceCents: 599, unit: "18 oz", aisle: "Bakery", inStock: true, dietaryTags: ["vegetarian", "gluten_free", "dairy_free", "nut_free"], substituteFor: "PAN-3008" },

  // --- meat & seafood ---
  { sku: "MET-4001", name: "Boneless Chicken Breast", brand: "Northwind", category: "meat", priceCents: 749, unit: "lb", aisle: "Meat", inStock: true, dietaryTags: ["gluten_free", "dairy_free", "nut_free"], substituteFor: "MET-4003" },
  { sku: "MET-4002", name: "Ground Beef, 85/15", brand: "Northwind", category: "meat", priceCents: 699, unit: "lb", aisle: "Meat", inStock: true, dietaryTags: ["gluten_free", "dairy_free", "nut_free"] },
  { sku: "MET-4003", name: "Atlantic Salmon Fillet", brand: "Northwind", category: "seafood", priceCents: 1299, unit: "lb", aisle: "Seafood", inStock: false, dietaryTags: ["gluten_free", "dairy_free", "nut_free"] },
  { sku: "MET-4004", name: "Firm Tofu", brand: "Harvest Lane", category: "meat_alternative", priceCents: 279, unit: "14 oz", aisle: "Produce", inStock: true, dietaryTags: ["vegan", "vegetarian", "gluten_free", "dairy_free", "nut_free"], substituteFor: "MET-4001" },

  // --- household ---
  { sku: "HSE-5001", name: "Paper Towels, 6 Rolls", brand: "Northwind", category: "household", priceCents: 899, unit: "6 ct", aisle: "Aisle 12", inStock: true, dietaryTags: [] },
  { sku: "HSE-5002", name: "Dish Soap, Lemon", brand: "Northwind", category: "household", priceCents: 349, unit: "18 oz", aisle: "Aisle 12", inStock: true, dietaryTags: [] },
];

export type Offer = {
  offerId: string;
  description: string;
  /** Applies to these SKUs, or to every SKU in these categories. */
  skus?: string[];
  categories?: string[];
  discountCents: number;
  /** Rewards offers must be clipped before they apply — the classic stale-coupon trap. */
  requiresClip: boolean;
  /** Basket subtotal the offer needs to stay valid. */
  minBasketCents?: number;
};

export const OFFERS: Offer[] = [
  { offerId: "OFR-9001", description: "$1.00 off any pasta", categories: ["pasta"], discountCents: 100, requiresClip: true },
  { offerId: "OFR-9002", description: "$2.00 off Valley Creamery dairy when you spend $25", categories: ["dairy"], discountCents: 200, requiresClip: true, minBasketCents: 2500 },
  { offerId: "OFR-9003", description: "$0.50 off Baby Spinach", skus: ["PRD-1002"], discountCents: 50, requiresClip: true },
  { offerId: "OFR-9004", description: "$3.00 off orders of $40 or more", discountCents: 300, requiresClip: false, minBasketCents: 4000 },
  { offerId: "OFR-9005", description: "$1.50 off Harvest Lane gluten-free items", categories: ["bakery", "pasta"], discountCents: 150, requiresClip: true },
];

export type PastOrder = {
  orderId: string;
  placedOn: string;
  status: "delivered" | "in_transit" | "preparing";
  skus: string[];
};

/**
 * The shopper's real order history. This is the ground truth that makes
 * "what do I usually buy?" answerable — and makes a fabricated answer
 * detectable. Without a tool over this, an assistant asked to build a list from
 * past purchases has nothing to draw on and will invent plausible groceries.
 */
export const ORDER_HISTORY: PastOrder[] = [
  { orderId: "ORD-7001", placedOn: "2026-08-21", status: "delivered", skus: ["PRD-1001", "DRY-2001", "DRY-2003", "PAN-3008", "MET-4001", "PRD-1002"] },
  { orderId: "ORD-7002", placedOn: "2026-08-14", status: "delivered", skus: ["PRD-1001", "DRY-2001", "PAN-3001", "PAN-3003", "MET-4002", "HSE-5001"] },
  { orderId: "ORD-7003", placedOn: "2026-08-07", status: "delivered", skus: ["PRD-1001", "DRY-2003", "DRY-2005", "PAN-3005", "PAN-3006", "PRD-1003"] },
  { orderId: "ORD-7004", placedOn: "2026-08-27", status: "in_transit", skus: ["DRY-2001", "PAN-3008", "PRD-1002"] },
];

export function getProduct(sku: string): Product | undefined {
  return PRODUCTS.find((p) => p.sku === sku.trim().toUpperCase());
}

export function formatMoney(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/** SKUs the shopper has actually bought, most-frequent first. */
export function frequentlyBoughtSkus(): Array<{ sku: string; timesBought: number }> {
  const counts = new Map<string, number>();
  for (const order of ORDER_HISTORY) {
    for (const sku of order.skus) counts.set(sku, (counts.get(sku) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([sku, timesBought]) => ({ sku, timesBought }))
    .sort((a, b) => b.timesBought - a.timesBought || a.sku.localeCompare(b.sku));
}
