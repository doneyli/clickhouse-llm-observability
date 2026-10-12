# Story arc 6 — Cost regression after a "recall" release

Drop-in section for the DEMO_SCRIPT "Story arc" (same Symptom → Pinpoint → Root
cause → Fix → Verify shape as Issues 1–5). Every number below was measured in the
Langfuse Cloud project on 2026-10-08 (live traffic 13:11–13:15 UTC, experiments
13:11–13:13 UTC) from the runs and traces linked here. None are estimates, except
where a line says "arithmetic". **Prompt: v1 in every run and trace.** It was the
`production` label then and is labelled `baseline` now. The releases differ only
in retrieval settings: same questions (fixed seeds), same prompt, same model
(`claude-sonnet-4-6`).

| release | retrieval | role |
|---|---|---|
| `assistant-1.5.1` | k = 3, relevance threshold 0.08 | baseline (before the change) |
| `assistant-1.6.0` | **k = 8, no threshold** ("more context for better recall") | the regression |
| `assistant-1.6.1` | k = 3, threshold 0.08 (current code defaults) | the fix: reverts the retrieval change |

1.5.1 and 1.6.1 run the same retrieval code, so 1.6.1 should land back on the
baseline, and it does.

### Issue 6 — Cost regression after a "recall" release (TCO, GATE-05)
- **Symptom.** Bottom row of the business dashboard, *Cost per turn (USD) by
  trace version*: `assistant-1.5.1 · prompt v1` **USD 0.0133** →
  `assistant-1.6.0 · prompt v1` **USD 0.0151** (+14 %). Next to it, *LLM calls
  per turn by trace version* is flat at **2.15** for both. The model is not
  being called more often; each call is bigger. *Turn latency (s)* barely moves
  (6.20 s → 6.53 s). The quality scores did not drop either (`faithfulness`
  0.963 → 0.981), so a quality-only view would pass this release. The extra cost
  is visible only if you chart cost per turn by release.
- **Pinpoint.**
  1. Tracing → Traces → filter **Version** = `assistant-1.6.0 · prompt v1` →
     search `international wire` → open the
     [1.6.0 hero trace](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/4a34ae082d97d3b3e3d20621ae37671e?observation=af56d633d80f83d7)
     ("What's the fee for an international wire transfer?"). It opens on the
     retriever.
  2. `tools` → `search_knowledge_base` → **`kb-retrieval`** (retriever). **Input**
     `{"query": "international wire transfer fee", "k": 8, "min_score": 0.0}`.
     **Output** has 8 documents. Two are relevant: KB-202 *International wire
     transfers* 0.291 and KB-301 *Everyday and Premier accounts* 0.124. Six score
     below 0.08 and have nothing to do with wires: KB-103 *Credit card interest*
     0.056, KB-102 *Disputing a card transaction* 0.053, KB-302 *Overdraft
     protection* 0.036, KB-104 *Using your card abroad* 0.034, KB-401 *Personal
     loans* 0.030, KB-101 *Lost or stolen cards* 0.000.
  3. Click the second `ChatAnthropic` generation (the call after the retrieval):
     **2,221 input tokens**, USD 0.0097. On the
     [1.6.1 twin](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/56635bee11886506736f5f63b43eed18?observation=6252a29a61fccb58)
     the same question retrieves only KB-202 and KB-301, and the same generation
     takes **1,455** (+53 % on 1.6.0), USD 0.0073. The first generation is
     identical in both traces (1,142 tokens) because it runs before retrieval.
     Turn cost: USD 0.0140 vs 0.0116 (+21 %). `faithfulness` is **1.0 on both**,
     so the six extra articles did not make the answer better.
  4. Offline confirmation: Datasets → `northwind-golden-qa-v1` → select runs
     `release 1.6.0 · k8 · v1` and `release 1.6.1 · k3 · v1` → **Compare**. The
     quality columns are identical. Cost per item is +24 % on 1.6.0.
