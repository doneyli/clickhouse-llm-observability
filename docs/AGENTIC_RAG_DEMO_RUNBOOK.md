# Agentic RAG Demo Runbook

**Duration:** ~25 minutes
**Target audience:** Teams evaluating ClickHouse for vector / RAG workloads and Langfuse for agent observability. Pairs with the broader [Langfuse Demo Runbook](./LANGFUSE_DEMO_RUNBOOK.md).

**The story:** "Naive RAG retrieves once and hopes. Agentic RAG *routes*, *grades* what it retrieved, *self-corrects*, uses *tools*, and *reflects* on its own answer — all on ClickHouse-native vector search, all observable in Langfuse." See [AGENTIC_RAG_ARCHITECTURE.md](./AGENTIC_RAG_ARCHITECTURE.md) for the diagram.

---

## Pre-Demo Checklist (15 min before)

### Environment
```bash
# CRITICAL: clear any leaked Langfuse keys from your shell — exported shell vars
# override .env in docker compose and will cause 401s on trace export.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

# Confirm .env has the demo keys (pk-lf-1234567890 / sk-lf-1234567890) and ANTHROPIC_API_KEY.
grep -E "LANGFUSE_(PUBLIC|SECRET)_KEY|ANTHROPIC_API_KEY" .env
```

### Infrastructure
```bash
docker compose --profile langfuse up -d                 # Langfuse + its ClickHouse 25.8
docker compose --profile demo up -d clickhouse-vectors  # dedicated ClickHouse 26.3 (vectors)
docker compose --profile demo build agentic-rag mcp-rag-retriever
```

### Build the vector index (one-time, idempotent)
```bash
docker compose --profile demo run --rm agentic-rag python ingest.py
# Expect: "Inserted 28 chunks." + a nearest-neighbor verification block.
```

### Langfuse-managed prompt (for Act 3b)
```bash
python scripts/seed-langfuse-prompt.py   # creates agentic-rag-generation v1 + v2(production)
```

### LibreChat agent (for Act 4)
```bash
docker compose --profile demo up -d mcp-rag-retriever
./scripts/seed-librechat-agents.sh        # creates the "Agentic RAG Assistant" agent
```

### Browser tabs (pre-opened)
1. Langfuse → Traces — http://localhost:3001 (demo@example.com / demodemo1!)
2. A terminal (for the CLI agent run)
3. LibreChat — http://localhost:3080 (for Act 4)
4. `docs/AGENTIC_RAG_ARCHITECTURE.md` (the diagram)

---

## Demo Script

### Opening: Frame the Problem [0:00 - 2:00]

**Screen:** The architecture diagram.

**Say:**
> "Most RAG demos do one vector lookup and stuff the results into a prompt. That breaks the moment the first retrieval is wrong — and you have no idea it happened. Today I'll show an *agentic* RAG system that grades its own retrieval and corrects itself, running on ClickHouse's native vector search, with every decision traced in Langfuse. Two things to watch: ClickHouse is doing the vector work — no separate vector database — and Langfuse is showing us the agent's reasoning as a graph."

---

### Act 1: ClickHouse IS the Vector Database [2:00 - 8:00]

**Screen:** Terminal.

**Action:** Show the schema and prove the native index is real.
```bash
docker exec clickhouse-vectors clickhouse-client --user agentic --password agentic123 \
  --query "SHOW CREATE TABLE agentic_rag.kb_chunks FORMAT TSVRaw"
```

**What audience sees:** An `embedding Array(Float32)` column and
`INDEX vec_idx embedding TYPE vector_similarity('hnsw', 'cosineDistance', 384)`.

**Say:**
> "This is a normal ClickHouse MergeTree table. The embeddings are just an array column, and the HNSW index is native — no extension, no experimental flag on 26.x. The same engine that stores our Langfuse traces can store and search our vectors."

