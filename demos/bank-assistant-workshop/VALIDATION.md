# Workshop validation

Validation report for the separate workshop copy. Live rehearsal is a distinct acceptance step.

## Local checks

Run with Python 3.12 and the pinned requirements:

```bash
.venv/bin/python scripts/validate.py
.venv/bin/python -m unittest discover -s tests -t . -v
```

The validator checks the preserved source hashes, syntax, learner/instructor pairs, local links, dataset expectations and all nine generated starting states. Behavioral tests cover copied guardrails/masking/business logic, the real LangGraph with fake model/MCP responses, blocked turns without LLM calls, signal targets, invalid judge responses, experiment fallback rejection, experiment completion/linkage/score coverage, effective prompt labels and portal input validation. They use no live credentials.

Local results are recorded after the build below. An offline pass does not validate exported trace shape or live model quality.

## Live rehearsal acceptance

The instructor must perform these in a dedicated workshop project:

- [ ] Start 00 and verify an untraced policy answer. A model key being present is insufficient.
- [ ] Complete 02 and run authentication preflight. Confirm one root per request, model/token data, RAG sources, session grouping and MCP server children in the same trace.
- [ ] Inspect synthetic card masking at export and the output guardrail. A regex test is not a complete privacy validation.
- [ ] Publish baseline, complete 03 and confirm the linked prompt version on generations. Demonstrate and restore a missing-label fallback.
- [ ] Complete 04 and inspect all three signal targets. Preview the platform judge on the structured root fields and verify the intended filter.
- [ ] Review all eight references, clear the review markers, seed the dataset and record the dataset version.
- [ ] Complete 06 and run baseline/candidate with identical model, judge and dataset version. Check ACTIVE item completion, per-item dataset-run linkage and applicable score coverage, including `--no-judge`.
- [ ] Inspect both improved and unimproved items. Record actual run/trace links. No expected improvement percentage is guaranteed.
- [ ] Stop and restart both processes to reset a synthetic blocked card. Rehearse returning the serving label to baseline.
- [ ] Test the distributed archive on another machine before a group session.

Source: [MCP tracing best practices](https://langfuse.com/docs/observability/best-practices), [MCP SDK experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk), [examined source](https://github.com/doneyli/clickhouse-llm-observability/tree/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant). Rehearsal requirements are a MEDIUM confidence delivery assessment; documented SDK mechanics are HIGH confidence.

## Recorded build results

- Python 3.12.14; resolved and installed pinned requirements, including Langfuse 4.17.0, LangGraph 1.2.14 and MCP 1.30.0.
- 42 unittest tests passed after the Astra adversarial audit. One uses the actual SDK and LangChain callbacks with an in-memory span exporter: a single nested trace contains agent/generation/retriever/guardrail types, session propagation, masked synthetic card data, the effective workshop label and linked managed prompt version. No network exporter or live project was used.
- Ten additional regression tests cover the audit's four P2 defects: missing dataset-run links, missing applicable callback scores, archived-item execution and incorrect prompt label metadata. Malformed references and an empty active dataset stop before task execution. A zero score remains a valid completed evaluation.
- Astra independently reran all 42 tests and exercised the genuine pinned SDK runner with mocked dataset APIs. ACTIVE-only execution, score applicability and rejection of run-link/evaluator failures passed with and without judges. The actual experiment task and real callbacks exported the explicitly selected candidate label and exact prompt version despite an ambient baseline label. See [the audit report](docs/2026-10-11-adversarial-review.md).
- All 80 original source snapshot hashes match. The original repository and its files were not edited.
- All nine starting states assembled and passed offline preflight; 00/01 have the same untraced integration modules, 02 starts untraced, and 06 has the unfinished task.
- Learner/instructor pairs, relative documentation links, Python syntax and the eight-item dataset schema/source IDs passed validation.
- The untraced setup checkpoint's portal and MCP process started on alternate loopback ports. Portal health/static content, real MCP handshake, all six tools, the own-account response and cross-account access denial passed a loopback smoke test. The portal layout was visually inspected in the in-app browser. No model response was generated during this UI check.
- No live keys, live project mutation, Cloud ingestion test, platform evaluator activation or baseline/candidate model experiment was performed.

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