- **Root cause.** Release 1.6.0 raised the retriever's `k` from 3 to 8 and
  removed the relevance threshold, "for better recall". In this knowledge base a
  typical question has fewer than 2 articles above the threshold (1.7–1.8 per
  retrieval on 1.5.1/1.6.1), so 1.6.0 pads every retrieval to 8 with articles
  the model doesn't need: **75.7 %** of the documents 1.6.0 retrieved scored
  below 0.08. They go into every LLM call made after the retrieval. Recall did
  not improve because the right article was already ranked first: `source-recall`
  is 0.875 in both runs.
- **Fix.** Release **1.6.1**: back to k = 3 plus the 0.08 relevance threshold
  (`RETRIEVAL_K` / `RETRIEVAL_MIN_SCORE` in `northwind/agent.py`). The prompt
  stays v1. The retriever observation records `k` and `min_score` in its input,
  so the setting a trace actually ran with can be read from the trace itself.
- **Verify.**
  - *Live traffic by release*: the same 14 conversations (10 core, 4 Spanish),
    27 turns per release, 15 of them with a retrieval. Prompt v1. Read from the
    API: `scripts/run_cost_arc.sh verify`.

    | | 1.5.1 · v1 (baseline) | **1.6.0 · v1 (k8)** | 1.6.1 · v1 (fix) |
    |---|---|---|---|
    | cost per turn, `turn-cost-usd` (list-price estimate) | USD 0.013270 | **USD 0.015124** | USD 0.013368 |
    | cost per turn, Langfuse-computed (sum of generation `totalCost`) | USD 0.013270 | **USD 0.015124** | USD 0.013368 |
    | cost per turn, retrieval turns only | USD 0.013717 | **USD 0.016926** (+25 % vs 1.6.1) | USD 0.013513 |
    | input tokens per turn | 3,128 | **3,709** (+17 %) | 3,177 |
    | input tokens per generation | 1,456 | **1,727** (+19 %) | 1,454 |
    | LLM calls per turn, `llm-calls` | 2.15 | 2.15 | 2.19 |
    | turn latency, `turn-latency-s` | 6.20 s | 6.53 s | 6.41 s |
    | documents per retrieval | 1.77 | **8.0** | 1.70 |
    | share of retrieved documents below 0.08 | 0 % | **75.7 %** | 0 % |
    | `faithfulness` (managed judge, n = 27 each) | 0.963 | 0.981 | 0.959 |
    | `banking-compliance` (managed judge, n = 27 each) | 0.944 | 0.944 | 0.981 |

    1.6.0 vs the 1.6.1 fix: cost per turn **+13 %**, vs the 1.5.1 baseline
    **+14 %**. 1.6.1 is back on the baseline (USD 0.013368 vs 0.013270).
  - *Experiments*, `northwind-golden-qa-v1`, 16 items, prompt v1:

    | run | correctness | must-include | source-recall | cites-expected-source | no-unsolicited-upsell | cost per item | input tokens per item | LLM calls per item | latency per item |
    |---|---|---|---|---|---|---|---|---|---|
    | `release 1.6.0 · k8 · v1` | 0.9375 | 1.00 | 0.875 | 0.875 | 0.923 | **USD 0.0136** | **3,349** | 1.875 | 12.3 s |
    | `release 1.6.1 · k3 · v1` | 0.9375 | 1.00 | 0.875 | 0.875 | 1.000 | USD 0.0109 | 2,519 | 1.875 | 13.2 s |

    Same quality, **+24 % cost per item** and +33 % input tokens on 1.6.0. Runs:
    [release 1.6.0 · k8 · v1](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/b3087bd9-6972-4075-9030-05dbd984c557) ·
    [release 1.6.1 · k3 · v1](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/8a1b5392-8e00-4d74-acf7-fe094a81a5ee).
  - *What it means at volume* (arithmetic on the measured per-turn delta, not a
    forecast): (0.015124 − 0.013368) × 1,000,000 turns = **USD 1,756 per million
    turns** at this traffic mix, where 15 of 27 turns retrieve. Counting only
    turns that retrieve: (0.016926 − 0.013513) × 1,000,000 = USD 3,413 per
    million. The bank should plug in its own volume and share of retrieval turns.
