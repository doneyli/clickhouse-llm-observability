# Brand Promo Multi-Agent — Spec

**Status:** built · **Written:** 2026-11-27, after the build, from the code and `docs/`

The demo was built in July 2026 (PR #29) before the demo standard asked for a spec, so
this is a design record written afterwards rather than a plan agreed beforehand. The
system diagram, agent fleet and model table are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); the evaluation design is in
[docs/EVALS_PLAN.md](docs/EVALS_PLAN.md). This file does not repeat them.

## 1. The question it answers

When agents delegate to agents across two frameworks, can one platform show which step
broke an answer, give the exec, ops and engineering their own view of the same fleet, and
decide whether a new version is allowed to ship? A retail or consumer-goods team with a
mixed LangGraph and CrewAI estate is the reference audience.

## 2. Audience and setting

Platform and AI engineering leads, often with an executive or an ops owner in the room,
evaluating Langfuse against an incumbent tracer or a homegrown one. A 25–30 minute
session, or 10–12 minutes for Acts 1–3. The neighbouring demos are `real-estate` (one
agent in depth, the full AI engineering loop) and `insurance-claims` (a regulated
workflow with a CI gate).

## 3. What the demo must prove

| # | Claim | How it is shown | Acceptance |
|---|---|---|---|
| 1 | A fleet is observable at operational scale | Act 1: about 50k traces across six agents, three persona dashboards | `verify_demo.py`: seeded `promo_planner_run` and every `<agent>_run` in the last 30 days; the three dashboards exist by name |
| 2 | One multi-agent, multi-framework run is one tree | Act 2: a `promo_planner_run` trace; Act 3: a live `PromoPlanner` trace with crew generations nested under the LangGraph nodes | A live `PromoPlanner` run in the last 7 days with 3+ observations; 3+ generations warns otherwise |
| 3 | A compliance violation is blocked, fail-closed | Act 3: `q2_compliance_catch` returns REJECTED | `tests/test_compliance_agent.py`: a tool error yields ERROR, never APPROVED |
| 4 | Evals catch a hallucination | Act 3: `q5_hallucination_catch`, `sku_validity` and `response_factuality` | `tests/test_evaluators.py` |
| 5 | A release gate decides what ships | Act 4: `run_experiment.py --ci` exits 1 when `certification_gate` fails | Dataset has 75+ items; 20 score configs; experiment runs exist (warn) |
| 6 | A prompt change is an experiment | Act 5: baseline against `strategy-v2` on the same items | `PROMO_SYSTEM_PROMPT_OVERRIDE` reaches the strategist's backstory |
| 7 | Humans review the ambiguous slice | Act 6: the `PromoPlanner Human Review` queue | Queue exists with items (warn) |

## 4. The fictional company and data

BrandCo, a consumer packaged goods company in North America, with Brand A, B and C, five
regions, four retail partners (MegaMart, ValueChain, ConvenienceCo, ClubWarehouse) and
placeholder regulators. All fictional. Sales, inventory, historical promos, brand
guidelines and regulatory rules are mock files under `src/data/`. Every name comes from
`demo.config.yaml` (default: `demo.config.example.yaml`), so a re-theme is a config edit.

## 5. Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full diagram. In short: one
Python process per command, no servers. `PromoPlanner` is a LangGraph graph
(`classify_intent` → `research_node` → `strategy_node` → `compliance_node` →
`compose_brief`, with an `out_of_scope` branch and a compliance-only route). The research and strategy nodes run
CrewAI crews; the compliance node runs a nested `PromotionCompliance` graph. One Langfuse
client per process carries a LangChain `CallbackHandler` for the graph and the
OpenInference CrewAI instrumentor for the crews' own LLM and tool calls
(`src/observability.py`).

## 6. What lands in Langfuse