**Action:** Prove the index is actually used (not a brute-force scan).
```bash
docker exec clickhouse-vectors clickhouse-client --user agentic --password agentic123 --query "
EXPLAIN indexes=1
SELECT doc_title FROM agentic_rag.kb_chunks
ORDER BY cosineDistance(embedding, (SELECT embedding FROM agentic_rag.kb_chunks LIMIT 1)) ASC
LIMIT 3"
```

**What audience sees:** A `Skip` node → `Name: vec_idx` → `Description: vector_similarity`.

**Say:**
> "ClickHouse's query planner picked the vector index. And because it's SQL, I can do what pure vector DBs struggle with — combine similarity with metadata filters, full-text (the 26.2 text index), and joins against my other data, in one query."

**Fallback:** If the table is empty, re-run `... run --rm agentic-rag python ingest.py`.

---

### Act 2: The Agent in Action [8:00 - 14:00]

**Screen:** Terminal.

**Action:** Run a question that forces self-correction.
```bash
docker compose --profile demo run --rm agentic-rag python -c "
from graph import create_agent
r = create_agent().run('What vector databases exist and how does ClickHouse compare?')
print('STEPS  :', ' | '.join(r['steps']))
print('GROUNDED:', r['grounded'])
print('ANSWER :', r['answer'][:400])
"
```

**What audience sees:** A step trace such as:
`route → kb | retrieve (attempt 1) → 4 chunks | grade → not relevant | rewrite → '…' | retrieve (attempt 2) → 4 chunks | grade → relevant | generate → drafted answer | reflect → grounded`

**Say:**
> "Watch the loop. It routed to the knowledge base, retrieved, then *graded its own retrieval and decided it wasn't good enough*. So it rewrote the query, retrieved again, and only then answered — and finally checked the answer was grounded in the context. A naive pipeline would have answered from that first weak retrieval. This is the CRAG pattern: route, retrieve, grade, correct, generate, reflect."

**Action (optional):** Run the full CLI demo (`python main.py`) to show routing variety (a `direct` greeting, several `kb` questions).

---

### Act 3: See the Agent's Mind in Langfuse [14:00 - 20:00]

**Screen:** Langfuse → Traces.

**Action:** Open the most recent `agentic-rag` trace. Click the **Graph** tab.

**What audience sees:** A node graph of the run — `route`, `retrieve`, `grade-relevance`, `rewrite-query`, `generate`, `reflect-groundedness` — with the retrieve→grade→rewrite→retrieve loop visible.

**Say:**
> "This is the same run, in Langfuse. Each step is a *typed* observation — `retriever` for the ClickHouse search, `evaluator` for the grading and reflection, `agent` for routing and rewriting. That typing is what makes Langfuse render this as an agent graph instead of a flat list. I can literally see where it self-corrected — there's the second retrieval."

**Action:** Click the `retrieve` (retriever) observation → show input query + returned chunk titles/distances. Click a `grade-relevance` (evaluator) observation → show its **`retrieval_relevance`** score. Click `reflect-groundedness` (evaluator) → show the **`groundedness`** score. Open the trace's **Scores**.

**Say:**
> "Two kinds of evaluator scores here. `retrieval_relevance` is logged on *each* grade step — so on this self-correcting run you can see attempt 1 scored **0** (not relevant), the agent rewrote and retried, and attempt 2 scored **1**. `groundedness` is the trace-level outcome score for the final answer. Both are real Langfuse Scores, so I can chart retrieval quality and groundedness over time, filter traces by them, and compare naive vs agentic RAG."

**Action:** Left sidebar → **Sessions** → open the agent's session.

**Say:**
> "Multi-turn conversations group into sessions, so I see cost and latency for a whole interaction, not just one call."

**Fallback:** If the Graph tab is empty, the trace may still be ingesting (worker is async) — wait ~20s and refresh, or open a slightly older `agentic-rag` trace.

---

### Act 3b: Prompt Management — the prompt lives in Langfuse [bonus]

