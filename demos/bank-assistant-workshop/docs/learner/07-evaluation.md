**DRAFT teaching material.**

# 07 Evaluation

Start `07-evaluation`. The complete experiment task and callback scores are present.

**Goal:** change one instruction and compare evidence on the same dataset.

The baseline asks for policy retrieval but does not require citations. The candidate adds one instruction requiring supporting KB citations. Provider, retrieval, guards and tool code remain the same. Inspect `WORKSHOP_CANDIDATE` in `src/northwind/prompts.py`.

Publish the candidate without changing the baseline label:

```bash
../../.venv/bin/python workshop.py --env-file ../../.env prompt --variant candidate --label workshop-candidate
```

Choose the same dataset version for both runs:

```bash
../../.venv/bin/python workshop.py --env-file ../../.env experiment --prompt-label workshop-baseline --dataset-version YOUR_VERSION_TIMESTAMP
../../.venv/bin/python workshop.py --env-file ../../.env experiment --prompt-label workshop-candidate --dataset-version YOUR_VERSION_TIMESTAMP
```

Replace the placeholder with the historical dataset version timestamp. For a fresh unchanged teaching dataset, you may omit the flag, but confirm no item edits occurred between runs. Keep model, judge and concurrency the same.

In Langfuse compare the two experiment rows. Start with answer citation coverage, then inspect correctness, factual keywords and language. Open two item traces: one that improved and one that did not. Read the answer and the cited policy. Baseline may already cite some articles; no fixed improvement percentage is promised.

**Decision exercise:** would you serve the candidate? State what improved, what worsened, which examples need review, and whether the test set answers your release question. Record the prompt versions, dataset version, model and judge.

**Rollback rehearsal:** set `NORTHWIND_PROMPT_LABEL=workshop-candidate` in your private setup file, restart, and inspect one answer. Return it to `workshop-baseline`, restart and verify the baseline version. The package never moves the original demo's production label.

**Review before sending: nuanced tradeoffs involved (Zone B, MEDIUM).** Deterministic checks are cheap and repeatable but shallow. Judges capture semantic mismatches but add cost and variability. Human review decides when either metric is misleading. Repeat unchanged runs when observed variance matters; eight examples are not statistical validation.

Source: [MCP experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk), [MCP prompt labels](https://langfuse.com/docs/prompt-management/features/prompt-version-control). HIGH confidence mechanisms, level 1. Candidate superiority is intentionally an unproven experiment hypothesis.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
