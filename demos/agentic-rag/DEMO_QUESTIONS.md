# Agentic RAG — Demo Questions & Self-Correction

Which questions to ask, what each one demonstrates, and specifically how to make the
agent **grade its own retrieval, reject it, rewrite the query, and recover** — the
`retrieval_relevance`-scored-twice moment.

Everything here works identically whether you drive the graph from the CLI
(`main.py` / `/query`) or from **LibreChat** (the `Agentic RAG Assistant` agent →
`agentic_rag_answer` MCP tool). Both execute the same `graph.py`, so both emit the
same fully-scored `agentic-rag` Langfuse trace. See
[../../docs/AGENTIC_RAG_DEMO_RUNBOOK.md](../../docs/AGENTIC_RAG_DEMO_RUNBOOK.md) for
the full talk track.

---

## How the agent decides what to do

The graph (`graph.py`) runs: **route → retrieve → grade → (rewrite → retrieve)\* →
generate → reflect**.

- **route** picks one of three paths from the question:
  - `kb` — answer from the knowledge base (concepts / how-to). This is the path that
    can self-correct.
  - `sql` — dataset-number questions run a live ClickHouse `SELECT` (`sql-tool` node).
  - `direct` — chit-chat / no retrieval needed.
- **grade** asks the LLM *"is this context relevant? yes/no"* and emits a
  `retrieval_relevance` score **on that grade observation** (span-level, so it can be
  scored more than once per run — `grade_node`).
- If **not relevant**, `_grade_edge` routes to **rewrite** (which is prompted to
  *expand abbreviations, add synonyms, be specific*), then back to **retrieve** — up
  to `MAX_RETRIEVE_ATTEMPTS = 2` (initial + one rewrite).
- **reflect** grades whether the answer is grounded in the context and emits a
  trace-level `groundedness` score (`reflect_node`).

---

## Question catalog (the 5 CLI demo questions)

From `main.py` → `DEMO_QUESTIONS`. Ask any of them verbatim in LibreChat too.

| # | Question | Route | Shows |
|---|----------|-------|-------|
| 1 | What is ClickHouse and what is it used for? | `kb` | Clean single-shot RAG (grade passes first try) |
| 2 | How does RAG architecture reduce hallucinations? | `kb` | Single-shot RAG |
| 3 | **What vector databases exist and how does ClickHouse compare?** | `kb` | **Self-correction** — grade fails, rewrite, re-retrieve, recovers ⭐ |
| 4 | Why is ClickHouse well-suited for storing observability data? | `kb` | Single-shot RAG |
| 5 | Hello, what can you help me with? | `direct` | Direct route — no retrieval, no `retrieval_relevance`, reflect skipped |

For the **`sql` route**, ask a dataset-number question, e.g. *"How many rides are in
the nyc_taxi dataset?"* — the graph writes and runs a `SELECT`. Note: in **LibreChat**
the agent answers number questions with the ClickHouse playground tools directly (per
its instructions), so the graph's internal `sql` route is exercised via the CLI /
direct `/query`, not through `agentic_rag_answer`.

---

## ⭐ The self-correction question (`retrieval_relevance` scored twice)

> **"What vector databases exist and how does ClickHouse compare?"**

This is the reliable fail-then-recover case. The returned step log:

```
route → kb
retrieve (attempt 1) → 4 chunks
grade → not relevant          ← retrieval_relevance = 0.0
rewrite → 'List of vector databases and vector search engines available'
retrieve (attempt 2) → 4 chunks
grade → relevant              ← retrieval_relevance = 1.0
generate → drafted answer
reflect → grounded
```

The resulting `agentic-rag` trace carries **two** `retrieval_relevance` scores:

```
retrieval_relevance = 0  | "attempt 1: context graded not relevant"
retrieval_relevance = 1  | "attempt 2: context graded relevant"
```

**In the Langfuse UI:** open the trace and you'll see two `grade-relevance`
observations, each with its own score badge (0.00 then 1.00), and the graph view
draws the `retrieve → grade → rewrite → retrieve → grade` loop.

### Why this question and not just any hard one

The lever is: **first retrieval must miss, but the rewritten query must hit.** Because
the `rewrite-query` node expands abbreviations and adds synonyms, abbreviation-heavy
questions (e.g. *"How does CH stack up vs other vector DBs for storing obs data?"*)
**do** trigger the rewrite loop — but with the small (~28-chunk) demo KB the second
pass often still grades *not relevant*, giving `0.0 → 0.0` (self-corrects but does not
recover). Question #3 fails **and recovers**, which is the story you want.

### Caveat: the grader is an LLM

`grade` is an LLM yes/no, so the loop is not 100% deterministic run-to-run. Question #3
is the designed self-correction case and triggers reliably on demand, but if you need a
*guaranteed* fail-then-recover for a high-stakes rehearsal, re-run it once to confirm,
or lower the grader's first-pass bar in `graph.py`.

---

## Scores you'll see on an `agentic-rag` trace, and where each comes from

| Score | Source | How it's produced |
|-------|--------|-------------------|
| `retrieval_relevance` | in-graph self-grade | `grade_node` posts it per grade (span-level) |
| `groundedness` | in-graph self-grade | `reflect_node` posts it once (trace-level) |
| `faithfulness`, `context-relevance`, `answer-relevance` | managed LLM judges | Langfuse worker; seeded by `../../scripts/seed-agentic-rag-evaluators.sh` (filter: observation `name=generate` + trace tag `agentic-rag`) |
| `credential-leak`, `leak-type` | code evaluator | `../../evaluators/credential-leak-guard.ts` on every generation |

The managed judges are `NEW`-scoped: run
`../../scripts/seed-agentic-rag-evaluators.sh` once, then generate fresh traffic for
`faithfulness` / `context-relevance` / `answer-relevance` to appear.

---

## Running the questions

**CLI (all 5, one shared session):**

```bash
docker compose --profile demo run --rm agentic-rag python main.py
docker compose --profile demo run --rm agentic-rag python main.py --interactive
```

**HTTP (single question):**

```bash
curl -s localhost:8006/query -H 'content-type: application/json' \
  -d '{"question":"What vector databases exist and how does ClickHouse compare?"}'
```

**LibreChat:** open the **Agentic RAG Assistant** agent and ask the question. It calls
the `agentic_rag_answer` tool, which runs this same graph server-side — so you get a
separate, fully-scored `agentic-rag` trace (with the double `retrieval_relevance` for
question #3), alongside a thin `LibreChat` orchestration trace.