- **Land.** "Langfuse records the cost of each release next to its quality.
  Here 1.6.0 cost 13 % more per turn and bought no quality. You can see why in
  one span: the retriever's input says `k: 8`, and six of its eight documents
  score below the relevance threshold. The same experiment that checks quality
  before a release also checks cost: cost per item is in the run compare view."
- **Candour.**
  - *Two cost numbers.* `turn-cost-usd` is the app's own estimate: token usage ×
    list price (`claude-sonnet-4-6` at USD 3 / 15 per 1M input/output tokens,
    `_PRICE` in `northwind/agent.py`). Langfuse computes cost per generation from
    its model price table. Here the two agree to six decimals on all 81 turns,
    because both start from the same token counts and the same list prices. With
    negotiated rates, prompt caching or a self-hosted model they will differ.
    Then trust the Langfuse cost, after setting the bank's model prices in
    Langfuse.
  - *Small n.* 27 turns per release, 15 of them with a retrieval, and 16 golden
    items. Read direction, not decimals. The token growth is structural (same
    questions, same documents, so the same extra tokens on every run). The
    latency differences are not: +2 % live and −7 % in the experiment (1.6.0 was
    *faster* there), which is noise at this context size. Don't claim a latency
    regression. Likewise faithfulness 0.981 vs 0.959 and no-unsolicited-upsell
    0.923 vs 1.000 (one item) are within noise. The claim is "no quality gain",
    not "quality changed".
  - *Same journeys, on purpose.* The arc's widgets in the bottom row are
    filtered to the replayed journeys (tags `scenario:core` / `scenario:es`), so
    every release bar is measured on the same conversations. Without the filter,
    the 1.5.x bars also include arc 5's dispute conversations. Those make more
    LLM calls per turn, so the unfiltered 1.5.1 bar was higher than 1.6.0's
    (USD 0.0161 over 42 turns, 2.60 calls per turn, when this was written). That
    is the traffic mix, not the release. Say this out loud: compare releases on
    the same traffic.

**Ask.** "Do you know what one assistant turn costs you today, by release? Who
would notice if it went up 13 % on a Tuesday deploy?"

## Reproduce

```bash
scripts/run_cost_arc.sh           # 1.5.1 → 1.6.0 → 1.6.1 live traffic + both experiments, then verify (~8 min)
scripts/run_cost_arc.sh verify    # re-read the evidence: per-release table, % deltas, hero trace pair
.venv/bin/python scripts/seed_business_dashboard.py   # adds the arc 5/6 widgets to an existing dashboard
```

Each release is a process with env overrides: regression
`NORTHWIND_RELEASE=assistant-1.6.0 NORTHWIND_RETRIEVAL_K=8 NORTHWIND_RETRIEVAL_MIN_SCORE=0`,
fix `NORTHWIND_RELEASE=assistant-1.6.1`, baseline `NORTHWIND_RELEASE=assistant-1.5.1`
(defaults otherwise). The script pins prompt label `baseline` (v1), because
`production` moves during the workshop (Act 5.4). The experiment step skips a run
name that already exists; `--rerun` adds a time suffix. `verify` reads only the
traces listed in `logs/cost-arc/traffic-<release>.log`, and drops any turn served
by another release or prompt version.

Dashboard: [Northwind — Business value & failure modes](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/dashboards/cmuzijcgx04bead0iw7s6tpgk).
The arc widgets are at the bottom: *Dispute self-service rate*, *Cost per turn
(USD)*, *LLM calls per turn* and *Turn latency (s)* by trace version, then *Cost
per turn over time* with one line per trace version. Set the date range to
include 2026-10-08.
