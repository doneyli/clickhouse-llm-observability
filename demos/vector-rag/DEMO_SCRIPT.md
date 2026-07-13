# Vector RAG — Demo Script (the honest baseline: naive RAG, fully traced)

A ready-to-run demo of a **textbook single-shot RAG pipeline** — ChromaDB
in-process, LangChain, Claude — fully traced to **Langfuse**, with its
generation prompt **managed in Langfuse and shipped by label**. This is
deliberately the *naive baseline*: the shape most teams' first RAG takes. Use it
to teach the fundamentals in fifteen minutes, and to set up the pivot to the
self-correcting [`agentic-rag`](../agentic-rag/DEMO_SCRIPT.md) demo.

- **App:** embed → retrieve (top-3) → generate, over a 10-document knowledge base
  (ClickHouse & LLM-observability topics), CLI batch + interactive
- **Vector store:** ChromaDB **in-process** (re-indexed each run — zero extra infra)
- **Models:** `all-MiniLM-L6-v2` embeddings (local, CPU) + `claude-sonnet-4-6`
- **Observability backend:** Langfuse (`http://localhost:3001`), trace name `vector-rag`
- **Run length:** 10–12 min full; ~5 min short path (Acts 1–2)

> Positioning in one line: **this demo is where RAG conversations start; the
> agentic-rag demo is where they end.** If the audience is already past "what is
> RAG," skip straight to [`../agentic-rag/DEMO_SCRIPT.md`](../agentic-rag/DEMO_SCRIPT.md).

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

The short path is Acts 1–2. Act 4 is the designed hand-off to agentic-rag —
if that demo is on the agenda, treat this whole script as its opening act.

---

## 0 · Pre-flight (do this BEFORE the meeting)

```bash
# CRITICAL: clear leaked Langfuse keys — exported shell vars override .env and 401 silently.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

# Stack up; .env needs ANTHROPIC_API_KEY + Langfuse keys
docker compose --profile langfuse up -d
docker compose --profile demo build vector-rag

# Seed the managed prompt (NOT covered by setup.sh — Act 2 needs this)
python scripts/seed-app-prompts.py

# Seed code evaluators if scores are missing (setup.sh normally does this)
./scripts/seed-code-evaluators.sh

# Fresh traces (10 questions; FIRST run also downloads the embedding model — do it now, not live)
docker compose --profile demo run --rm vector-rag python main.py
```

**Browser tabs ready:** Langfuse Traces filtered to name `vector-rag` (`:3001`,
`demo@example.com` / `demodemo1!`), the **Prompts** tab open on
`vector-rag-generation`, and a terminal for interactive mode.

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| **RAG pipeline traced end-to-end** (LangChain auto-instrumentation) | Act 1 — the `vector-rag` trace |
| **Token usage + cost per answer** | Act 1 — click the generation |
| **Prompt management** (fetch by label, fallback, version linked) | Act 2 — `vector-rag-generation` |
| **Ship a prompt change with no redeploy** | Act 2 — edit → re-run → v2 on the trace |
| **Structural + credential guardrails on every generation** | Act 3 — `structure-clean`, `credential-leak` |
| **The limits of naive RAG** (and why they're invisible without evals) | Act 4 — out-of-corpus question + the gap list |

---

## Opening · Locate the pain (2 min, no screen yet)

**Frame.** Every team's first RAG looks like this app: one vector lookup, stuff
the chunks into a prompt, answer. It demos brilliantly and ships fast. The
problems arrive later, and they're all *visibility* problems: you can't see what
was retrieved, you can't measure whether answers stayed grounded, and the prompt
you tune weekly is hardcoded next to the business logic.

**Ask (these steer the session):**
- "Where is your team on RAG — evaluating, prototype, production?"
- "For the RAG you have: can you see what the retriever actually returned for a
  given bad answer?"
- "How do you change your RAG prompt today, and how do you know the change
  helped?"

**Land.** "I'll show the baseline honestly — a naive pipeline, instrumented the
cheap way — what that buys you (full traces, cost, managed prompts, free
guardrails), and exactly where it runs out of road. Then, if you want, the
self-correcting version that fixes what this one can't."

---

## Act 1 · The pipeline on glass (3 min)

**Frame.** Instrumentation effort is the first objection to observability. So
watch what the *minimum* integration — one callback handler — buys.

**Show.** Run it:

```bash
docker compose --profile demo run --rm vector-rag python main.py
```

It indexes the 10-doc corpus (watch `Indexed N chunks from 10 documents` — chunked
at 500 chars, embedded locally, in-memory), then answers 10 questions like
*"How does ClickHouse handle high-cardinality data?"* and *"What are vector
embeddings and how are they used?"*.

In Langfuse → **Traces**, open a `vector-rag` trace (tags `vector-rag`, `demo`):

- The LangChain chain appears automatically — prompt template → **ChatAnthropic
  generation** → output parser, with metadata `purpose: rag_generation`.
- Click the generation: **input shows the retrieved context** (three chunks,
  `---`-separated, each traceable to its source doc by title) above the
  question; output is the answer; **tokens, cost, latency, model** on the right.
- Point at the **Prompt** panel: the generation is stamped with the exact
  version of `vector-rag-generation` that produced it — Act 2's setup.

**Land.** "One callback handler in the code — that's the integration — and every
answer is inspectable: what context the model was given, what it cost, which
prompt version shaped it. When someone reports a bad answer, you open the trace
and read the actual chunks instead of guessing what the retriever did."

**Ask.** "Today, when your RAG answers wrong, can you reconstruct what the model
actually saw? How long does that take?"

> **Presenter note — one honest limitation, said out loud:** the retrieval call
> here happens *outside* the instrumented chain, so it isn't a first-class span —
> you see its *output* (the context in the generation input), not a timed,
> scored retrieval step. That's typical of minimum-effort instrumentation, and
> it's the first thing the agentic-rag demo fixes (typed `retriever` spans,
> graded and scored). Naming this yourself builds more trust than hoping nobody
> asks.

