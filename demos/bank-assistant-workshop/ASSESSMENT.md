# Banking demo workshop transfer assessment

Internal brief for Doneyli. Assessed 2026-10-11 before creating the workshop copy.

The banking core is transferable. Matching the official workshop requires moderate refactoring and substantial lesson authoring, not a replacement of the agent. Keeping the complete enterprise demo in the main learner path would make setup and pacing materially harder.

## Source snapshots

| Source | Examined revision | Evidence |
| --- | --- | --- |
| Official Langfuse workshop | `fe01d263dd1dcda268f5e461e637394d9ec87676` | [README](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/README.md), learner/instructor lessons, preparation guide, agent and helper scripts |
| Banking demo | `6af4e7b0d7f87f888860e57f71614af207be62ac` | [Banking README](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/README.md), agent/config/MCP/knowledge/masking/prompts/evaluators, scripts, labs and requirement tracker |

Source-code observations below are HIGH confidence as observations of these revisions, not claims of production readiness. Transfer effort and delivery judgments are MEDIUM confidence assessments. Langfuse facts are tagged MCP and linked to official documentation. No ClickHouse schema, sizing or configuration recommendation is made.

## What the official structure requires

The official workshop teaches 00 Setup, 01 Base App, 02 Tracing, 03 Prompt Management, 04 Monitoring, 05 Dataset, 06 Experiments, 07 Evaluation and 08 Wrap-up. Learner and instructor guides are separate. The first two checkpoints share an untraced app. Each subsequent checkpoint starts before its exercise; the finished app is the reference solution. Monitoring uses observation inputs suitable for conversations. Experiments invoke the same application logic as the web UI and attach callback scores. [Web, official source](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/AGENTS.md).

The banking demo is already a two-hour presenter-led capability demonstration with three short viewer/driver labs. It starts fully instrumented and pre-seeded. That is a different learning model from participants building the engineering loop. [Web, user source](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/DEMO_SCRIPT.md), [labs](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/LABS.md).

## Constraints and their implications

