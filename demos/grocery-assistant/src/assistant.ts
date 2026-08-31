/**
 * The Northwind Grocers shopping assistant, instrumented TWO ways on purpose.
 *
 * `mode: "good"` is how you want it. `mode: "broken"` reproduces, faithfully, the
 * five defects a real grocery-retail harness shipped to its first internal pilot.
 * Both modes call the same model with the same tools — the ONLY difference is
 * instrumentation, which is the point: the app worked fine in both cases, and
 * only one of them was measurable.
 *
 * The five defects, and why each one hurts:
 *
 *   1. GENERATIONS WITH NULL INPUT/OUTPUT. Reproduced with the AI SDK's real
 *      `recordInputs`/`recordOutputs: false` switches — usually turned off early
 *      for PII reasons and never turned back on. Consequence: there is nothing
 *      for any evaluator to read, so no judge can ever run. This is the one that
 *      blocks everything else.
 *   2. HIGH-CARDINALITY TRACE NAME. Naming the trace after the shopper's message
 *      means no two traces group, so you cannot filter, aggregate, or target a
 *      rule at "the chat endpoint". The question belongs in the INPUT.
 *   3. NO INPUT/OUTPUT ON THE ROOT OBSERVATION. Trace input/output mirrors the
 *      root observation, so the Traces table shows blank rows and a
 *      root-targeted evaluator sees nothing.
 *   4. CONVERSATION HISTORY DUPLICATED ONTO EVERY TURN'S ROOT. Note this one is
 *      NOT a rule Langfuse states anywhere — it is an observed consequence of a
 *      rule it does state. Langfuse's guidance is "one trace per turn and one
 *      session per conversation", because "the per-turn model keeps traces small
 *      and easy to navigate in the session view". Restate the transcript on each
 *      root and you lose exactly that: the session view renders each trace as one
 *      turn, so every turn shows the whole conversation again. The model needs the
 *      history; the trace root does not.
 *   5. SESSION ID SET ONLY ON THE ROOT, NOT PROPAGATED. Observation-level
 *      evaluators and filters read attributes off the OBSERVATION. An
 *      un-propagated sessionId matches nothing, and per-generation cost never
 *      rolls up to the session.
 */
import { anthropic } from "@ai-sdk/anthropic";
import { generateText, stepCountIs } from "ai";
import {
  getActiveTraceId,
  propagateAttributes,
  startActiveObservation,
  startObservation,
} from "@langfuse/tracing";

import { AGENT_MODEL, BASE_TAGS } from "./env.js";
import { buildTools, cartSubtotalCents, getSessionState } from "./tools.js";

export type InstrumentationMode = "good" | "broken";

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type TurnResult = {
  traceId: string | undefined;
  answer: string;
  toolsCalled: string[];
  skusMentioned: string[];
  cartSkus: string[];
  cartSubtotalCents: number;
  transcript?: ChatMessage[];
};

/**
 * Stable, low-cardinality trace name. Verb-first, one per logical operation —
 * the shopper's message goes in the trace INPUT, never in the name.
 */
export const TRACE_NAME = "handle-chat-message";
export const SNAPSHOT_NAME = "conversation-snapshot";
export const CONVERSATION_END_TAG = "conversation_end";

const SYSTEM_PROMPT = [
  "You are the shopping assistant for Northwind Grocers, a regional grocery chain.",
  "",
  "You help shoppers find products, build a cart across a whole shopping trip, use",
  "Rewards offers, re-order what they usually buy, and check order status.",
  "",
  "Rules:",
  "- Ground every claim in a tool result. Never state a price, a stock status, or a",
  "  past purchase you have not seen returned by a tool in this conversation.",
  "- For anything about what the shopper usually buys or has bought before, call",
  "  get_order_history first. Do not guess from what seems typical.",
  "- Constraints persist. If the shopper tells you once that something is required",
  "  (a diet, a budget, a brand to avoid), it applies for the rest of the",
  "  conversation, whether or not they repeat it.",
  "- If a filter is not supported, say so plainly. Never present an empty result as",
  "  'we do not carry that'.",
  "- If an item is out of stock, say so and offer a real substitute from the catalog.",
  "- Adding to the cart can invalidate an offer that needed a minimum basket.",
  "  Re-check offers after changing the cart rather than repeating an old total.",
  "- Reference products by name AND SKU so the shopper can be sure which one you mean.",
  "- Be concise. Two short paragraphs or a short list, not an essay.",
].join("\n");

const SKU_RE = /\b((?:PRD|DRY|PAN|MET|HSE)-\d{4})\b/g;

export function extractSkus(text: string): string[] {
  return [...new Set(text.match(SKU_RE) ?? [])];
}

export type RunTurnArgs = {
  message: string;
  sessionId: string;
  userId: string;
  history?: ChatMessage[];
  turnIndex?: number;
  isFinalTurn?: boolean;
  mode?: InstrumentationMode;
  extraTags?: string[];
};

export async function runTurn(args: RunTurnArgs): Promise<TurnResult> {
  const {
    message,
    sessionId,
    userId,
    history = [],
    turnIndex = 0,
    isFinalTurn = false,
    mode = "good",
    extraTags = [],
  } = args;

  return mode === "good"
    ? runTurnInstrumentedWell({ ...args, history, turnIndex, isFinalTurn, extraTags })
    : runTurnInstrumentedBadly({ ...args, history, turnIndex, extraTags });
}

