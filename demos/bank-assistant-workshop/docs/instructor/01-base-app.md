# Instructor 01-base-app

**DRAFT.** Suggested duration: 10 minutes. Timing is a MEDIUM confidence delivery assessment; adjust after rehearsal.

Use the [learner lesson](../learner/01-base-app.md). Start from the named checkpoint before the exercise, not from the complete root solution.

**Frame:** "The assistant answers from policies and tools. We need to understand those steps before judging the answer."

**Show:** Wire policy, Ana account listing, cross-customer refusal, then Spanish overdraft. Tour the graph and MCP ownership checks.

**Learner work:** Ask participants to predict which requests use RAG, which use banking tools and which are blocked.

**Land:** "The visible answer is only the final step of a multi-step application."

**Ask:** "If the answer were wrong, could this UI identify the failing step?"

**Common pitfalls:** Customer selection is synthetic identity, not a login. Card blocking persists until the MCP server restarts. Never call the TF-IDF index a production vector database.

**Recovery:** Use policy questions if MCP is unavailable, but mark tool tracing as deferred until the server is fixed.

**Advance when:** the learner lesson's verification criteria are demonstrated. If you use a completed checkpoint to catch up, identify which exercise was skipped. Folder changes do not migrate Langfuse configuration or run remote seed scripts.

Source: [official docs or examined source](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/northwind/agent.py). Documented mechanics/source observations are HIGH confidence; facilitation choices are MEDIUM. See the learner lesson for feature-specific sources and Zone B limits.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
