# Agentic RAG — Demo Script (ClickHouse-native vectors + Langfuse)

A ready-to-run demo of a **self-correcting (CRAG) RAG agent** that runs on
**ClickHouse-native vector search** and is fully observable in **Langfuse**. The
same graph drives a **CLI/HTTP service** and a **no-code LibreChat agent**, and
both emit the same fully-scored trace.

- **Vector store + engine:** a dedicated **ClickHouse 26.3** container
  (`clickhouse-vectors`) with a native HNSW index — no separate vector database
- **Agent:** a LangGraph CRAG loop — `route → retrieve → grade → (rewrite →
  retrieve)* → generate → reflect` (`graph.py`)
- **Observability backend:** Langfuse (`http://localhost:3001`), trace tag `agentic-rag`
- **Chat surface:** LibreChat (`http://localhost:3080`) → **Agentic RAG Assistant** agent
- **HTTP:** `POST http://localhost:8006/query`
- **Run length:** ~20–25 min full; 8–10 min short path (Acts 1–3)

> The graph, ClickHouse store, and Langfuse instrumentation all live in
> `demos/agentic-rag/`. The deep screen-by-screen reference is
> [`docs/AGENTIC_RAG_DEMO_RUNBOOK.md`](../../docs/AGENTIC_RAG_DEMO_RUNBOOK.md);
> the question catalog + the reliable self-correction trigger is
> [`DEMO_QUESTIONS.md`](DEMO_QUESTIONS.md); the architecture diagram is
> [`docs/AGENTIC_RAG_ARCHITECTURE.md`](../../docs/AGENTIC_RAG_ARCHITECTURE.md).

---

## How to run this script

It's written to be a **conversation, not a walkthrough**. Every act does three
things: it **frames** a problem the audience already has, **shows** how the
platform answers it, and **lands** the benefit — then hands a **question** back to
the room. So each act carries four beats:

- **Frame** — the problem, in their terms (say this *before* you touch the screen).
- **Show** — the exact clicks / commands.
- **Land** — the "so what": the benefit, not the feature.
- **Ask** — an open question that invites them to map it to their own world.

Don't rush the **Ask** — the answers tell you which acts to go deep on and which
to skim, and they surface the specifics you'll need if this turns into a
proof-of-concept. The short path is Acts 1–3; add 3b/4 when there's appetite.

---

## 0 · Pre-flight (do this BEFORE the meeting)

```bash
# CRITICAL: clear leaked Langfuse keys — exported shell vars override .env and 401 silently.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

# Langfuse (+ its ClickHouse 25.8) and the dedicated vector ClickHouse 26.3
docker compose --profile langfuse up -d
docker compose --profile demo up -d clickhouse-vectors
docker compose --profile demo build agentic-rag mcp-rag-retriever

# Build the vector index (one-time, idempotent) — expect "Inserted 28 chunks."
docker compose --profile demo run --rm agentic-rag python ingest.py

# Langfuse-managed generation prompt (for Act 3b): v1 + v2(production)
python scripts/seed-langfuse-prompt.py

# Independent managed judges (for Act 3 / Act 4): faithfulness/context-relevance/answer-relevance
./scripts/seed-agentic-rag-evaluators.sh

# LibreChat agent (for Act 4)
docker compose --profile demo up -d mcp-rag-retriever
./scripts/seed-librechat-agents.sh
```

**Browser tabs ready:** Langfuse Traces (`:3001`, `demo@example.com` / `demodemo1!`),
a terminal, LibreChat (`:3080`), and the architecture diagram.

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| **ClickHouse native vector search** (HNSW, `cosineDistance`) | Act 1 — schema + `EXPLAIN` shows the index is used |
| **Hybrid** (vector + metadata + text + joins in one SQL) | Act 1 — the "one engine" argument |
| **Agentic self-correction** (route → grade → rewrite → retry → reflect) | Act 2 — the step log |
| **Agent Graph view** (typed observations) | Act 3 — Langfuse Graph tab |
| **Score on a span, logged twice** (`retrieval_relevance`) | Act 3 — attempt 1 = 0, attempt 2 = 1 |
| **Trace-level outcome score** (`groundedness`) | Act 3 — reflect step |
| **Independent managed judges** (faithfulness / context-relevance) | Act 3 — the "who checks the checker" moment |
| **Sessions** (whole-interaction cost/latency) | Act 3 — Sessions view |
| **Prompt management** (versioned, label-routed, linked) | Act 3b — Prompts tab |
| **Same loop, no code** (MCP tool + LibreChat) | Act 4 — identically-scored trace |

