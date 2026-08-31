/**
 * Tools the shopping assistant can call.
 *
 * Two design rules here are the whole reason this file is worth reading, and
 * both come from bugs found by evaluating real conversations:
 *
 * 1. CONSTRAIN VOCABULARIES WITH `enum`, NOT WITH EXAMPLES. If a filter accepts
 *    free-form strings and the description only lists examples, the model will
 *    invent tokens out of the shopper's own words — "no gluten", "dairy-free" —
 *    and an exact match against a fixed vocabulary silently returns nothing.
 * 2. NEVER LET AN UNKNOWN FILTER ZERO THE RESULTS. An unrecognised term is
 *    reported back in `unsupportedFilters` and dropped from the query, so the
 *    assistant can say "I can't filter on that" instead of reporting an empty
 *    result as "we don't carry it". Filtering on a term nothing can match
 *    guarantees zero rows, and an empty result reads to the model — and then to
 *    the shopper — as scarcity rather than as a broken filter.
 */
import { tool } from "ai";
import { z } from "zod";

import {
  DIETARY_VOCABULARY,
  OFFERS,
  ORDER_HISTORY,
  PRODUCTS,
  formatMoney,
  frequentlyBoughtSkus,
  getProduct,
  type Offer,
  type Product,
} from "./catalog.js";

// ---------------------------------------------------------------- cart state ---
export type CartLine = { sku: string; quantity: number };
export type SessionState = {
  cart: CartLine[];
  clippedOffers: string[];
};

const sessions = new Map<string, SessionState>();

export function getSessionState(sessionId: string): SessionState {
  let state = sessions.get(sessionId);
  if (!state) {
    state = { cart: [], clippedOffers: [] };
    sessions.set(sessionId, state);
  }
  return state;
}

export function resetSessionState(sessionId: string): void {
  sessions.delete(sessionId);
}

export function cartSubtotalCents(state: SessionState): number {
  return state.cart.reduce((sum, line) => {
    const product = getProduct(line.sku);
    return sum + (product ? product.priceCents * line.quantity : 0);
  }, 0);
}

// ------------------------------------------------------------------ helpers ---
function summarize(p: Product) {
  return {
    sku: p.sku,
    name: p.name,
    brand: p.brand,
    price: formatMoney(p.priceCents),
    priceCents: p.priceCents,
    unit: p.unit,
    aisle: p.aisle,
    inStock: p.inStock,
    dietaryTags: p.dietaryTags,
  };
}

/** Split requested dietary filters into recognised tokens and unrecognised ones. */
export function normalizeDietary(
  filters: string[] | undefined,
): { known: string[]; unknown: string[] } {
  const synonyms: Record<string, string> = {
    "gluten free": "gluten_free",
    glutenfree: "gluten_free",
    "no gluten": "gluten_free",
    coeliac: "gluten_free",
    celiac: "gluten_free",
    "dairy free": "dairy_free",
    "no dairy": "dairy_free",
    "lactose free": "dairy_free",
    plantbased: "vegan",
    "plant based": "vegan",
    "nut free": "nut_free",
    "no nuts": "nut_free",
    "low salt": "low_sodium",
    "low sodium": "low_sodium",
  };
  const known: string[] = [];
  const unknown: string[] = [];
  for (const raw of filters ?? []) {
    const flat = String(raw).trim().toLowerCase();
    const token = synonyms[flat] ?? flat.replace(/[-\s]+/g, "_");
    if ((DIETARY_VOCABULARY as readonly string[]).includes(token)) {
      if (!known.includes(token)) known.push(token);
    } else if (!unknown.includes(String(raw))) {
      unknown.push(String(raw));
    }
  }
  return { known, unknown };
}

function applicableOffers(state: SessionState): Offer[] {
  const subtotal = cartSubtotalCents(state);
  const cartSkus = state.cart.map((l) => l.sku);
  return OFFERS.filter((offer) => {
    if (offer.requiresClip && !state.clippedOffers.includes(offer.offerId)) return false;
    if (offer.minBasketCents !== undefined && subtotal < offer.minBasketCents) return false;
    if (offer.skus && !offer.skus.some((s) => cartSkus.includes(s))) return false;
    if (offer.categories) {
      const cats = cartSkus.map((s) => getProduct(s)?.category);
      if (!offer.categories.some((c) => cats.includes(c))) return false;
    }
    return true;
  });
}

