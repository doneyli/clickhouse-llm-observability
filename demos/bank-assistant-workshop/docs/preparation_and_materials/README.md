# Workshop delivery guide

**DRAFT.** This version follows the official nine-module path on the bank's Python/LangGraph app. Plan **180 minutes** for a hands-on group. That timing is a MEDIUM confidence teaching assessment, not a measured completion time. The original bank's 120-minute presenter run remains in `original-demo/DEMO_SCRIPT.md`.

| Module | Minutes | Group check-in |
| --- | --- | --- |
| 00-setup | 15 | The workshop has one setup file and a dedicated project. No trace is expected yet. |
| 01-base-app | 10 | The visible answer is only the final step of a multi-step application. |
| 02-tracing | 30 | An answer is debugged by reading the steps that produced it. |
| 03-prompt-management | 20 | A linked version makes prompt changes explainable and reversible. |
| 04-monitoring | 25 | A signal points to evidence. It does not prove overall quality. |
| 05-dataset | 20 | Reviewed expected behavior is the foundation of an interpretable comparison. |
| 06-experiments | 25 | An experiment joins reusable examples, the actual application and explicit measurements. |
| 07-evaluation | 25 | The decision is based on examples and constraints, not a single average. |
| 08-wrap-up | 10 | Observe behavior, define expected behavior, test a change, then observe the served result. |

**Before the session:**

1. Use a dedicated Cloud project per attendee, or an instructor project for demonstration. Learners need write access for prompts, datasets and evaluator setup; Viewer access supports trace exploration only.
2. Verify Python 3.12, pinned dependencies, available provider models and two free loopback ports. Rehearse the full live acceptance checklist in the root `VALIDATION.md`.
3. Review all synthetic dataset references. Do not publish the draft markers as if a domain owner approved them.
4. Publish baseline/candidate prompts, seed the reviewed dataset and prepare actual experiment links in the instructor's project. Capture trace screenshots after checking masking. Historical original-demo links are not the new workshop's evidence.
5. Review current Langfuse evaluator field mappings and a working provider connection. The workshop intentionally supplies no copied screenshots that might show stale UI or customer data.
6. Generate all nine checkpoints and distribute the release archive. Keep participant credentials out of the archive. Each person configures one private `.env`.
7. Set a workshop spend limit and plan the number of agent/judge calls. Eight items per run plus model tool iterations can create more than eight calls. The `--no-judge` path reduces calls while retaining deterministic checks.
8. Keep a second facilitator available for setup and Python problems. Review the [official group delivery guidance](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/docs/preparation_and_materials/README.md).

The core UI palette uses navy for bank identity, blue for actions and light blue for conversation context. It focuses on the assistant and trace evidence; optional presenter, voice and enterprise screens remain in the full source copy.

**Delivery options (MEDIUM, assessment):**

| Path | Pros | Cons |
| --- | --- | --- |
| Full 180-minute self-paced workshop | Learners complete the entire loop and inspect their own evidence. | Requires working keys, Python familiarity and active setup help. |
| 90-minute guided session | Setup beforehand; demonstrate the dataset/experiment steps with prepared live evidence. | Fewer independent coding exercises. Tell the room which steps were demonstrated. |
| Original 120-minute enterprise demo | Broad capability and governance tour with the existing presenter console. | Different goals; it does not teach progressive instrumentation. |

Use short group check-ins after tracing, monitoring and experiment comparison. Each check-in asks a learner to explain one concrete observation or failed case. The [slide outline](slides.md) supports the same checkpoints.

**Optional extensions:**

| Extension | Source location | Preparation |
| --- | --- | --- |
| Voice and audio traces | `original-demo/northwind/voice.py`, voice scripts | Additional model services, audio inputs, privacy review. |
| n8n | `original-demo/n8n/` | Separate n8n setup and custom tracing pattern. |
| APM/Dynatrace | `original-demo/docs/DYNATRACE.md` | Collector/export settings; Jaeger does not validate a Dynatrace tenant. |
| Calibration and annotation | Original judge and queue scripts | Labeled human examples, project permissions, rubric review. |
| Dispute lookback and retrieval cost | `original-demo/docs/arcs/` | Reset synthetic state and rehearse multi-turn confirmation. |
| Self-hosted/governance | Original architecture/security docs | Server version, edition and deployment review. Not validated by the core build. |

Source: [official delivery guide](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/docs/preparation_and_materials/README.md), [bank capabilities](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/README.md). HIGH confidence source observations; delivery tradeoffs are MEDIUM, Zone B review.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