---

## Opening · Locate the pain (2 min, no screen yet)

**Frame.** Most RAG in production does one vector lookup and stuffs the results
into a prompt. It breaks the moment the first retrieval is wrong — and nobody finds
out, because a confidently-wrong answer looks exactly like a good one. Two things
compound that: you can't tell retrieval failures from generation failures, and you
usually can't *see* the retrieval at all.

**Ask (these steer the session):**
- "Are you doing RAG today? Single-shot retrieve-and-stuff, or something with more
  control?"
- "When it returns a bad answer, can you tell whether retrieval missed or the model
  fumbled? How?"
- "What's your vector store today — a dedicated DB, pgvector, something else? How
  many moving parts are in that stack?"

**Land.** "I'll show a RAG agent that grades its own retrieval and *corrects
itself*, running on ClickHouse's native vector search — no separate vector DB —
with every decision traced and scored in Langfuse. Two things to watch: ClickHouse
is doing the vector work, and Langfuse is showing the agent's reasoning as a graph."

---

## Act 1 · ClickHouse IS the vector database (5 min)

**Frame.** The default assumption is that vectors need a dedicated vector database
bolted on next to everything else. That's more infrastructure, more sync, and a
silo your embeddings can't be joined against. Let's test that assumption.

**Show.** Prove the native index is real, and actually used:
```bash
docker exec clickhouse-vectors clickhouse-client --user agentic --password agentic123 \
  --query "SHOW CREATE TABLE agentic_rag.kb_chunks FORMAT TSVRaw"
```
Point at `embedding Array(Float32)` and
`INDEX vec_idx embedding TYPE vector_similarity('hnsw', 'cosineDistance', 384)`.
```bash
docker exec clickhouse-vectors clickhouse-client --user agentic --password agentic123 --query "
EXPLAIN indexes=1
SELECT doc_title FROM agentic_rag.kb_chunks
ORDER BY cosineDistance(embedding, (SELECT embedding FROM agentic_rag.kb_chunks LIMIT 1)) ASC
LIMIT 3"
```
The plan shows a `Skip` node → `Name: vec_idx` → `Description: vector_similarity` —
the planner picked the vector index, not a brute-force scan.

**Land.** "This is a normal MergeTree table — embeddings are just an array column,
HNSW is native (no extension, no experimental flag on 26.x). Because it's SQL, I can
do what pure vector DBs struggle with: combine similarity with metadata filters,
full-text (the 26.2 text index), and joins against my other data — in one query.
The *same engine* stores our Langfuse traces, our vectors, and our business data."

**Ask.** "How many moving parts is your retrieval stack right now? And do you ever
need to filter or join vector results against your operational data — and can you
today?"

> **Fallback:** if the table is empty, re-run `... run --rm agentic-rag python ingest.py`.

---

## Act 2 · The agent that checks its own work (5 min)

**Frame.** "Better RAG" isn't a bigger model — it's a *loop* that notices when
retrieval was weak and does something about it. Here's that loop on the question
that reliably forces a self-correction.

**Show.** Run the self-correction question (from [`DEMO_QUESTIONS.md`](DEMO_QUESTIONS.md) — Q3):
```bash
docker compose --profile demo run --rm agentic-rag python -c "
from graph import create_agent
r = create_agent().run('What vector databases exist and how does ClickHouse compare?')
print('STEPS  :', ' | '.join(r['steps']))
print('GROUNDED:', r['grounded'])
print('ANSWER :', r['answer'][:400])
"
```
The step log reads like the agent's reasoning:
```
route → kb | retrieve (attempt 1) → 4 chunks | grade → not relevant |
rewrite → '…' | retrieve (attempt 2) → 4 chunks | grade → relevant |
generate → drafted answer | reflect → grounded
```