// -------------------------------------------------------------------- tools ---
export function buildTools(sessionId: string) {
  return {
    search_products: tool({
      description:
        "Search the Northwind Grocers catalog. Returns matching products with SKU, " +
        "price, aisle and stock status. Use this before recommending anything — never " +
        "name a product or price you have not seen in a result here.",
      inputSchema: z.object({
        query: z.string().describe("Free-text product search, e.g. 'pasta' or 'oat milk'."),
        category: z.string().optional().describe("Narrow to one category, e.g. 'produce'."),
        dietary: z
          .array(z.enum(DIETARY_VOCABULARY))
          .optional()
          .describe(
            "Dietary filters. ONLY these exact values are supported: " +
              DIETARY_VOCABULARY.join(", ") +
              ". Map the shopper's wording onto them ('no gluten' is gluten_free). " +
              "If they ask for something not in this list, omit it and say it cannot be filtered on.",
          ),
        maxPriceCents: z.number().int().positive().optional(),
        inStockOnly: z.boolean().optional().default(true),
      }),
      execute: async ({ query, category, dietary, maxPriceCents, inStockOnly }) => {
        const { known, unknown } = normalizeDietary(dietary);
        const q = query.trim().toLowerCase();
        const matches = PRODUCTS.filter((p) => {
          const haystack = `${p.name} ${p.brand} ${p.category}`.toLowerCase();
          if (q && !q.split(/\s+/).some((word) => haystack.includes(word))) return false;
          if (category && p.category !== category) return false;
          if (maxPriceCents !== undefined && p.priceCents > maxPriceCents) return false;
          if (inStockOnly && !p.inStock) return false;
          return known.every((tag) => p.dietaryTags.includes(tag));
        }).sort((a, b) => a.priceCents - b.priceCents);

        const result: Record<string, unknown> = {
          count: matches.length,
          products: matches.map(summarize),
        };
        if (known.length) result["filteredOnDietary"] = known;
        if (unknown.length) {
          result["unsupportedFilters"] = unknown;
          result["note"] =
            `These filters are not tracked and were NOT applied: ${unknown.join(", ")}. ` +
            `Tell the shopper you cannot filter on them rather than implying we do not ` +
            `carry anything. Supported: ${DIETARY_VOCABULARY.join(", ")}.`;
        }
        return result;
      },
    }),

    get_order_history: tool({
      description:
        "The shopper's real past orders and what they buy most often. This is the ONLY " +
        "source of truth for 'what do I usually buy', 'buy again', or building a list " +
        "from past purchases. Call it before making any claim about their history.",
      inputSchema: z.object({
        mode: z
          .enum(["recent_orders", "frequently_bought"])
          .default("frequently_bought")
          .describe("recent_orders lists orders; frequently_bought ranks repeat purchases."),
        limit: z.number().int().min(1).max(20).optional().default(8),
      }),
      execute: async ({ mode, limit }) => {
        if (mode === "recent_orders") {
          return {
            orders: ORDER_HISTORY.map((o) => ({
              orderId: o.orderId,
              placedOn: o.placedOn,
              status: o.status,
              items: o.skus.map((s) => {
                const p = getProduct(s);
                return p ? { sku: p.sku, name: p.name, price: formatMoney(p.priceCents) } : { sku: s };
              }),
            })),
          };
        }
        return {
          frequentlyBought: frequentlyBoughtSkus()
            .slice(0, limit)
            .map(({ sku, timesBought }) => {
              const p = getProduct(sku);
              return {
                sku,
                name: p?.name ?? sku,
                timesBought,
                price: p ? formatMoney(p.priceCents) : undefined,
                inStock: p?.inStock,
              };
            }),
        };
      },
    }),

    manage_cart: tool({
      description:
        "View the cart, or add and remove items by SKU. Always confirm what changed. " +
        "Adding an item can invalidate an offer that needed a minimum basket, so check " +
        "list_offers again after changing the cart.",
      inputSchema: z.object({
        action: z.enum(["view", "add", "remove"]),
        sku: z.string().optional().describe("Required for add and remove."),
        quantity: z.number().int().min(1).max(20).optional().default(1),
      }),
      execute: async ({ action, sku, quantity }) => {
        const state = getSessionState(sessionId);
        if (action !== "view") {
          if (!sku) return { error: "sku is required for add and remove." };
          const product = getProduct(sku);
          if (!product) {
            return { error: `No product with SKU '${sku}'. Search first and use a SKU from the results.` };
          }
          if (action === "add") {
            if (!product.inStock) {
              const sub = PRODUCTS.find((p) => p.substituteFor === product.sku && p.inStock);
              return {
                error: `${product.name} (${product.sku}) is out of stock and was NOT added.`,
                suggestedSubstitute: sub ? summarize(sub) : undefined,
              };
            }
            const line = state.cart.find((l) => l.sku === product.sku);
            if (line) line.quantity += quantity;
            else state.cart.push({ sku: product.sku, quantity });
          } else {
            state.cart = state.cart.filter((l) => l.sku !== product.sku);
          }
        }
        const subtotal = cartSubtotalCents(state);
        return {
          cart: state.cart.map((l) => {
            const p = getProduct(l.sku)!;
            return {
              sku: l.sku,
              name: p.name,
              quantity: l.quantity,
              lineTotal: formatMoney(p.priceCents * l.quantity),
              dietaryTags: p.dietaryTags,
            };
          }),
          subtotal: formatMoney(subtotal),
          subtotalCents: subtotal,
        };
      },
    }),

    list_offers: tool({
      description:
        "Rewards offers. Shows which are available, which the shopper has clipped, and " +
        "which currently apply to the cart. An offer that applied earlier can stop " +
        "applying after the basket changes — say so rather than repeating a stale total.",
      inputSchema: z.object({}),
      execute: async () => {
        const state = getSessionState(sessionId);
        const applying = applicableOffers(state).map((o) => o.offerId);
        const subtotal = cartSubtotalCents(state);
        return {
          cartSubtotal: formatMoney(subtotal),
          offers: OFFERS.map((o) => ({
            offerId: o.offerId,
            description: o.description,
            discount: formatMoney(o.discountCents),
            requiresClip: o.requiresClip,
            clipped: state.clippedOffers.includes(o.offerId),
            minBasket: o.minBasketCents ? formatMoney(o.minBasketCents) : undefined,
            appliesToCartNow: applying.includes(o.offerId),
          })),
          totalDiscountNow: formatMoney(
            applicableOffers(state).reduce((s, o) => s + o.discountCents, 0),
          ),
        };
      },
    }),

    clip_offer: tool({
      description: "Clip (activate) a Rewards offer by offerId so it can apply to the cart.",
      inputSchema: z.object({ offerId: z.string() }),
      execute: async ({ offerId }) => {
        const state = getSessionState(sessionId);
        const offer = OFFERS.find((o) => o.offerId === offerId.trim().toUpperCase());
        if (!offer) return { error: `No offer with id '${offerId}'.` };
        if (!state.clippedOffers.includes(offer.offerId)) state.clippedOffers.push(offer.offerId);
        const applying = applicableOffers(state).some((o) => o.offerId === offer.offerId);
        return {
          clipped: offer.offerId,
          description: offer.description,
          appliesToCartNow: applying,
          why: applying
            ? "Applies to the current cart."
            : "Clipped, but it does not apply to the cart as it stands — check the minimum basket or that a qualifying item is in the cart.",
        };
      },
    }),

    get_order_status: tool({
      description: "Status of a specific order by orderId, or the most recent order.",
      inputSchema: z.object({ orderId: z.string().optional() }),
      execute: async ({ orderId }) => {
        const order = orderId
          ? ORDER_HISTORY.find((o) => o.orderId === orderId.trim().toUpperCase())
          : [...ORDER_HISTORY].sort((a, b) => b.placedOn.localeCompare(a.placedOn))[0];
        if (!order) return { error: `No order with id '${orderId}'.` };
        return {
          orderId: order.orderId,
          placedOn: order.placedOn,
          status: order.status,
          itemCount: order.skus.length,
        };
      },
    }),
  };
}

export type GroceryTools = ReturnType<typeof buildTools>;
