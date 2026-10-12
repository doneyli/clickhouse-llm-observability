# Live workshop rehearsal, 2026-10-12

This rehearsal used the separate workshop copy and real model calls. The existing banking demo and its Cloud project were preserved. All results below are observed rehearsal evidence, not a production readiness assessment.

## Target and authentication

- [Cloud US project: banking-workshop](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr), in `clickhouse` (`cmlfwuz9w01c7ad07rxxb27x4`).
- CLI 1.2.4, Python 3.12.12, and the pinned workshop requirements, including Langfuse 4.17.0.
- The authorized format adapter read the existing org settings and produced canonical private CLI settings. Org authentication succeeded with `api organizations list-projects`; `api projects list` requires project keys and rejects org keys. The org list was checked before project creation.
- Newly created project keys were verified against `api projects list`, which returned only the dedicated project and the expected organization. Private settings are ignored and mode 0600. Inherited Langfuse variables were cleared for CLI calls, and the repository CLI wrapper was used throughout.
- The existing Anthropic provider key was loaded privately for local calls. The agent and experiment judge both selected `claude-haiku-4-5`; exported generations identified `claude-haiku-4-5-20251001`. The baseline and candidate comparison used the same model, judge, dataset snapshot, and application code.

## Serving and trace acceptance

Checkpoint 00 produced a real untraced wire-policy answer containing USD 35 and 3:00 PM, with no trace or observation ID. Its services were stopped before starting the reference solution.

Six traced turns were fetched through the current observations API with I/O, metadata, model, usage, prompt and trace-context fields. Each had exactly one logical root, named `northwind-assistant`, with structured request/response fields. The six turns share session `banking-workshop-rehearsal-20261012`.

| Turn | Final trace | Exported evidence |
| --- | --- | --- |
| Wire policy RAG | [Trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/ea9d2c347b822db24ef8edc32c50118a) | 16 observations; two generations; retriever query, article bodies, IDs, sources, effective dates and scores. |
| Own accounts via MCP | [Trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/fbc22bd349326413fa51995450807f1c) | 18 observations; two generations; server child under the MCP client and core-banking child under the server, all in the same trace. |
| Injection refusal | [Trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/80f6ed833cd823491f696ecd3880c012) | Five observations; no generation or banking tool execution; blocked signal true. |
| Synthetic card data | [Trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/e2c61413debefcb620e76e43bf62b3f1) | Card number absent from every exported observation; redaction marker present; output guardrail records its final safe answer and warning-added metadata. |
| Capitals without disagreement | [Trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/88371a0978bbd3204bb3e73c38319067) | Capitals signal true on a normal balance question; demonstrates the signal's limits. |
| Complaint with prior messages | [Trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/7686655162239b72df076977c0c6d1d6) | 38 observations; four generations; prior conversation and tool evidence on the root. |

All serving generations had model and positive input/output token counts and linked application prompt version 1. MCP server observations had the same session and recorded `traceparent + baggage in MCP _meta` linkage. All three BOOLEAN application signals were fetched and matched each turn's root observation ID and trace ID, including expected false values. Score verification used explicit observation/trace subjects; the session-filtered scores query returned no rows for these observation scores.

