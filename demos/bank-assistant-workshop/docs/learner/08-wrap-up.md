**DRAFT teaching material.**

# 08 Wrap-up

Start `08-wrap-up`, or keep your completed 07 folder.

**Goal:** explain the loop using one bank example.

Present a policy question, its trace, the prompt version, a monitoring signal, the dataset item and the experiment comparison. Explain each connection:

1. The trace records the model, retrieval, tool and guardrail steps.
2. Prompt management identifies the instructions actually used.
3. Monitoring identifies examples worth reviewing.
4. Dataset references define expected behavior.
5. Experiments run the same app against those examples.
6. Evaluation compares changes and exposes uncertainty.
7. The next served version returns to monitoring.

**Verify learning:** can you explain which tool failed, distinguish retrieved evidence from an answer citation, identify a fallback prompt, and distinguish a judge failure from an incorrect answer?

Write down one use case to apply this to, one observable review signal and three reference examples. Identify the person who can review those references. Keep the scope small enough to inspect.

Optional bank-specific follow-ons are in the package root's `original-demo/`: voice, n8n, APM correlation, annotation and judge calibration, bilingual register, disputes/lookback and retrieval cost arcs. They have separate requirements and historical evidence. Self-hosted and enterprise governance require a deployment/version/edition review; the core workshop has not validated them in your environment.

Stop the app with Ctrl-C. Retain your experiment links and version metadata. Follow your organization's process for retiring workshop keys. The workshop performs no automatic deletion of project data.

Source: [official workshop loop](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/README.md), [bank demo optional capabilities](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/README.md). HIGH confidence source observations. The transfer exercise is a MEDIUM confidence teaching assessment.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