**Land.** "Watch the loop: it routed to the knowledge base, retrieved, then *graded
its own retrieval and rejected it*, rewrote the query, retrieved again, and only
*then* answered — and finally checked the answer was grounded in the context. A
naive pipeline would have answered from that first weak retrieval and you'd never
know. This is CRAG: route, retrieve, grade, correct, generate, reflect."

**Ask.** "When your RAG misses today, what's the recovery — does anything catch it,
or does the bad answer just go out? What would it be worth to have the system retry
instead of you finding out from a customer?"

> **Fallback:** the grader is an LLM, so it's not 100% deterministic. Q3 is the
> designed fail-then-recover case; re-run once to confirm before a high-stakes demo.

---

## Act 3 · See the agent's mind in Langfuse (6 min)

**Frame.** A step log in a terminal is nice for you; it doesn't scale to production.
The question is whether you can *see and measure* this loop across thousands of runs
— which retrievals missed, whether answers stayed grounded, and how that trends.

**Show.** Langfuse → **Traces** → open the latest `agentic-rag` trace → **Graph** tab.
You see the run as a node graph — `route`, `retrieve`, `grade-relevance`,
`rewrite-query`, `generate`, `reflect-groundedness` — with the
retrieve→grade→rewrite→retrieve loop drawn out.

- Click `retrieve` (a **retriever** obs) → the query + returned chunk titles/distances.
- Click `grade-relevance` (an **evaluator** obs) → its **`retrieval_relevance`**
  score. On this self-correcting run there are **two** grade steps: attempt 1 scored
  **0** (not relevant), attempt 2 scored **1** — the recovery, visible.
- Click `reflect-groundedness` → the trace-level **`groundedness`** score for the
  final answer.

**Land.** "These are real Langfuse Scores, so retrieval quality and groundedness
become things you *chart over time, filter traces by, and compare* — naive vs
agentic RAG, model A vs model B. The typing is what makes Langfuse draw this as an
agent graph instead of a flat log."

**Who checks the checker? (higher-value beat).** The scores above are the agent
grading *itself*. If you ran `./scripts/seed-agentic-rag-evaluators.sh`, Langfuse
*independently* scores the same answers server-side: `faithfulness`,
`context-relevance`, `answer-relevance` — visible on the `generate` observation.
"In-graph evals *drive* the loop synchronously; managed evals *monitor* it
independently. When they agree, you trust the self-grade; when they diverge — the
agent says its retrieval was relevant but the independent judge says 0.4 — that's a
calibration signal telling you the self-assessment is drifting."

Then: **Sessions** → open the agent's session → whole-interaction cost and latency.

**Ask.** "If you could score every retrieval and every answer automatically, what
would you *do* with it — alert, block a release, feed a dataset? And who checks your
RAG's quality today — is anything watching the watcher?"

> **Fallback:** empty Graph tab = trace still ingesting (async worker); wait ~20s and
> refresh, or open a slightly older `agentic-rag` trace.

---

## Act 3b · The prompt lives in Langfuse (bonus, 3 min)

**Frame.** In most stacks the generation prompt is a string in the code, so tuning
it means a code change and a deploy. That's friction on the thing you tune most.

**Show.** Langfuse → **Prompts** → `agentic-rag-generation`: two versions with commit
messages, the `production` label on v2 (adds grounding rules + citations), the
`{{context}}`/`{{question}}` variables, and a **linked-generations** panel showing
which traces used each version. The agent's `generate` node fetches this by the
`production` label at runtime (you saw `promptVersion` on the generate step).