| Object | Name | Rule |
|---|---|---|
| Trace (live) | `PromoPlanner` | One per `run_live_demo.py` run; root input is the query, root output the brief; session `live-<UTC timestamp>-<6 hex>`; tag `live_demo` |
| Trace (experiment) | `PromoPlanner` | One per dataset item; session = run name; tags `experiment` and the label; fault injection off |
| Trace (history) | `promo_planner_run`, `<agent>_run` | About 50k over 30 days from `generate_history.py`; tags `synthetic` and the agent; no model calls |
| Prompts | 12 under `promo-planner/` | Label `production`, from `scripts/seed_prompts.py`; the app reads its prompt text from `src/prompts/`, not from Langfuse |
| Score configs | 20 | `scripts/setup_score_configs.py`: 10 item-level, 8 run averages, `certification_gate`, `human_brief_review` |
| Dataset | `promo-planner-golden-v1` | 75 items; ids are content hashes, so re-seeding upserts |
| Online judges | `tool-call-correctness`, `response-factuality`, `compliance-adherence` | Trace target, 10% sampling, created in the UI from a printed checklist |
| Dashboards | `Executive - Agent Fleet`, `Ops - Agent Health`, `Engineer - PromoPlanner Deep Dive` | Created in the UI from a printed checklist |
| Annotation queue | `PromoPlanner Human Review` | Created by `seed_annotation_queue.py`; items from `run_experiment.py --queue-failures` |

## 7. Act plan

| Act | Minutes | What it shows | Depends on |
|---|---|---|---|
| Opening | 2 | No screen: locate the pain | — |
| 1 | 4 | Fleet scale, persona dashboards | History within 30 days; the three dashboards |
| 2 | 5 | One run as one tree, cost by model tier | A seeded `promo_planner_run` trace |
| 3 | 4 | Live compliance catch and hallucination catch | Anthropic key; a recent live run proves the pipeline |
| 4 | 5 | Golden dataset, experiment, certification gate, `--ci` | The dataset and score configs |
| 5 | 3 | Prompt A/B on the same yardstick | A baseline run to compare against |
| 6 | 2 | Human review queue | The queue with items |
| Close | 1 | Re-theme via `demo.config.yaml` | — |

The minutes are the script's headings. They have not been re-measured in a recorded
rehearsal since the standard was adopted.

## 8. Measurements

None recorded with their commands yet. The README's cost and time figures for experiments
(about 30 s for a 10-item deterministic run, about $10–15 and 15–20 minutes for a full
judged run) are estimates carried over from the original build. `uv run pytest -q` takes
about 20 s for 104 tests on a laptop.

## 9. Risks and open questions

- The history ages out of the 30-day window, so a demo weeks after seeding has an empty
  Act 1. `verify_demo.py` catches it.
- The dashboards and online judges depend on a manual UI step that nobody re-checks
  between demos except `verify_demo.py`.
- `seed_annotation_queue.py` reads the legacy traces endpoint that Langfuse Cloud removed
  on 2026-11-16, and `seed_dashboards.py` posts to an endpoint Cloud answers with 404.
  Moving both to current APIs (Observations v2, the unstable dashboards API) is open work.
- `verify_demo.py` needs Observations v2, which a self-hosted v3 server does not serve.

## 10. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Frameworks | LangGraph orchestrates, CrewAI runs the crews | Real agent estates are mixed; one trace across both is the point |
| D2 | Crew tracing | OpenInference CrewAI instrumentor in event-listener mode next to the LangChain handler | CrewAI's own LLM wrapper bypasses LangChain callbacks (PR #115) |
| D3 | Compliance | Deterministic keyword and regex checks, fail-closed to ERROR | Mechanical policy should not depend on a model, and a broken check must never pass |
| D4 | Fleet scale | Synthetic history through batch ingestion, no model calls | Dashboards and filters need operational volume that live runs cannot produce cheaply |
| D5 | Fault injection | About 20% of runs carry a fault; off during experiments | Realistic dashboards, honest golden-set scores |
| D6 | Model tiers | Sonnet for orchestration and research, Opus for strategy and judging | Cost by role is part of the story |
| D7 | Gate | Every dimension present must meet its threshold; thresholds are flags | The quality bar is versioned config, and `--ci` turns it into an exit code |
| D8 | Re-theming | Every domain name from `demo.config.yaml` | A new customer is an edit, not a rewrite |
| D9 | Isolation | Own `uv` env, `.env` and Langfuse project; `.env` keys win over the shell | A shell-exported key would otherwise send traces to another project silently |
