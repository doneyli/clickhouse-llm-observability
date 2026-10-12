# Astra adversarial review, 2026-10-11

Internal review for the workshop owner. Review model: `gpt-6-astra`. Scope: workshop source, nine checkpoint boundaries, preserved source snapshot, pinned SDK behavior and distributed archive. No credentials, network/model calls or live mutations were used.

The first pass confirmed four P2 defects. No P0/P1 blocker was established. All four corrections passed independent Astra verification, including all 42 local tests. No remaining confirmed issue was found in the reviewed source changes.

| Finding | Reproduction and impact | Correction |
| --- | --- | --- |
| Missing dataset-run linkage accepted as success. | The pinned SDK catches dataset-run-item API failures and can return completed answers/scores with missing run IDs. The comparison then has no linked dataset run. | Require a dataset run ID and matching run IDs on every completed item. Reject incomplete runs. |
| Missing deterministic scores accepted as success. | A malformed `must_include=[35]` causes an evaluator exception. The SDK omits that score; the old helper checked only correctness, and checked no scores with `--no-judge`. | Validate reference metadata before tasks. Require each applicable deterministic score and the judge score when enabled. Missing, invalid and ambiguous scores fail; a numeric zero is valid. |
| Archived items executed. | The pinned `DatasetClient.run_experiment` forwards every fetched item. ARCHIVED items entered tasks and averages, while the old completion check counted only ACTIVE items. | Select ACTIVE items before running. Use exactly that snapshot for completion checks and refuse an empty active dataset. |
| Serving traces falsely reported the production label. | Actual in-memory SDK export linked a managed prompt version but reported `prompt_label=production` when the environment selected `workshop-candidate`. Version-pinned experiments had the same defect. | Resolve the effective label once. Pass the experiment label with its frozen version and record whether resolution uses a label or version. |

Code evidence: [experiment helper](../scripts/helpers.py), [prompt resolution](../src/northwind/prompts.py), [agent metadata](../src/northwind/agent.py), [experiment task](../src/northwind/experiment_hooks.py). Regression evidence: [behavioral tests](../tests/test_workshop.py), [real SDK export test](../tests/test_trace_export.py). Findings are HIGH confidence observed source-code/runtime behavior, not Cloud acceptance results.

The audit additionally identified stale copied comments claiming authenticated identity and APM behavior. Those comments were corrected in the workshop source. The immutable `original-demo/` snapshot remains unchanged.

The first pass verified all 678 compared non-env archive files matched the then-current workshop tree, all 79 compared non-env original-demo files matched the source snapshot, and no private settings, runtime or working-exercise files were packaged. The package validator separately checked all 80 original source hashes, including the public example settings file. All nine before-exercise boundaries were coherent. Real in-memory export confirmed managed prompt name/version linkage.

The second pass additionally exercised the genuine pinned SDK and DatasetClient with network-free mocks. Only ACTIVE items executed; legitimately inapplicable evaluators could skip; missing per-item run links and evaluator failures failed the command with and without judges. The actual workshop task, real LangChain callbacks and a scripted model fetched exact prompt version 7 and exported `workshop-candidate` with `prompt_resolution=version`, despite an ambient baseline label. The checkpoint starter signature matched the helper invocation.

Regenerate checkpoints and rebuild the archive from the corrected source before release. Live Cloud ingestion, current platform evaluator UI mapping, a real baseline/candidate model run and installation on a clean machine remain acceptance steps in [VALIDATION.md](../VALIDATION.md).

Review limits: this was an adversarial code and local-runtime review. It cannot establish production banking security, complete PII protection or statistical improvement from an eight-item dataset.

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
