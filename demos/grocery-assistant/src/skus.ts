/**
 * SKU extraction, kept free of any import that touches credentials.
 *
 * The deterministic evaluators need this, and they must stay testable without a
 * `.env`: src/assistant.ts imports src/env.ts, which refuses to load without one.
 * src/assistant.ts re-exports `extractSkus`, so existing imports keep working.
 */
const SKU_RE = /\b((?:PRD|DRY|PAN|MET|HSE)-\d{4})\b/g;

export function extractSkus(text: string): string[] {
  return [...new Set(text.match(SKU_RE) ?? [])];
}
