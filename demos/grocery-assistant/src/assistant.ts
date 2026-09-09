/**
 * The Northwind Grocers shopping assistant, instrumented THREE ways on purpose.
 *
 * `mode: "good"` is how you want it. `mode: "broken"` reproduces, faithfully, the
 * five defects a real grocery-retail harness shipped to its first internal pilot.
 * Every mode calls the same model with the same tools — the ONLY difference is
 * instrumentation, which is the point: the app worked fine in both cases, and
 * only one of them was measurable.
 *
 * `mode: "collapsed"` is a THIRD mode carrying exactly one defect (#6, the agent
 * loop flattened into a single generation). It is deliberately not folded into
 * `broken`, because defect 6 and defect 1 cannot coexist in one trace: defect 1's
 * lesson is a generation per model call with every one of them empty, and defect 6
 * is the absence of per-call generations altogether. Putting them together would
 * destroy both. So `collapsed` is instrumented CORRECTLY in every other respect —
 * stable name, root io, propagated session — which makes the loop shape the only
 * variable when you diff it against `good`.
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
 *
 * And the sixth, which lives in `mode: "collapsed"`:
 *
 *   6. THE AGENT LOOP FLATTENED INTO ONE GENERATION. A turn that calls tools is
 *      several model invocations — the model asks for a tool, reads the result,
 *      decides again. Wrapping that whole loop in a single hand-rolled generation
 *      that records only the FINAL answer is the shape Langfuse warns about:
 *
 *        "You should see a `generation` for each model invocation in an agent
 *         loop, interleaved with the `tool` calls it requested. Avoid wrapping
 *         the whole loop in one parent generation that only records the final
 *         output."
 *        — https://langfuse.com/docs/observability/best-practices
 *
 *      What it costs, per the same page: you cannot see what the agent decided
 *      after each tool result, and you cannot see which tool call blew up the
 *      context window — the token count is one aggregate, so the expensive step
 *      is indistinguishable from the cheap ones. Cost still totals correctly,
 *      which is why this survives review: the dashboards look fine.
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

export type InstrumentationMode = "good" | "broken" | "collapsed";

export const INSTRUMENTATION_MODES: InstrumentationMode[] = ["good", "broken", "collapsed"];

export function isInstrumentationMode(value: string): value is InstrumentationMode {
  return (INSTRUMENTATION_MODES as string[]).includes(value);
}

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type TurnResult = {
  traceId: string | undefined;
  answer: string;
  toolsCalled: string[];
  /**
   * How many times the model was actually invoked for this turn.
   *
   * Ground truth from the AI SDK, independent of anything Langfuse received —
   * which is what makes defect 6 measurable: compare this against the number of
   * `generation` observations the trace ended up with.
   */
  modelInvocations: number;
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

/**
 * The name of the one hand-rolled generation in `collapsed` mode.
 *
 * Verb-first and low-cardinality on purpose: the defect being demonstrated is the
 * loop's SHAPE, not its naming, so nothing else about this observation should give
 * a presenter something to point at.
 */
export const COLLAPSED_GENERATION_NAME = "generate-response";

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

  const common = { ...args, history, turnIndex, isFinalTurn, extraTags };
  if (mode === "broken") return runTurnInstrumentedBadly(common);
  if (mode === "collapsed") return runTurnWithCollapsedLoop(common);
  return runTurnInstrumentedWell(common);
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
          modelInvocations: result.steps.length,
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
    // TAGS, AND ONLY TAGS — demo bookkeeping, not part of the reproduction.
    //
    // Without this the broken run carries no session, no user and no tag, so
    // NOTHING in the UI selects it: the presenter is told to open "Traces
    // filtered to compare:broken" and gets an empty screen mid-demo. The tag is
    // the handle. Note what is deliberately still missing from this call —
    // sessionId and userId — which is defect 5, intact.
    return await propagateAttributes({ tags: [...BASE_TAGS, ...extraTags] }, async () => {
      // DEFECT 5: sessionId/userId are stamped on the ROOT only, as free-form
      // metadata. Children carry neither, so observation-level filters and
      // evaluators never match them, and the Sessions view cannot group a thing.
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
        modelInvocations: result.steps.length,
        skusMentioned: extractSkus(answer),
        cartSkus: state.cart.map((l) => l.sku),
        cartSubtotalCents: cartSubtotalCents(state),
      };
    });
  });
}