The audit used the freshly fetched [official tracing best practices](https://langfuse.com/docs/observability/best-practices). It found and corrected missing core-banking I/O, missing article bodies on retrieval observations, and warning metadata updated after the guardrail span ended. The rerun verified those corrections in Cloud. Experiment judge observations now also record their reference inputs and parsed score/reason outputs. The in-memory export regression test verifies the transformed guardrail answer and warning metadata.

## Prompts, fallback and rollback

Prompt `bank-workshop/banking-workshop/assistant` was published as baseline version 1 and candidate version 2. Candidate adds citation instructions to the same baseline. No production/staging label was changed.

- `workshop-serving` selected candidate version 2: [trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/c05ddd1ab65a7ce43c135992b6c9f8ac).
- A deliberately absent label returned a real answer from the local fallback, with no managed prompt version: [trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/ad0d97312b5ee161f1cb0e81cd237c9f).
- `workshop-serving` was restored to baseline version 1 and another real turn verified it: [trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/c8215884a44e07234284286ae251bb0a).

The normal app settings continue to select `workshop-baseline`. The helper rejects fallback prompt comparisons, as verified by the behavioral tests.

## Dataset and measured experiments

The six policy references were checked against KB-202, KB-103, KB-102 and KB-302, including the two Spanish references. Both refusal references were checked against the copied guardrail behavior. Human instructor review remains pending on all eight examples. The owner's explicit authorization permitted rehearsal upload with `dataset --allow-draft`, which preserves `reference_review_required=true` and is restricted to synthetic items. The ordinary reviewed-upload path still requires cleared review markers.

[Dataset](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/datasets/cmv52ibyo00joad0eypi3zqby) snapshot: `2026-10-12T09:48:48.619694+00:00`. All three runs used this timestamp. A separate versioned dataset-item read confirmed exactly eight ACTIVE IDs and matched every experiment item's input and expected output against that historical snapshot.

| Run | Exact prompt | Required item scores | Citation pass | Other measured results |
| --- | --- | --- | --- | --- |
| [Baseline](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/datasets/cmv52ibyo00joad0eypi3zqby/runs/5de42269-f24f-47e3-a724-dcd0f2619e43) | v1, `workshop-baseline` | 30/30 present | 5/6 policy items | Facts 6/6, language 8/8, safe refusal 2/2, correctness judge 8/8. |
| [Candidate](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/datasets/cmv52ibyo00joad0eypi3zqby/runs/37993722-01d7-483d-a9b4-8dbbe1e790d8) | v2, `workshop-candidate` | 30/30 present | 6/6 policy items | Facts 6/6, language 8/8, safe refusal 2/2, correctness judge 8/8. |
| [Candidate without judge](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/datasets/cmv52ibyo00joad0eypi3zqby/runs/a3390379-90bf-4f22-8133-c815b910f4e4) | v2, `workshop-candidate` | 22/22 present | 6/6 policy items | Facts 6/6, language 8/8, safe refusal 2/2; no correctness score. |

Each run completed all eight ACTIVE items. Current experiments/experiment-items API exports confirmed all 24 run/item links, exact prompt versions, and every applicable score attached to the canonical item observation. Archived-item exclusion remains covered by the local regression tests; this live dataset contained only ACTIVE items.

Item `g01` improved: the [baseline trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/2721e14e906b71ea5e0202ef1c92b5ed) gave the correct wire facts without a KB citation; the [candidate trace](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/3070e93532b4172476ed9f3ed62e3e9f) added `[KB-202]`. The other seven items retained passing scores. A single eight-item comparison does not establish a statistically reliable improvement rate.

The dataset timestamp is recorded in run metadata and passed through the SDK's versioned dataset fetch and dataset-run linkage. The current experiment-items export reports `experimentItemVersion=null` with this pinned SDK; the verification uses the independently fetched historical snapshot rather than claiming that field was populated. [Official versioned datasets](https://langfuse.com/docs/evaluation/experiments/datasets), [SDK experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk), and [prompt linkage](https://langfuse.com/docs/prompt-management/features/link-to-traces) were fetched before the run.

## Platform monitor preparation

Cloud accepted a disabled `customer-disagreement` evaluator (`cmv52tbpn00m0ad0ei8u55gjp`) and disabled `banking-workshop-preview` rule (`cmv52tcec00n4ad0jf2exn97g`). Its BOOLEAN rubric distinguishes disagreement/escalation from capital letters alone. The saved mappings are:

| Variable | Observation source | JSONPath |
| --- | --- | --- |
| `messages` | input | `$.messages` |
| `question` | input | `$.question` |
| `answer` | output | `$.answer` |

The rule requires root name `northwind-assistant`, type AGENT, environment `workshop`, `isRootObservation=true`, and exact session `banking-workshop-preview-20261012`. These payloads were validated against the current official OpenAPI schema and accepted by Cloud. No broad scoring rule was enabled and no provider credential was uploaded to the platform.

Actual platform execution/preview remains pending. Automatic approval review rejected uploading the existing Anthropic key into the new project's platform connection because this specific credential transfer was not authorized. The owner was asked to authorize that connection and a two-turn, session-scoped preview. The local experiment judge is a separate, successfully executed check; it does not establish platform monitor execution. [Official observation-level judge mappings](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge).

## State reset and remaining delivery checks

The assistant [blocked fictional card 4417](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/f1815c1ca83ce9892de292ff2e309718) and a [second MCP-backed answer](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/d8d5dcb55b483702ad0f600dc615ad81) reported it blocked. Both portal and MCP processes were stopped and restarted before [verifying it active again](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr/traces/ec55ea52aa16b935a0f7964a3babef99). The serving-label rollback above also passed.

Remaining human/second-machine checks: instructor review of all eight synthetic references, calibration of the platform rubric, actual platform preview after the provider connection is authorized, and installation of the distributed archive on another machine. Local tests, generated checkpoint validation and archive verification are recorded in [VALIDATION.md](../VALIDATION.md).

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