**Land.** "The agent doesn't hardcode its prompt — it pulls the version labelled
`production`. Edit here, label a new version `production`, and the next run picks it
up with **no redeploy**, and every generation links back to the exact version that
produced it — so you can compare quality and cost across versions."

**Ask.** "How does a prompt change reach production for you today — and who's allowed
to make one?"

---

## Act 4 · Same loop, no code (LibreChat) (4 min)

**Frame.** A developer wants an API; an analyst wants a chat box. The usual outcome
is two implementations with two different levels of governance. It doesn't have to be.

**Show.** LibreChat → select the **Agentic RAG Assistant** agent → ask:
> *"How does RAG reduce hallucinations, and how does ClickHouse fit in?"*

The agent calls the `agentic_rag_answer` MCP tool, which runs the **full
self-correcting graph** server-side, then answers with cited document titles. Two
linked traces land in Langfuse: a thin `LibreChat` orchestration trace (the tool
call) and a separate `agentic-rag` trace scored **identically to Act 3** —
`retrieval_relevance`, `groundedness`, and the managed `faithfulness` /
`context-relevance` / `answer-relevance` judges.

**Land.** "Same graded pipeline as the LangGraph service — the exact same loop —
exposed as one MCP tool behind a no-code chat agent. A developer gets the service, an
analyst gets the chat, and *both are evaluated the same way*. Governance doesn't fork."

**Ask.** "Who in your org would reach for chat vs an API — and do those audiences
need the same quality guarantees, or do they diverge today?"

> **Fallback:** tool missing → confirm `mcp-rag-retriever` + `agentic-rag` are up and
> re-run `./scripts/seed-librechat-agents.sh`. No scores on the `agentic-rag` trace →
> run `./scripts/seed-agentic-rag-evaluators.sh` once and re-ask (judges are `NEW`-scoped).

---

## Closing · Why this matters (1 min)

**Land three takeaways:**
1. **One engine.** ClickHouse is your vector database *and* your analytics warehouse
   *and* your observability backend — native HNSW, hybrid SQL, fewer moving parts.
2. **Agentic RAG is a measurable loop**, not magic: route, grade, correct, reflect.
3. **Langfuse makes the loop visible and scoreable** — so you can prove retrieval
   quality, catch regressions, and calibrate the agent's self-grades against
   independent judges. "Naive RAG hopes; this system checks its work — and you can
   watch it do so."

---

## Under the hood — how it's instrumented (for the "show me the code" moment)

All of it lives in `demos/agentic-rag/`. Open these when someone asks *how* the graph
becomes a scored trace.

**1 · Two layers of instrumentation — `langfuse_config.py`**
```python
# langfuse_config.py:42  the LangChain/LangGraph callback handler — drives the auto Agent Graph
def get_handler(): return CallbackHandler()
# langfuse_config.py:79  a CUSTOM typed-observation context manager (this is the app's helper,
# NOT the stock @observe decorator) wrapping the low-level SDK with null-safety + a typed as_type
@contextmanager
def observe(name, as_type="span", input=None):
    with client.start_as_current_observation(as_type=as_type, name=name, input=input) as obs:
        yield obs
```
*Why it matters:* the graph renders automatically from the LangGraph handler, and the
explicit `observe()` calls add the *typed* semantics on top. The whole thing degrades
to a no-op when Langfuse keys are absent — the agent still runs.

**2 · Typed observations are what make it an agent graph — `graph.py`**
```python
with lf.observe("route",   as_type="agent")     as obs: ...   # :89
with lf.observe("retrieve",as_type="retriever", input=query) as obs: ...   # :115
with lf.observe("grade-relevance", as_type="evaluator") as obs: ...        # :126
with lf.observe(gen_name,  as_type="generation") as obs: ...   # :187
with lf.observe("reflect-groundedness", as_type="evaluator") as obs: ...   # :229
```
*Why it matters:* `as_type` is the lever — `retriever` / `tool` / `evaluator` / `agent`
are what tell Langfuse to draw the RAG-aware graph you showed in Act 3, instead of a
flat list.

