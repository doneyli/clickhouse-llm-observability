# Instructor 04-monitoring

**DRAFT.** Suggested duration: 25 minutes. Timing is a MEDIUM confidence delivery assessment; adjust after rehearsal.

Use the [learner lesson](../learner/04-monitoring.md). Start from the named checkpoint before the exercise, not from the complete root solution.

**Frame:** "The useful first monitor identifies turns worth reviewing."

**Show:** Add SDK BOOLEAN signals, send normal/blocked/PII/all-caps requests, and inspect their targets. Preview a semantic disagreement or escalation judge on the root fields.

**Learner work:** Have the room identify false positives, then choose one turn to turn into a regression example.

**Land:** "A signal points to evidence. It does not prove overall quality."

**Ask:** "Which signal would change the order in which your team reviews conversations?"

**Common pitfalls:** Do not map a message array as a plain string without checking the preview. Do not duplicate SDK score names in the platform. A provider connection in Langfuse is separate from the local key.

**Recovery:** App signal scores require no extra judge key in the platform. If platform setup stalls, teach the field mapping and complete the preview in rehearsal.

**Advance when:** the learner lesson's verification criteria are demonstrated. If you use a completed checkpoint to catch up, identify which exercise was skipped. Folder changes do not migrate Langfuse configuration or run remote seed scripts.

Source: [official docs or examined source](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk). Documented mechanics/source observations are HIGH confidence; facilitation choices are MEDIUM. See the learner lesson for feature-specific sources and Zone B limits.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
