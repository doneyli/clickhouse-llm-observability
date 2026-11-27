# Evaluator accuracy and calibration audit

The answer-key scorers agree with all 69 eligible historical item scores reviewed. That
checks scoring consistency against the stored reference answers. It does not establish
that the release thresholds are calibrated for the current application. Their status is
**provisional** in `cicd/thresholds.json`; their numeric values have not been retuned.

This demo has deterministic evaluators, not an LLM judge. The main distinction is between
an answer matching the reference, a selected metric reproducing, and a summary making
only supported claims. These are separate properties.

## Evidence reviewed

Read-only exports from the demo's Langfuse project:

- Dataset `scenario-explorer-questions`, version `2026-11-27T03:25:48.869Z`: 15 active
  items. All question texts, answer-key sets, and reference SQL match the repository.
- Nine experiment records and 73 item records. Four items belong to a different
  pipeline-check dataset and were excluded. The audit uses seven experiments / 69 items.
- All 69 item-level `answer_correct` and all 69 `answer_key_match` values agree with
  recomputation: 138 score comparisons, zero disagreements. Stored expected outputs
  also match the current dataset's expected outputs on all 69 items.
- An independent typed exact-match comparator agrees on all 69 historical outputs.
  Fifteen known-positive answers and 40 single-key counterfactuals pass their expected
  scoring outcomes (55 controls). These controls validate discrimination rules; they
  are constructed cases, not held-out estimates of release-gate reliability.
- All 15 stored expected answers use categorical codes and/or integers. The generic
  1% floating-point answer tolerance is not exercised by this dataset.
- Dataset metadata reports runner-up margins from 2.96% to 20.24%, above the seeder's
  2% floor. This audit inspected the reference SQL and metadata; it did not rerun the
  15 reference queries against the Cloud table or independently certify their truth.

| Historical run | Items | Answer-correct | Mean model cost | Texts differ from current dataset |
|---|---:|---:|---:|---:|
| `breadth-10-20261126T220710Z` | 15 | 14 | $0.1399 | 5 |
| `breadth-300-20261126T220710Z` | 15 | 14 | $0.1492 | 5 |
| `breadth-10-20261126T232112Z` | 15 | 6 | $0.1182 | 5 |
| `breadth-300-20261126T232112Z` | 15 | 12 | $0.1445 | 5 |
| `gate · production v1/v1 · 20261127T033940Z` | 3 | 3 | $0.0947 | 0 |
| `gate · candidate-coarse v2/v2 · 20261127T034111Z` | 3 | 3 | $0.0897 | 0 |
| `gate · candidate-coarse v3/v3 · 20261127T034420Z` | 3 | 2 | $0.0879 | 0 |

The five changed texts are `q-padding`, `q-late-departure-block`, `q-padding-by-month`,
`q-padding-carrier`, and `q-connection-window`. See `MEASUREMENTS.md` for the earlier
observed year-filter problem. The first experiment pair also predates the restrictions
on SQL-side search. None of these runs establishes a full baseline for the fixed code.

## What each evaluator establishes

| Score | Establishes | Does not establish |
|---|---|---|
| `answer_correct` | Every expected answer key matches, with deterministic normalization; errored runs fail | Correct metric calculations, appropriate query scope, exhaustive search, or truthful prose |
| `answer_key_match` | Fraction of expected keys matching; errored runs receive zero | Independent evidence that the expected answer is correct |
| `recommendation_grounded` | The named scenario exists, its claimed metric matches the recorded result, and a repeat query agrees within display-rounding tolerance | Reference-answer correctness, global optimality, or arbitrary narrative claims |
| `task_error` | A task failed or stopped with an error | Whether a successful task answered correctly |
| Cost/time/breadth scores | Recorded run measurements | Answer quality |

Of the 69 reviewed outputs, 68 had `recommendation_grounded=true`; 14 of those answers
were incorrect against the reference. This is expected for a metric-replay check: an
agent can faithfully measure an inferior scenario. It must not be presented as an
overall accuracy or summary-truthfulness score.

## Corrections made during this audit

- Final summaries are rendered from complete recorded scenario results. The analyst's
  draft remains visible on its generation but cannot add claims to the final report.
  `summary_evidence` records the selected point, measured metric, reported support,
  verification state, and counts of metric / missing-metric / failed queries.
- Query counts include repeated scenarios across rounds. Missing metrics never imply
  successful threshold crossings. Reports explicitly limit their scope to tested points.
- Metric replay now compares the claim, original result, and repeat query. The old
  0.5% relative tolerance could accept an incorrect metric. The new `5e-6` relative
  tolerance accommodates the six significant figures shown to the analyst; `1e-9`
  absolute tolerance covers values near zero. Non-finite values are rejected.
- Integer answers are compared without conversion to float, preventing false matches
  above `2**53` and overflow on large integers. Contradictory aliases such as `Origin`
  and `origin` cannot overwrite each other and earn a passing score.
- Gate reports state which configured checks passed. A smoke pass no longer says it is
  safe to promote a prompt. Provisional calibration is documented without changing the
  acceptance policy.

## Replay of the observed summary defect

The approved live run's exported trace is
[`87dbea56649feea10be9485874fbc506`](https://us.cloud.langfuse.com/project/cmwy1nsnr02t1ad0iq8gztwne/traces/87dbea56649feea10be9485874fbc506).
Its analyst draft claimed that every airport crossed 85% within 29 minutes, although
LGA at 29 returned a NULL metric with support 45,895. Its LAX / 9 recommendation did
match the reference and passed metric replay.

An offline replay of all exported scenario records through the new reporting code gives:

> For scenario {"buffer": 9, "origin": "LAX"}, the measured metric was 9 (reported
> support: 68,453). A repeat query confirmed this metric. Evaluated 922 scenario queries
> across 4 round(s): 497 returned a usable metric, 425 returned no usable metric, and 0
> failed. These measurements cover only the tested scenarios.

The selected metric also passes the tightened replay tolerance. This validation reuses
the saved trace and makes no additional model calls. It is not a new live run of the
modified application.

## Calibration still needed

At 15 questions, one answer changes accuracy by 6.7 percentage points. A 70% floor means
11/15 passes; the historical 12/15 baseline leaves one miss of headroom. One baseline
does not measure the probability that a good prompt is rejected or a regression passes.
The three-item candidate demonstrations show that one known coarse-grid defect is
detected, not that the thresholds generalize.

Before relying on this as a release gate, collect repeated full runs using the current
question version, code, pinned prompt pair, model, and service configuration. Review the
reference SQL / expected answers, add a separate challenge set with wrong-scope queries,
near-ties, NULL-heavy results, failed verification, and misleading narratives, and agree
on acceptable false-pass / false-block rates before tuning bounds. Continuous answer
targets would also need a justified numeric tolerance. No new live dataset, labels, or
scores were created by this audit.

## Reproducing the read-back

After loading the demo's Langfuse environment, use the CLI (JSON exports contain `body`
and `data`; experiment item IO may be JSON strings and must be decoded):

```bash
langfuse api dataset-items list --dataset-name scenario-explorer-questions --all --json
langfuse api experiments list --from-start-time 2026-11-20T00:00:00Z --fields core,metadata,scores --all --json
langfuse api experiment-items list --from-start-time 2026-11-20T00:00:00Z --fields core,dataset,io,metadata,scores --all --json
```

Filter experiment items by the dataset ID before comparing. Recompute each item's
`answer_correct` and `answer_key_match` against its expected output; compare with its
observation-level scores. Compare historical item text and expected output with the
current dataset separately. Passing recomputation is a consistency check, not a held-out
calibration result.