**3 · The two scores — span-level vs trace-level — `graph.py`**
```python
# grade_node — span-level, so it can fire MORE THAN ONCE per run (the double 0→1 in Act 3)
lf.score_current_span("retrieval_relevance", 1.0 if relevant else 0.0,
                      comment=f"attempt {attempt}: context graded ...")   # :139
# reflect_node — trace-level outcome score for the final answer
lf.score_current_trace("groundedness", 1.0 if grounded else 0.0, comment="agent self-reflection")  # :238
```
*Why it matters:* the "scored twice" moment isn't a trick — it's a deliberate choice
to log `retrieval_relevance` on the *span*, so each self-correction attempt carries its
own score.

**4 · Prompt management + linking (Act 3b) — `graph.py` (`generate_node`)**
```python
prompt_obj = lf.get_prompt(GEN_PROMPT_NAME, label="production")   # :193  fetch by label
text = prompt_obj.compile(context=context, question=state["question"])
obs.update(prompt=prompt_obj)                                     # :199  link version → trace
```
*Why it matters:* the prompt is versioned data in Langfuse, with a local fallback — same
Deploy-node story as prompt promotion, no redeploy.

**5 · Feeding the independent judges — `graph.py` (`generate_node`)**
```python
gen_name = "generate" if context else "generate-direct"          # :186  skip judges on context-less answers
obs.update(input={"question": state["question"], "context": context}, output=answer)  # :217
```
*Why it matters:* exposing question + context on the `generate` observation is what lets
the managed `faithfulness` / `context-relevance` judges score it *independently* — the
"who checks the checker" beat. Direct-route answers are named `generate-direct` so they're
correctly excluded.

**6 · Session wrapping — `graph.py` (`run`)**
```python
with lf.trace_context("agentic-rag", session_id=session_id):     # :290
    final = self.graph.invoke({"question": question}, config={"callbacks": [handler]})
```
*Why it matters:* one `trace_context(...)` around the whole graph invocation names the
trace and groups multi-turn runs into a Session (Act 3).

> One-liner for the room: *"Each node is a typed `with`-block, a score is a single call,
> and the prompt + independent judges ride along on the generation. The graph view and
> all the scores fall out of that."*

---

## Talking points & objections

- **"Why not a dedicated vector DB?"** Fewer moving parts, hybrid SQL+vector+text in
  one query, joins against operational data, and the same engine already runs your
  observability. ClickHouse scales vector indexes across replicas for sets that exceed
  one node's memory.
- **"Is the vector index production-ready?"** Native since 25.8; the 26.2 release brought
  the `text` index and `QBit` type to GA. We pin a dedicated 26.3 container so the demo
  uses the latest, leaving Langfuse's bundled ClickHouse (25.8) untouched.
- **"How do you know retrieval is good?"** The grade + reflect evaluators are logged as
  Langfuse scores; the independent managed judges cross-check them. Wire both to Langfuse
  datasets + experiments to compare naive vs agentic RAG offline.
- **"Cost of the extra LLM calls?"** Each grade/rewrite/reflect is a small Claude call;
  all token usage is captured per-observation in Langfuse, so the route/grade overhead is
  measurable against the quality gain.
- **"Framework lock-in?"** The graph is LangGraph, but instrumentation is the Langfuse SDK
  + the LangChain callback handler; the same patterns apply to any framework or plain code.

---

## Reset / re-run

```bash
# Rebuild the vector index from scratch
docker compose --profile demo run --rm agentic-rag python ingest.py

# Re-run the 5 CLI demo questions (one shared session) or interactively
docker compose --profile demo run --rm agentic-rag python main.py
docker compose --profile demo run --rm agentic-rag python main.py --interactive

# Single question over HTTP
curl -s localhost:8006/query -H 'content-type: application/json' \
  -d '{"question":"What vector databases exist and how does ClickHouse compare?"}'

# Wipe the vector store entirely
docker compose --profile demo rm -sf clickhouse-vectors
docker volume rm clickhouse-llm-observability_clickhouse-vectors-data
```