// ======================================================= COLLAPSED LOOP =====
/**
 * DEFECT 6, on its own: the agent loop flattened into a single generation.
 *
 * Everything else here is the GOOD implementation, line for line — stable trace
 * name, root input/output, propagated session and user, snapshot on the last
 * turn. That is deliberate. A trace with six defects proves nothing about any one
 * of them; this one changes exactly one variable, so when you put it beside
 * `good` the only thing that differs is the shape of the loop.
 */
async function runTurnWithCollapsedLoop(
  args: Required<Pick<RunTurnArgs, "message" | "sessionId" | "userId" | "history" | "turnIndex" | "isFinalTurn" | "extraTags">>,
): Promise<TurnResult> {
  const { message, sessionId, userId, history, turnIndex, isFinalTurn, extraTags } = args;

  return await startActiveObservation(TRACE_NAME, async (root) => {
    return await propagateAttributes(
      {
        traceName: TRACE_NAME,
        sessionId,
        userId,
        tags: [...BASE_TAGS, ...extraTags, ...(isFinalTurn ? [CONVERSATION_END_TAG] : [])],
        metadata: { agentModel: AGENT_MODEL, turn: String(turnIndex + 1) },
      },
      async () => {
        root.update({
          input: { message },
          metadata: { turn: turnIndex + 1, priorTurns: history.length / 2 },
        });

        // The hand-rolled wrapper. Opened before the loop, closed after it, and
        // given the final answer as its output — which is the whole defect. It
        // looks responsible: named well, typed as a generation, carrying the model
        // and the real token totals. Everything about it is right except its
        // GRANULARITY.
        const collapsed = startObservation(
          COLLAPSED_GENERATION_NAME,
          { model: AGENT_MODEL, input: { message } },
          { asType: "generation" },
        );

        const tools = buildTools(sessionId);
        const result = await generateText({
          model: anthropic(AGENT_MODEL),
          system: SYSTEM_PROMPT,
          messages: [...history, { role: "user" as const, content: message }],
          tools,
          stopWhen: stepCountIs(6),
          // This is how the defect actually happens: the framework's own
          // per-invocation telemetry is switched off — usually because the team
          // decided to "instrument it ourselves" — and the hand-rolled span above
          // replaces N generations and their interleaved tool calls with one box.
          // Note it takes the `tool` observations with it, so the tree cannot show
          // what the model did between them either.
          telemetry: { isEnabled: false },
        });

        const answer = result.text?.trim() || "(no answer)";
        const toolsCalled = result.steps
          .flatMap((s) => s.toolCalls ?? [])
          .map((c) => c.toolName);

        // Aggregated usage across every step. Correct, and useless for finding
        // which step is expensive — that is the second cost Langfuse names.
        const usageDetails: Record<string, number> = {};
        if (result.usage.inputTokens !== undefined) usageDetails["input"] = result.usage.inputTokens;
        if (result.usage.outputTokens !== undefined) usageDetails["output"] = result.usage.outputTokens;

        collapsed.update({
          output: answer,
          ...(Object.keys(usageDetails).length > 0 ? { usageDetails } : {}),
          // The step count is recorded so the trace itself admits what it hid.
          // In the wild nobody writes this down, which is exactly why the shape
          // survives: there is nothing in the trace that looks wrong.
          metadata: { modelInvocations: result.steps.length, toolCalls: toolsCalled.length },
        });
        collapsed.end();

        root.update({ output: answer });

        const state = getSessionState(sessionId);
        const out: TurnResult = {
          traceId: getActiveTraceId(),
          answer,
          toolsCalled,
          modelInvocations: result.steps.length,
          skusMentioned: extractSkus(answer),
          cartSkus: state.cart.map((l) => l.sku),
          cartSubtotalCents: cartSubtotalCents(state),
        };

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