| Constraint | Observed evidence | Workshop treatment |
| --- | --- | --- |
| Python/LangGraph versus TypeScript/OpenAI | `northwind/agent.py`, official `src/server/support-agent.ts` | Keep Python and LangGraph. Match module order and learning outcomes; code snippets will differ. |
| Tracing, prompts and many scores are already present | `run_turn`, `_graph`, `_score_turn` and business scoring in `agent.py` | Add explicit lesson integration seams and real before-exercise checkpoints. A docs-only copy would not be equivalent. |
| Two app processes | MCP Streamable HTTP server on 8765 and portal on 8090 | Keep MCP because distributed banking tool traces are useful. Supply one managed launcher and readiness checks. Restart both processes after checkpoint changes. |
| Environment coupling | `config.py` overlays `.env.cloud`, defaults to self-hosted when absent, searches the main checkout for provider keys, ignores shell Langfuse keys | Workshop copy uses one local `.env`, one Cloud target, dedicated names and no parent-checkout discovery. |
| SDK minimum mismatch | `requirements.txt` allows Langfuse 4.13.1; `agent.py` calls `propagate_attributes(prompt=...)` | This parameter requires Python SDK 4.14.0+. Resolve and pin a compatible current SDK in the copy. [MCP](https://langfuse.com/docs/prompt-management/features/link-to-traces). |
| Private callback internals | `_nest` inspects `CallbackHandler._runs` and `_otel_span` | Keep isolated in the copied agent and pin dependencies. Test trace hierarchy live after upgrades. Successful import does not prove nesting. |
| Core-banking state changes | `block_card` mutates a shared in-memory card; cases and callbacks get random IDs; transactions are dated relative to server start | Use policy Q&A plus deterministic blocked requests for the core experiment set. Leave stateful actions as app tours/bonus labs. Restart MCP to reset synthetic card state. |
| Authentication is simulated | Portal chooses a synthetic customer; MCP accepts caller-provided `customer_id`. Account/transaction tools check ownership relative to that value | Label the identity boundary honestly. The model cannot pick the ID through the exposed tool schema, but the MCP service does not authenticate a real customer session. Loopback and synthetic data only. |
| Masking is partial | `masking.py` uses regex/Luhn/keyword anchors and explicitly excludes reliable detection of names/addresses | Preserve export-stage masking and output scrubbing. Teach limitations. Do not present it as complete DLP or banking compliance. [MCP](https://langfuse.com/docs/observability/sdk/advanced-features). |
| Evaluation meaning | Source recall combines retrieved and cited IDs; regex language/register/upsell checks are proxies; judge scores vary | Keep separate retrieval, answer citation and factual scores. Teach false positives and human review. A high judge score does not demonstrate safe tool execution. |
| Historical evidence is project-specific | Requirement tracker links to the original Cloud project and CI runs | Preserve in the source snapshot; do not use those URLs as validation of the new workshop. |
| Enterprise extras add independent dependencies | Voice APIs, n8n, Jaeger/APM, self-hosted EE and governance scripts | Preserve the complete source as a bonus/reference copy. Exclude it from the core setup requirements. Jaeger is a stand-in, not a validated Dynatrace tenant. |
| Deployment feature drift | Official workshop explicitly prefers Cloud; banking demo includes self-hosted EE | Core uses Cloud. Self-hosted is an advanced path after checking server version and supported features. [Web, official guide](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/docs/preparation_and_materials/README.md). |

## Approaches and tradeoffs

| Approach | Pros | Cons | Assessment |
| --- | --- | --- | --- |
| Copy all existing runtime and add nine lesson documents | Least code change; every enterprise capability remains immediately available | Learners start with the answer; setup remains multi-stack; no clean first-trace exercise | Good for a presenter-only seminar, poor fit for the requested hands-on structure. |
| Copy banking core into a standalone workshop; preserve full demo as optional reference | Retains LangGraph, RAG, guardrails and MCP; clean before/after exercises; smaller setup | Needs a workshop config, lightweight portal, checkpoint builder and new lesson materials | Selected for this copy. MEDIUM confidence delivery assessment, based on the two source trees. |
| Rewrite the bank scenario on the official TypeScript app | Closest code-level alignment to the official workshop; fewer framework concepts | Replaces working Python/LangGraph/MCP behavior and is a major rewrite | Choose only if a TypeScript audience outweighs reuse. |

The principal's call is whether distributed MCP tracing belongs in the live session or a bonus. This version retains it because it is central to the banking example, while removing Docker from the core. For a mixed beginner audience, allow more time than the original presenter's two-hour run sheet.

## Implementation scope

Create a new standalone directory, copy the full source demo into `original-demo/`, and reuse its Python banking modules in `src/northwind/`. Add 9 learner lessons, 9 instructor guides, delivery preparation, a smaller portal, prompt/dataset/experiment helpers, a small synthetic regression dataset, explicit source attribution and reproducible filesystem checkpoints. No original branch, tags or files need modification. The filesystem checkpoints will be equivalent exercise starting states, not claims that remote Git checkpoint tags exist.

Live prompts, datasets, evaluator setup and model calls are participant/presenter actions described by the workshop. This build does not mutate a Langfuse project. The first publication must use workshop-specific names, so it does not move the existing demo's production labels.

## Research gaps and validation boundary

Langfuse Docs MCP was connected. Five parallel searches returned 7, 3, 6, 6 and 5 documents respectively: workshop scope, LangGraph integration, experiment data model, configuration and programmatic dataset/prompt workflows. Follow-up full pages verified callback instrumentation, propagation, prompt linkage, score targets, export masking and experiment signatures. [MCP integration](https://langfuse.com/integrations/frameworks/langchain), [MCP SDK experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk), [MCP SDK v4 migration](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4), [MCP scores](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk).

Local validation can prove checkpoint assembly, dependency compatibility, app/tool behavior with fake models, dataset shape, guardrail behavior and evaluator failure handling. It cannot prove Cloud ingestion, displayed trace nesting, current UI mappings, prompt resolution in a target project or live model quality. Rehearsal must run those paths with workshop keys and inspect the resulting observations and experiment scores. No live-key acceptance test is claimed by this assessment.

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
