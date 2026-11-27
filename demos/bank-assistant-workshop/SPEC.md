# Bank Assistant Workshop — Spec

**Status:** draft · **Written:** 2026-11-27, after the build, from the records it links

This workshop predates the demo standard, so this spec was written afterwards. It
summarizes and links the design record rather than repeating it:
[ASSESSMENT.md](ASSESSMENT.md) (the 2026-10-11 transfer decision),
[VALIDATION.md](VALIDATION.md) (local checks and the live acceptance checklist),
[the 2026-10-12 live rehearsal](docs/2026-10-12-live-rehearsal.md) (measured Cloud
evidence) and [the 2026-10-11 adversarial review](docs/2026-10-11-adversarial-review.md).

## 1. The question it answers

How does a team get from an agent it cannot see inside to a prompt change it can defend
with evidence? Learners build the answer themselves, one module at a time, on an agent
that is realistic enough to need it: retrieval, tools in a separate process, guardrails
and two languages.

## 2. Audience and setting

Engineers who will instrument their own LLM app, on their own laptops, with a facilitator,
for about 180 minutes (a 90-minute guided variant demonstrates modules 05 to 07). The
neighbouring choice is the presenter-led [bank-assistant](../bank-assistant/) demo, which
starts fully instrumented and seeded: a capability tour, not a build.

## 3. What the workshop must prove

| # | Claim | How it is shown | Acceptance |
|---|---|---|---|
| 1 | One turn is one readable trace, across two processes | Module 02 | One `northwind-assistant` root with generations, `kb-retrieval` and MCP server spans in the same trace (rehearsal: six turns checked) |
| 2 | Personal data can be removed before export | Module 02, synthetic card `4111 1111 1111 1111` | `[REDACTED_CARD]` in every exported observation (rehearsal) |
| 3 | A prompt change is explainable and reversible | Modules 03 and 07 | Generations link the served version; a missing label falls back visibly; the label rolls back (rehearsal) |
| 4 | Signals find turns worth reviewing, and their limits show | Module 04 | Three BOOLEAN scores on the root observation; `verify_demo.py` checks their subject is the latest turn's root |
| 5 | The same app runs against reusable examples | Modules 05 and 06 | Eight ACTIVE items, every run link and applicable score present, else a non-zero exit (`helpers.validate_experiment`) |
| 6 | A release decision rests on examples, not one average | Module 07 | Baseline and candidate on one dataset version; rehearsal citation pass 5/6 vs 6/6 |

## 4. The fictional company and data

Northwind Bank, a fictional retail bank. Four synthetic customers (`C-1001` to `C-1004`),
synthetic accounts, cards and transactions in the MCP server, and a fictional help centre
in `src/northwind/knowledge.py`. The eight dataset items in `data/seed-dataset.json` are
synthetic and marked `reference_review_required: true` until an instructor reviews them.

## 5. Architecture

```
browser ── portal 127.0.0.1:8090 (FastAPI) ── LangGraph agent, in-process TF-IDF RAG
                                     │  traceparent + baggage in MCP _meta
                                     ▼
                    banking MCP server 127.0.0.1:8765/mcp (streamable HTTP)
one Langfuse client per process ──► Langfuse Cloud, one dedicated project per learner
```

`workshop.py serve` owns both processes. `scripts/checkpoint.py` builds each module's
starting state from the reference root by replacing four seam modules: `tracing.py`,
`prompt_source.py`, `monitoring.py` and `experiment_hooks.py`.

## 6. What lands in Langfuse