---

## Act 2 · Ship a prompt without a deploy (4 min) — the money moment

**Frame.** The prompt is the most-tuned artifact in any RAG system — grounding
rules, tone, format, refusal behavior all live there. In most stacks it's a
string in the repo, so every tweak rides the release train.

**Show.** **Prompts** tab → `vector-rag-generation`, label `production`. The app
fetches it by label at startup and falls back to an identical inline template if
Langfuse is unreachable — a fresh clone always runs.

Edit it live in the UI — a visible change, e.g. add *"Answer in exactly three
bullet points."* — save as v2, move the `production` label. Re-run:

```bash
docker compose --profile demo run --rm vector-rag python main.py
```

Answers restructure themselves. Open a new trace: the generation now links to
**v2**. Flip between v1- and v2-linked traces — behavior, cost, and version
travel together.

**Land.** "No rebuild, no redeploy — promote a label and the next run serves the
new prompt, and every generation is stamped with the version that produced it.
That turns 'someone changed the prompt and quality moved' from archaeology into
a filter. Version-linked traces are also what make prompt A/B tests honest —
the full compare-and-promote workflow is in this repo's real-estate demo."

**Ask.** "Who tunes your prompts — and how many of their ideas die waiting for a
deploy window? What would they try this week if shipping one were a label
promotion?"

---

## Act 3 · Free guardrails on every answer (2 min)

**Frame.** Before you ever pay for an LLM judge, there's a class of quality
checks that are mechanical: did it answer at all, did it truncate mid-sentence,
did it leak a template placeholder or a credential? Those should cost nothing
and run on everything.

**Show.** On any trace, open **Scores**:

- `output-present`, `structure-clean`, `response-length` — from the
  `response-structure-check` code evaluator (catches empty outputs, unclosed
  code fences, leaked `{context}` placeholders, mid-sentence truncation)
- `credential-leak` / `leak-type` — from `credential-leak-guard`, scanning every
  generation stack-wide for key-shaped strings

These run **inside Langfuse at ingest** — deterministic TypeScript, 100% of
traffic, typically scored within 30 seconds, no LLM cost.

**Land.** "`response-length` alone gives you drift detection for free — chart it
and you'll see a bad prompt change or a truncation bug the day it ships.
Semantic checks (relevance, hallucination) are the LLM-judge layer; adding a
managed judge scoped to `vector-rag` traces is a two-minute UI change. Cheap
mechanical layer first, judges where they earn their cost."

**Ask.** "What's your equivalent of `structure-clean` — the mechanical 'this
answer is broken' signal you wish fired automatically?"

---

## Act 4 · Where naive RAG runs out of road (3 min) — the hand-off

**Frame.** Now the honest part. This pipeline retrieves once and *hopes*. It has
no idea whether the retrieval was any good — and neither do you, unless you're
looking.

**Show.** Interactive mode, one out-of-corpus question:

```bash
docker compose --profile demo run --rm vector-rag python main.py --interactive
# ask:  How do I configure Kubernetes ingress?
```

The retriever dutifully returns the three *least-unrelated* chunks — top-k
always returns k — and the prompt's grounding instruction is the only thing
standing between those chunks and a confident wrong answer. Open the trace and
show the mismatched context in the generation input: *"the retrieval failed,
silently; nothing measured it, nothing retried."*

Name the gaps as a list — each one is an agentic-rag feature: no retrieval
grading (is this context relevant?), no query rewrite and retry, no groundedness
check on the answer, no retrieval score to filter or alert on.

**Land.** "That's the ceiling of retrieve-and-stuff: failures are silent and
per-query. The fix isn't a bigger model — it's a *loop* that grades its own
retrieval and corrects itself, with each grade logged as a score. That's the
agentic-rag demo: same stack, ClickHouse-native vectors instead of a bolt-on
store, and every decision you just couldn't see becomes a scored, graphed step."

