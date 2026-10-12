# Banking workshop slide outline

**DRAFT.** Use one slide per numbered topic. Suggested speaker prompts, not a claim that these timings have been rehearsed.

1. **What did the assistant do?** Show the wire-fee question. Ask what evidence would explain a wrong answer.
2. **The bank app.** LangGraph orchestrates the model, TF-IDF policy retrieval, rules guardrails and a separate synthetic banking MCP server.
3. **One turn, one trace.** Identify agent, generations, retrieval, tools and server children on a real rehearsal trace.
4. **Which prompt was served?** Show compiled bank name, linked version and fallback behavior.
5. **Which turns need review?** Compare normal, blocked and capitals signals. Distinguish a signal from a quality conclusion.
6. **What should happen?** Show one English policy, one Spanish policy and one refusal reference. Identify the domain owner.
7. **Test the actual app.** Dataset tasks invoke the same agent as the portal. Show callback scores and a tool trace.
8. **Change one instruction.** Citation-only candidate. Same dataset version, model and judge. Compare item evidence.
9. **Choose and reverse.** State remaining gaps, serve the chosen workshop label, verify it, then rehearse return to baseline.
10. **Apply the loop.** Each participant names one application, one signal and three reviewed reference examples.

Optional closing appendix: voice, n8n, APM, calibration and self-hosted governance require separate preparation. All bank data is synthetic; the app's identity and masking are teaching controls.

Source: [official workshop](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/README.md), [examined banking app](https://github.com/doneyli/clickhouse-llm-observability/tree/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant). Source observations are HIGH confidence. Lesson framing is a MEDIUM confidence assessment.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