| Object | Name | Rule |
|---|---|---|
| Trace | `northwind-assistant` | One per turn; the agent root carries input and output. Environment `workshop` |
| Session | browser UUID | One per portal conversation; experiments use `experiment-<hex>` per item |
| Prompt | `bank-workshop/<WORKSHOP_ID>/assistant` | Labels `workshop-baseline` (module 03) and `workshop-candidate` (module 07); never `production` |
| Scores | `guardrail-blocked`, `pii-in-input`, `all-caps-signal` | BOOLEAN on the root observation of serving turns (`monitoring.py`) |
| Dataset | `bank-workshop/<WORKSHOP_ID>/regression-v1` | Eight items upserted by a deterministic id; remote extras are never deleted |
| Experiment | `Bank assistant workshop` | Trace `bank-workshop-experiment`; item scores `must-include`, `cites-expected-source`, `language-match`, `refused-safely`, `correctness` |
| Evaluator | `customer-disagreement` | Observation-level LLM judge the learner configures in module 04 |

## 7. Module plan

The minutes are the teaching estimate from the
[delivery guide](docs/preparation_and_materials/README.md); they have not been measured.
[WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md) is the run of show.

| Module | Minutes | What it shows | Depends on |
|---|---|---|---|
| 00 Setup | 15 | The untraced app answering | `.env`, model key |
| 01 Base app | 10 | The graph, tools and guardrails | — |
| 02 Tracing | 30 | One nested trace across two processes | Project keys; checked by `verify_demo.py` |
| 03 Prompt management | 20 | Label-served, linked prompt; fallback | `workshop-baseline` label |
| 04 Monitoring | 25 | Signals and a semantic monitor | A provider connection in the project |
| 05 Dataset | 20 | Reviewed examples | Instructor review of the references |
| 06 Experiments | 25 | The real app against the dataset | Modules 03 and 05 |
| 07 Evaluation | 25 | A one-instruction change, compared | `workshop-candidate` label, one dataset version |
| 08 Wrap-up | 10 | The loop applied elsewhere | — |

## 8. Measurements

Every number comes from the [2026-10-12 live rehearsal](docs/2026-10-12-live-rehearsal.md)
on Cloud US project `banking-workshop` with `claude-haiku-4-5` as agent and judge, run with
the `workshop.py experiment` commands in the README's Evaluation section. Baseline cited
the expected source on 5 of 6 policy items, candidate on 6 of 6; facts 6/6, language 8/8,
refusals 2/2 and the correctness judge 8/8 on both. Local test and validator results are
in [VALIDATION.md](VALIDATION.md). The cost of a run and the time learners need per
module have not been measured.

## 9. Risks and open questions

- The platform judge (`customer-disagreement`) exists only as a disabled draft; its preview
  is pending a provider connection in the project ([CONTINUE.md](docs/CONTINUE.md)).
- No instructor has reviewed the eight references yet, so the plain `--reviewed` upload is
  still blocked.
- The distributed archive has not been installed on a second machine.
- `agent._nest` reads private callback state, so an SDK upgrade can break trace nesting
  without failing an import.

## 10. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Rewrite on the official TypeScript app, or keep the bank's Python | Keep Python and LangGraph, match the official module order | Reuses working RAG, guardrails and MCP ([ASSESSMENT.md](ASSESSMENT.md)) |
| D2 | Keep the MCP server as a separate process | Yes, with one launcher | Cross-process tracing is central to the banking example |
| D3 | Deployment target | Langfuse Cloud only; self-hosted is an optional path | Matches the official workshop; avoids version drift |
| D4 | Starting states | Generated folders, not Git tags | No remote checkpoint tags exist; learner edits are never overwritten |
| D5 | Dataset scope | Policy questions and blocked requests only | Card blocking and disputes change state or need multi-turn confirmation |
| D6 | Prompt labels | `workshop-*` only | The helper cannot move another demo's `production` label |
| D7 | Langfuse project in `demo.yaml` | Keep `bank-workshop`; any other name, including the `banking-workshop` rehearsal project, is an explicit `DEMO_LANGFUSE_PROJECT` override in the `.env` | The code defines no project name, and existing installations keep their contract |
| D8 | What session readiness requires | Portal `/health` with tracing on for this workshop, the MCP server answering `initialize` as `northwind-core-banking`, both labels, every dataset item, a fresh turn with its signals, a prepared run per label | Every module, including the guided variant's prepared runs, depends on them; `--prep` is a scoped exception |
