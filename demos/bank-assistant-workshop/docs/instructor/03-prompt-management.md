# Instructor 03-prompt-management

**DRAFT.** Suggested duration: 20 minutes. Timing is a MEDIUM confidence delivery assessment; adjust after rehearsal.

Use the [learner lesson](../learner/03-prompt-management.md). Start from the named checkpoint before the exercise, not from the complete root solution.

**Frame:** "We need to know which instruction version produced each answer."

**Show:** Publish the workshop-baseline prompt; replace prompt_source.resolve; inspect generation linkage. Demonstrate an absent label and visible local fallback.

**Learner work:** Find the compiled bank name, label and prompt version. Restore the baseline before experiments.

**Land:** "A linked version makes prompt changes explainable and reversible."

**Ask:** "Would a run on the local fallback be a valid test of the published candidate?"

**Common pitfalls:** Re-publishing creates another version. The helper uses workshop-specific names, not northwind-assistant-system. Cache TTL zero is a teaching choice.

**Recovery:** Generate 04 for completed prompt retrieval. If Cloud prompt publication fails, demonstrate fallback and repair it before module 06.

**Advance when:** the learner lesson's verification criteria are demonstrated. If you use a completed checkpoint to catch up, identify which exercise was skipped. Folder changes do not migrate Langfuse configuration or run remote seed scripts.

Source: [official docs or examined source](https://langfuse.com/docs/prompt-management/features/link-to-traces). Documented mechanics/source observations are HIGH confidence; facilitation choices are MEDIUM. See the learner lesson for feature-specific sources and Zone B limits.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
