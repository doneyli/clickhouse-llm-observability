**DRAFT teaching material.**

# 05 Dataset

Start `05-dataset`. Monitoring is complete.

**Goal:** turn the assistant's product scope and observed failures into reusable regression examples.

Open `data/seed-dataset.json`. There are eight synthetic examples: six policy questions, including two Spanish questions, and two blocked requests. Every item separates:

| Field | Responsibility |
| --- | --- |
| `input` | Question and simulated customer ID. |
| `expected_output` | Reference facts or expected refusal behavior. |
| `metadata` | Expected sources, must-include facts, language, category and review status. |

Review every reference against `src/northwind/knowledge.py` and the guardrail behavior from module 01. These are fictional Northwind policies, not current financial guidance. Do not upload until a person has checked the references. Set `reference_review_required` to false after reviewing the item, then run:

```bash
../../.venv/bin/python workshop.py --env-file ../../.env dataset --reviewed
```

The helper upserts stable IDs into `bank-workshop/<WORKSHOP_ID>/regression-v1`. Repeating does not duplicate IDs. It does not delete remote items absent from the local file. To change the scenario materially, use a new dataset name/version rather than mixing incomparable tests.

For an instructor rehearsal explicitly authorized by the dataset owner, `dataset --allow-draft` uploads only synthetic examples and preserves the pending human-review markers. It does not replace the participant review exercise or make draft references production ground truth. The default command still refuses an unreviewed upload.

Choose one monitored failure and draft an additional item. Keep a minimal safe synthetic reproduction. Review the expected behavior before uploading it. Core examples avoid blocking cards or opening disputes because those operations change state or depend on multi-turn confirmation.

**Verify:** input/reference/metadata are separate in Langfuse; exactly the intended item IDs exist in your fresh workshop dataset; Spanish examples have Spanish references; blocked requests expect refusal.

Before comparing two experiments, record the dataset version timestamp and use the same version for both. A changed test set changes the meaning of the comparison.

**Assessment: MEDIUM, Zone B.** Eight examples are useful for teaching and finding specific regressions. They cannot establish coverage, statistical significance or banking release readiness. At projected growth, a real evaluation set needs representative languages, intents and failure modes reviewed by domain owners.

Source: [MCP datasets](https://langfuse.com/docs/evaluation/experiments/datasets), [MCP experiment data model](https://langfuse.com/docs/evaluation/experiments/data-model), [banking reference examples](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/scripts/seed_datasets.py). Dataset mechanics: HIGH confidence, level 1.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