// ============================================================ GOOD ==========
async function runTurnInstrumentedWell(
  args: Required<Pick<RunTurnArgs, "message" | "sessionId" | "userId" | "history" | "turnIndex" | "isFinalTurn" | "extraTags">>,
): Promise<TurnResult> {
  const { message, sessionId, userId, history, turnIndex, isFinalTurn, extraTags } = args;

  return await startActiveObservation(TRACE_NAME, async (root) => {
    // propagateAttributes, not root-only attributes: these land on the root AND
    // every child observation, which is what makes them filterable per
    // observation and what lets per-generation cost roll up to the session.
    return await propagateAttributes(
      {
        traceName: TRACE_NAME,
        sessionId,
        userId,
        tags: [...BASE_TAGS, ...extraTags, ...(isFinalTurn ? [CONVERSATION_END_TAG] : [])],
        metadata: { agentModel: AGENT_MODEL, turn: String(turnIndex + 1) },
      },
      async () => {
        // Trace input/output mirror the ROOT observation. One turn's question in,
        // one turn's answer out — the history is NOT restated here, so the
        // Sessions view renders this trace as exactly one turn.
        root.update({
          input: { message },
          metadata: { turn: turnIndex + 1, priorTurns: history.length / 2 },
        });

        const tools = buildTools(sessionId);
        const result = await generateText({
          model: anthropic(AGENT_MODEL),
          system: SYSTEM_PROMPT,
          // The MODEL gets the full history — that is how a follow-up resolves.
          // This is the distinction people collapse: history belongs in the model
          // call, not restated on the trace root.
          messages: [...history, { role: "user" as const, content: message }],
          tools,
          stopWhen: stepCountIs(6),
          // Defaults record input and output. Named explicitly because the whole
          // lesson of the broken mode is what happens when they are off.
          telemetry: { functionId: "chat-turn", recordInputs: true, recordOutputs: true },
        });

        const answer = result.text?.trim() || "(no answer)";
        const toolsCalled = result.steps
          .flatMap((s) => s.toolCalls ?? [])
          .map((c) => c.toolName);

        root.update({ output: answer });

        const state = getSessionState(sessionId);
        const out: TurnResult = {
          traceId: getActiveTraceId(),
          answer,
          toolsCalled,
          skusMentioned: extractSkus(answer),
          cartSkus: state.cart.map((l) => l.sku),
          cartSubtotalCents: cartSubtotalCents(state),
        };

        // One observation owning the whole conversation, emitted once, on the
        // last turn. This is what a conversation-level judge can match on: an
        // observation-level evaluator sees ONLY the observation it matched, so
        // without this there is nothing in the trace that holds more than a turn.
        if (isFinalTurn) {
          const transcript: ChatMessage[] = [
            ...history,
            { role: "user", content: message },
            { role: "assistant", content: answer },
          ];
          const snapshot = startObservation(SNAPSHOT_NAME, {
            input: { transcript, turns: transcript.length / 2 },
            output: answer,
          });
          snapshot.end();
          out.transcript = transcript;
        }

        return out;
      },
    );
  });
}

// ========================================================== BROKEN ==========
async function runTurnInstrumentedBadly(
  args: Required<Pick<RunTurnArgs, "message" | "sessionId" | "userId" | "history" | "turnIndex" | "extraTags">>,
): Promise<TurnResult> {
  const { message, sessionId, userId, history, turnIndex, extraTags } = args;

  // DEFECT 2: the shopper's message IS the trace name. Every trace is unique, so
  // nothing groups and no rule can target this endpoint.
  const highCardinalityName = `chat: ${message.slice(0, 60)}`;

  return await startActiveObservation(highCardinalityName, async (root) => {
    // DEFECT 5: sessionId/userId are stamped on the ROOT only. Children carry
    // neither, so observation-level filters and evaluators never match them.
    root.update({
      // DEFECT 4: the whole conversation restated on every turn's root, which is
      // what makes the Sessions view unreadable.
      input: { message, conversationHistory: history },
      metadata: { sessionId, userId, turn: turnIndex + 1 },
    });

    const tools = buildTools(sessionId);
    const result = await generateText({
      model: anthropic(AGENT_MODEL),
      system: SYSTEM_PROMPT,
      messages: [...history, { role: "user" as const, content: message }],
      tools,
      stopWhen: stepCountIs(6),
      // DEFECT 1: the generation is traced, but with no input and no output.
      // Turned off "for PII" on day one and never revisited. Every LLM call shows
      // up in the trace tree as an empty box, and no evaluator can read it.
      telemetry: { functionId: "chat-turn", recordInputs: false, recordOutputs: false },
    });

    const answer = result.text?.trim() || "(no answer)";
    const toolsCalled = result.steps.flatMap((s) => s.toolCalls ?? []).map((c) => c.toolName);

    // A span carrying nothing, of the kind that accumulates when instrumentation
    // is added defensively — pure noise in the tree and billable ingest.
    const emptySpan = startObservation("postprocess");
    emptySpan.end();

    // DEFECT 3: the root's output is never set, so the trace's output column is
    // blank and a root-targeted evaluator has nothing to score.
    const state = getSessionState(sessionId);
    return {
      traceId: getActiveTraceId(),
      answer,
      toolsCalled,
      skusMentioned: extractSkus(answer),
      cartSkus: state.cart.map((l) => l.sku),
      cartSubtotalCents: cartSubtotalCents(state),
    };
  });
}