**Setup (once):** `python scripts/seed-langfuse-prompt.py` creates `agentic-rag-generation` with v1 (baseline) and v2 (`production`, adds grounding rules + citations). The agent's `generate` node pulls it at runtime via `get_prompt(..., label="production")`.

**Screen:** Langfuse → **Prompts** → `agentic-rag-generation`.

**What audience sees:** Two versions with commit messages, the `production` label on v2, the `{{context}}`/`{{question}}` variables, and a **linked-generations** panel showing the traces that used each version.

**Say:**
> "The agent doesn't hardcode its prompt — it fetches this versioned prompt from Langfuse by the `production` label. Every generation trace links back to the exact version that produced it (you saw `promptVersion=2` on the generate step). So I can edit the prompt here, label a new version `production`, and the agent picks it up on the next run — no redeploy — and I can compare quality and cost across versions."

**Action (optional):** Edit the prompt → save as a new version → label it `production` → re-run one query → show the new version linked on the next trace. Tie back to the **Prompt Engineer** LibreChat agent (the `langfuse-prompts` MCP) which can list/read/update these prompts from chat.

---

### Act 4: Same Loop, No Code (LibreChat) [20:00 - 24:00]

**Screen:** LibreChat → select the **Agentic RAG Assistant** agent.

**Action:** Ask: *"How does RAG reduce hallucinations, and how does ClickHouse fit in?"*

**What audience sees:** The agent calling the `retrieve_kb` MCP tool (ClickHouse vector search), then answering with cited document titles.

**Say:**
> "This is the same ClickHouse-native retrieval — exposed as an MCP tool — driven by a no-code LibreChat agent instead of LangGraph. Same vector store, same observability. A developer gets the LangGraph service; an analyst gets a chat agent. Both land in Langfuse."

**Fallback:** If the tool doesn't appear, confirm `mcp-rag-retriever` is up and re-run `./scripts/seed-librechat-agents.sh`.

---

### Closing: Why This Matters [24:00 - 25:00]

**Say:**
> "Three takeaways. One — ClickHouse is your vector database *and* your analytics warehouse *and* your observability backend; one engine, native HNSW, hybrid SQL. Two — agentic RAG isn't magic, it's a measurable loop: route, grade, correct, reflect. Three — Langfuse makes that loop *visible* and *scoreable*, so you can prove retrieval quality and catch regressions. Naive RAG hopes; this system checks its work — and you can watch it do so."

**Capabilities recap:**
- ClickHouse native: `vector_similarity` HNSW, `cosineDistance`, hybrid (text index + metadata), QBit query-time precision.
- Langfuse agentic: Agent Graph, typed observations, groundedness scores, sessions, datasets/experiments.

---

## Appendix: Talking Points & Q&A

- **"Why not a dedicated vector DB?"** Fewer moving parts, hybrid SQL+vector+text in one query, joins against operational data, and the same engine already runs your observability. ClickHouse scales vector indexes across replicas for sets that exceed one node's memory.
- **"Is the vector index production-ready?"** Native since 25.8; the 26.2 release brought the `text` index and `QBit` type to GA. We pin a dedicated 26.3 container so the demo uses the latest, leaving Langfuse's bundled ClickHouse (25.8) untouched.
- **"How do you know retrieval is good?"** The grade + reflect evaluators are logged as Langfuse observations/scores; wire them to Langfuse datasets + experiments to compare naive vs agentic RAG offline (see Phase 5 / roadmap).
- **"Cost of the extra LLM calls?"** Each grade/rewrite/reflect is a small Claude call; all token usage is captured per-observation in Langfuse so the route/grade overhead is measurable against the quality gain.

## Appendix: Reset / Re-run

```bash
# Rebuild the vector index from scratch
docker compose --profile demo run --rm agentic-rag python ingest.py

# Re-run a single agent query
docker compose --profile demo run --rm agentic-rag python main.py

# Wipe the vector store entirely
docker compose --profile demo rm -sf clickhouse-vectors
docker volume rm clickhouse-llm-observability_clickhouse-vectors-data
```
