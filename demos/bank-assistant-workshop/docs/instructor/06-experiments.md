# Instructor 06-experiments

**DRAFT.** Suggested duration: 25 minutes. Timing is a MEDIUM confidence delivery assessment; adjust after rehearsal.

Use the [learner lesson](../learner/06-experiments.md). Start from the named checkpoint before the exercise, not from the complete root solution.

**Frame:** "The test must run the same agent as the app, including its tools."

**Show:** Complete the task and evaluator module. Run the baseline on the hosted dataset with concurrency one. Open an item trace and its callback scores.

**Learner work:** Have learners explain one keyword result, one citation result and one judge rationale. Identify an example where a keyword-only score would mislead.

**Land:** "An experiment joins reusable examples, the actual application and explicit measurements."

**Ask:** "Which failure would be hidden by a prompt-only test?"

**Common pitfalls:** Missing prompt labels are hard failures. Judge parser/provider failures, missing applicable deterministic scores and missing dataset-run links are incomplete evidence. Only ACTIVE items execute. Model calls cost money; --no-judge still requires deterministic score coverage and dataset-run linkage. A recorded zero score is a completed evaluation.

**Recovery:** Generate 07 for the completed task. If models cannot be called, show an instructor rehearsal run and state the participants have not run a live experiment.

**Advance when:** the learner lesson's verification criteria are demonstrated. If you use a completed checkpoint to catch up, identify which exercise was skipped. Folder changes do not migrate Langfuse configuration or run remote seed scripts.

Source: [official docs or examined source](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk). Documented mechanics/source observations are HIGH confidence; facilitation choices are MEDIUM. See the learner lesson for feature-specific sources and Zone B limits.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