**Ask.** "Of those gaps, which one bites you first — silent retrieval misses, or
not being able to prove groundedness? That tells us where to go next."

---

## Close (1 min)

Three takeaways: **a one-callback integration made the whole pipeline
inspectable** (context, cost, prompt version per answer); **the prompt ships by
label**, not by deploy; **free deterministic guardrails score every answer at
ingest**. And one honest one: naive RAG's failures are silent — which is the
reason the self-correcting version exists one directory over. The repo is
public: `demos/vector-rag/` is four small files — clone it, swap
`documents.py` for your corpus, and it's your prototype.

---

## Under the hood — how it's instrumented (for the "show me the code" moment)

All of it lives in `demos/vector-rag/` — this is the *minimum-effort* end of the
instrumentation ladder (one callback + context propagation), which is exactly
its teaching value. Contrast `demos/agentic-rag/graph.py` (typed observations)
and `demos/real-estate/agent/` (fully manual tree).

**1 · Managed prompt with fallback + version linking — `rag_pipeline.py:26`**
```python
lf_prompt = get_managed_prompt(name)   # name="vector-rag-generation" at the :117 call site; label="production"
tmpl = ChatPromptTemplate.from_template(lf_prompt.get_langchain_prompt())
tmpl.metadata = {"langfuse_prompt": lf_prompt}   # THIS links prompt version → generation
```
*Why it matters:* one metadata line is the entire Act 2 story; the identical
inline fallback (`rag_pipeline.py:118`) is why a fresh clone runs unseeded.

**2 · The chain, tagged with intent — `rag_pipeline.py:133`**
```python
(self.response_prompt | self.llm | StrOutputParser())
    .with_config({"metadata": {"purpose": "rag_generation"}})
```

**3 · Retrieval: top-3, concatenated — `rag_pipeline.py:137`**
```python
docs = self.retriever.invoke(question)                       # Chroma similarity, k=3
self._context = "\n\n---\n\n".join(d.page_content for d in docs)
```
*Why it matters:* note it's invoked *outside* the callback-instrumented chain —
the Act 1 presenter note, in one line. The context still lands in the trace via
the generation's input.

**4 · The whole Langfuse integration — `langfuse_config.py:140`**
```python
from langfuse.langchain import CallbackHandler
handler = CallbackHandler()      # passed per-query as callbacks=[handler]
```

**5 · Trace name + tags by propagation — `langfuse_config.py:120`**
```python
with propagate_attributes(trace_name="vector-rag", tags=["vector-rag", "demo"]):
    ...
```

**6 · Graceful degradation — `langfuse_config.py:16`**
```python
LANGFUSE_ENABLED = bool(PUBLIC_KEY and SECRET_KEY)   # no keys → app runs untraced
```

> One-liner for the room: *"A callback handler, a context manager for the trace
> name, and one metadata line to link the prompt version. That's the entire
> integration — everything you saw fell out of those."*

---

## Talking points & objections

- **"ChromaDB in production?"** Here it's the deliberately-simple choice: in-
  process, re-indexed per run, zero infra — right for a 10-doc teaching demo. The
  production-shaped answer is the agentic-rag demo: **ClickHouse-native HNSW**,
  where vectors live in the same engine as your analytics and your traces, and
  hybrid vector+SQL+text queries are one statement.
- **"Where's the retrieval span?"** Named honestly in Act 1: minimum-effort
  instrumentation traces the chain, and this retriever runs outside it. The
  context is still captured on the generation. Typed, scored retrieval steps are
  the agentic-rag demo.
- **"Can judges score this automatically?"** Yes — managed LLM-as-a-Judge
  evaluators are scoped by trace name/tags in the UI; the stack's default judges
  are scoped to its test harness, so add one targeting `vector-rag` (two
  minutes) to score relevance/hallucination on live traffic.
- **"Why local embeddings?"** `all-MiniLM-L6-v2` runs on CPU in-container — no
  embedding API cost or key, and the model caches in a Docker volume after
  first run. Swap in any embedding endpoint if you'd rather.
- **"Framework lock-in?"** It's LangChain + the Langfuse callback here, but the
  sibling demos show the same platform over LangGraph, CrewAI, and plain-Python
  SDK instrumentation. The traces land the same either way.
- **"Where does trace data live?"** Langfuse stores it in **ClickHouse** — fast
  score filters and dashboards, and your traces sit in an engine you can query
  with SQL yourself.

---

## Reset / re-run

```bash
docker compose --profile demo run --rm vector-rag python main.py                 # fresh traces
docker compose --profile demo run --rm vector-rag python main.py --interactive  # Act 4 moment
python scripts/seed-app-prompts.py                                              # re-seed prompt (idempotent)
docker compose --profile demo build vector-rag                                  # after code edits
```

The corpus re-indexes in-memory on every run — there's no vector-store state to
reset. First run after a wipe re-downloads the embedding model (cached in the
`vector-rag-models` volume thereafter).
