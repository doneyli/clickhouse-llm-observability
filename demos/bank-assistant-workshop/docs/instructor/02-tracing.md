# Instructor 02-tracing

**DRAFT.** Suggested duration: 30 minutes. Timing is a MEDIUM confidence delivery assessment; adjust after rehearsal.

Use the [learner lesson](../learner/02-tracing.md). Start from the named checkpoint before the exercise, not from the complete root solution.

**Frame:** "One banking turn should tell us what the model knew, what it called and where time went."

**Show:** Complete ENABLED, callbacks and attribute scope in tracing.py. Restart both services, run authentication preflight, send RAG and account requests. Open one trace.

**Learner work:** Identify root, generation, retriever, guardrail and MCP server. Compare one blocked request with one normal request.

**Land:** "An answer is debugged by reading the steps that produced it."

**Ask:** "Which observation would prove that an account lookup reached the banking service?"

**Common pitfalls:** Only restarting the portal leaves the MCP server untraced. Wrong project keys split exports. The private callback nesting helper needs live inspection after dependency upgrades.

**Recovery:** Stop both services and generate 03 in a new folder for the completed tracing code. Retain the learner's 02 folder.

**Advance when:** the learner lesson's verification criteria are demonstrated. If you use a completed checkpoint to catch up, identify which exercise was skipped. Folder changes do not migrate Langfuse configuration or run remote seed scripts.

Source: [official docs or examined source](https://langfuse.com/integrations/frameworks/langchain). Documented mechanics/source observations are HIGH confidence; facilitation choices are MEDIUM. See the learner lesson for feature-specific sources and Zone B limits.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
