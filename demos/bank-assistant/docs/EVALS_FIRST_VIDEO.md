# Evals-first walkthrough video — storyboard

A narrated screenshot walkthrough of
[DEMO_SCRIPT_EVALS_FIRST.md](../DEMO_SCRIPT_EVALS_FIRST.md), about **10–11
minutes**. It follows the live demo's acts, including the Act 2.5
decision-model beat, but leaves out the **Ask** beats (they are for a live
room) and keeps the **Land** lines as the narration that closes each act. Every
scene below lists what to capture, what to highlight, the caption, and the
voice-over.

Northwind Bank is fictional and all data is synthetic. Ids and numbers were
re-read from the Cloud project `northwind-bank-assistant` on 2026-10-18.

---

## Production spec

| Item | Choice |
|---|---|
| Output | 1920×1080 MP4, H.264, yuv420p, 30 fps, AAC 48 kHz, loudness-normalised |
| Capture | built-in browser pane, light theme, viewport 1440×900, scaled to 1080p in post (slides are rendered at 1920×1080) |
| Highlights | coral `#FF5D5F` 4 px box, drawn in post; a "zoom" is a crop of the full still scaled to 1080p. Box coordinates are recorded per shot in `shots.json` |
| Captions | burned-in lower third, ≤ 8 words, plus an `.srt` from the narration |
| Voice-over | scratch track with macOS `say` to lock timing; replace with a recorded voice later. Scene length = max(planned, VO + 0.8 s) |
| Transitions | 0.3 s crossfade between scenes, no transition within an act's zoom pairs |
| Slides | HTML in the portal palette (blue `#4E9CFF`, coral `#FF5D5F`, charcoal `#1B1917`), screenshotted |
| Working dir | `demos/bank-assistant/video/evals-first/` (`shots/`, `slides/`, `vo/`, `clips/`, `shots.json`, `build.sh`). Add `video/` to `.gitignore` before the first build so binaries stay out of the repo |

**Before capture** (same as the script's presenter prep): restart the MCP server
and portal, run `verify_demo.py`, generate production traffic, and have
**the presenter sign in to Langfuse Cloud in the browser pane** (Claude does not
enter passwords). GitHub Actions pages are public and need no sign-in.

**Never capture:** Langfuse Settings → Members / API keys, the account menu,
any terminal showing `.env` contents.

---

## Scenes

Base URL `LF` = `https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f`.

### Open · define "good" (34 s)

**S00 · Title card** — 8 s · slide
- Content: "How do you know it's good enough to ship?" / "An evals-first walkthrough · Northwind Bank (fictional)"
- VO: "How do you know an AI assistant is good enough to ship? Here's an evals-first walkthrough, on a fictional bank."

**S01 · Five questions → five scores** — 26 s · slide
- Content: the five-row table from the script's Open (question → evaluator → kind).
- Caption: "Quality is a list you can write down"
- VO: "Most teams start by logging everything, then wonder what to look at. Start at the other end. Write down what a good answer means for a bank: is it true, is it allowed, is it safe, is it in the customer's language and register, and did the customer get what they came for? Each question becomes a score."

### Act 1 · Every answer gets graded (117 s)

**S02 · The question** — 15 s · portal `http://localhost:8090`
- Capture: Customer assistant → Ana Torres (C-1001), web → chip "Savings in bitcoin?" → wait for the answer.
- Highlight: the answer bubble and its badges (prompt version, model, latency).
- Caption: "A live production turn"
- VO: "A customer asks the assistant whether to put their savings in bitcoin. The assistant declines to give investment advice and offers to book a licensed advisor. That answer is now being graded."

**S03 · Deterministic scores** — 20 s · trace from S02 → Scores tab (fallback `LF/traces/b31717e318b6bbea090b177effd841cb`)
- Capture: Open in Langfuse → Scores tab, before the judges land, if possible.
- Highlight: box the app-written scores (`security-risk`, `guardrail-blocked`, `pii-in-input`, `language-match`, `intent`, `task-outcome`, `advisor-offered`, `turn-cost-usd`).
- Caption: "Free checks, every turn"
- VO: "Open the trace and go straight to the scores. The app has already written its deterministic checks: security risk, the guardrail's decision, PII, language, the customer's intent, the outcome, cost and latency. They cost nothing and run on every turn."

**S04 · Judge scores with reasoning** — 15 s · same trace, after refresh
- Highlight: zoom on `banking-compliance` = 1 and its reasoning ("Compliant. … declines to recommend Bitcoin …"); smaller box on `faithfulness`.
- Caption: "LLM judges, with reasoning"
- VO: "Seconds later the LLM judges arrive: faithfulness, and the bank's own compliance judge, which scores the answer against conduct rules P1 to P5 and explains why."

**S05 · Evaluators list** — 10 s · `LF` → Evaluators (LLM-as-a-Judge)
- Highlight: the three judges `faithfulness`, `banking-compliance`, `manipulation-resistance`.
- Caption: "Three judges, defined as code"
- VO: "Three judges are defined in the project, as code, and they run on production traffic automatically."

**S06 · A rule: target, mapping, sampling** — 25 s · Evaluators → `faithfulness` rule
- Capture: open the rule detail so that the filter, variable mapping and sampling are all visible (two stills if they don't fit: S06a filter + sampling, S06b mapping).
- Highlight: S06a, the filter (root observation `northwind-assistant`, environment `production`, tags none of `risk:*`) and sampling 100%; S06b, `{{context}}` ← `metadata.context`.
- Caption: "The trace is the judge's evidence"
- VO: "Each rule says what to score and how often. This one targets the assistant's answers in production, maps the customer's message and the answer into the rubric, and scores every one of them. The faithfulness judge also reads the context the model actually saw. That's why we trace: the trace is the evidence the judge reads."

**S07 · Two cost decisions** — 20 s · Evaluators → rules list
- Capture: the rules list, showing `manipulation-resistance-sampled` (20%, enabled) and the retired `manipulation-resistance` (disabled).
- Highlight: S07a, the tags filter `none of risk:*` on a judge rule; S07b, sampling 20% on `manipulation-resistance-sampled`.
- Caption: "Don't judge what you already decided"
- VO: "Two cost decisions sit in these rules. A turn the guardrail blocked always gets the same fixed refusal, so no judge wastes tokens on it. And the security judge runs on a twenty percent sample of everything else, so an attack the rules missed still gets judged."

**S08 · Three layers** — 12 s · slide
- Content: three stacked bands, deterministic (every turn, free) · LLM judges (targeted, sampled) · humans (calibrate).
- VO: "So every answer gets three layers: free deterministic checks, targeted LLM judges, and human signal. But a judge is a model too."

### Act 2 · Grade the grader (162 s)

**S09 · Calibration dataset** — 18 s · `LF/datasets/cmuz2y1p3053yad0e2hpk0mb6` (items tab)
- Highlight: `metadata.source` on one synthetic and one production row; the expected verdict (1.0 / 0.5 / 0.0).
- Caption: "Planted errors + real reviewer labels"
- VO: "Before you trust a judge, test it. This dataset holds two kinds of answer: sixteen whose correct verdict is known by construction, including seeded errors such as a wrong fee, and twenty-seven real production answers a reviewer has labelled."

**S10 · Judge-model bake-off** — 22 s · compare view of runs `aa64b188…` (sonnet-4-6), `3cc6bce7…` (haiku), `62bb3fd9-a074…` (gpt-4.1-mini), `c486cb45…` (sonnet-5-5), all rubric v1 on the same 15 synthetic items
- Capture: select exactly these four (the names repeat; one same-named gpt-4.1-mini run scored 0.0). If the compare view does not show run averages clearly, capture the runs list with its score columns instead.
- Highlight: the accuracy column 1.00 / 0.87 / 0.80 / 0.73.
- Caption: "Same rubric, same items, four judges"
- VO: "Choose the judge model on the planted errors first. The live judge gets all fifteen right. Haiku scores eighty-seven percent, but gets only sixty percent of the minor slips right. The newest model scores seventy-three. The newest model is not automatically the best judge for this rubric."

**S10b · Now add production** — 18 s · run `LF/datasets/cmuz2y1p3053yad0e2hpk0mb6/runs/8e806919-ad3b-4fca-8d1a-2b812b7612a8`
- Capture: the run's run-level scores (`judge-accuracy-synthetic`, `judge-accuracy-production`); fall back to a slide with the two numbers if they aren't on one screen.
- Highlight: synthetic 1.00 vs production 0.81.
- Caption: "Synthetic 1.00 · production 0.81"
- VO: "Now run the same judge on the production answers too. On the planted errors it's perfect. On real answers it agrees with the reviewer eighty-one percent of the time. Synthetic items prove the judge catches what you planted. Only production labels show where it disagrees with people."

**S11 · Why rubric v2** — 14 s · `LF/traces/bff8d9603c5fec585bdf387db0f40193` → Scores
- Highlight: `faithfulness` 0.5 and the reasoning that names the invented navigation steps.
- Caption: "Rubric v2: invented steps = 0.5"
- VO: "Why a second rubric? This answer invents navigation steps that aren't in the help article. Rubric two scores it zero point five."

**S12 · Same answer under v1** — 10 s · `LF/traces/a2db1c01b319bd6cf8756da62d9f9621` → Scores
- Highlight: `faithfulness` 1.0.
- Caption: "Rubric v1 gave it 1.0"
- VO: "The same answer text, under rubric one, scored a perfect one. The rubric was tightened, then re-calibrated."

**S13 · Expert review queue** — 15 s · `LF` → Annotation queues → *SME review — assistant answers* → one item
- Highlight: the score panel (`sme-faithfulness`, `sme-compliance`, `sme-failure-mode`).
- Caption: "Experts label the hardest cases"
- VO: "Then compare the judge with people. Production answers go into a review queue, worst judge scores first, and an expert labels faithfulness, compliance and the failure mode."

**S14 · Judge vs human** — 12 s · `LF` → Scores → Analytics, `faithfulness` vs `sme-faithfulness`
- Capture: note at capture time what this view shows (matrix / MAE / correlation); the kappa figure comes from S15, not this screen.
- Caption: "Same answers, judge vs human"
- VO: "Score analytics puts the judge's score and the human's label side by side, on the same answers."

**S15 · 81% is not good enough** — 25 s · slide
- Content: "26 pairs · 81% agreement · κ = 0.18" / "always-'pass' judge: 85%" / "reviewer failed 4 · judge caught 1".
- Caption: "Use kappa, not percent agreement"
- VO: "The judge's live scores agree with the reviewer eighty-one percent of the time. That sounds fine. But the reviewer passed twenty-two of twenty-six answers, so a judge that always said pass would score eighty-five. This judge caught one of the four answers the reviewer marked down. Cohen's kappa is point one eight, far below the point six a bank should require."

**S16 · Read the disagreement** — 28 s · `LF/traces/aeb2ce3d57fb6ba3071b44044dca59bb`
- Capture: two stills. S16a, the root output (*"I already have that information from my previous search!"*); S16b, Scores, `faithfulness` 0 with reasoning *"no retrieval or tool evidence in the context"* next to `sme-faithfulness` 1.
- Caption: "The judge only saw this turn"
- VO: "Read the disagreements. Here the judge says there's no evidence in the context, but this is a follow-up turn. The facts came from the previous turn's search, which the judge never saw. That was a bug in the evaluator's input, not in the agent, and only a human comparison could find it. It's fixed now: the agent passes the last turns to the judge."

### Act 2.5 · Every turn, every rule, one call (93 s)

Scene ids D1–D4 leave the S-numbers unchanged. The decision-model scores were
re-read on 2026-10-20. Both evaluators run on OpenAI's `gpt-6-luna`, because the
project has no TypeSafe connection for Jev yet. Background:
[DECISION_MODELS.md](DECISION_MODELS.md).

**D1 · The price of a yes** — 22 s · slide
- Content: "13 yes/no signals · 1M turns a month" / "as LLM judges ≈ $35,000" / "as 2 decision-model calls ≈ $330".
- Caption: "Every turn, every rule, one call"
- VO: "Most of what a bank wants to know about each conversation is a yes or a no: a request for a person, a complaint, a scam in progress. Thirteen such signals as LLM judges would cost about thirty-five thousand dollars a month at a million turns. As decision models, about three hundred and thirty."

**D2 · One flag per rule** — 22 s · `LF` → Evaluators → `conduct-screen` → test panel on the root observation of trace `77f9c9ff2d45e2ff9b761bccd0b67bee`
- Capture: D2a, the evaluator: type **Decision model** and its six questions; D2b, the test result (fallback: the trace's Scores tab).
- Highlight: D2a, the type and the question names; D2b, `conduct-unsolicited-upsell` (the live rule scored this trace 0.86), the other five at 0, and the cost estimate.
- Caption: "One call, six rules, a probability each"
- VO: "A decision model doesn't write text: it answers typed questions, each with a probability. This one asks six, one per conduct rule, in a single call. On an old-prompt answer that offers an upgrade, the upsell rule fires and the other five stay near zero, for a fiftieth of a cent."

**D3 · A conversation signal** — 22 s · `LF/traces/22e6b8f41999ef8f77a7853855bff925` → Scores
- Capture: D3a, the turn's input (*"Además me llamó alguien del banco…"*); D3b, the Scores tab.
- Highlight: D3b, `signal-scam-risk` 1, `signal-vulnerability` 1 and `guardrail-blocked` 0; a smaller box on `customer-intent` = `fraud-report` next to the app's `intent` = `other`.
- Caption: "Vulnerable, and being scammed"
- VO: "Two turns in Spanish. I lost my job and can't pay my card. Then: someone from the bank called and told me to move my savings to a safe account. Scam risk: one. Vulnerability: one, read from the earlier turn. No guardrail fires: the assistant isn't under attack, the customer is."

**D4 · Act, review, ignore** — 27 s · slide
- Content: three bands, "≥ 0.7 · act: complaint case, fraud team, vulnerable-customer desk" / "0.3–0.7 · a person reviews → SME queue" / "< 0.3 · ignore"; footer "No rationale · Jev 1.13 is weaker in Spanish → one threshold per language".
- Caption: "A probability buys a third path"
- VO: "Every flag is a probability, so it gets three paths: act above point seven, a person between point three and point seven, ignore below. Two limits: there's no rationale, so the LLM judge still writes the reason. And Jev one point one three is weaker in Spanish, so each language gets its own threshold. Decision models are how you look at every conversation, not a sample."

### Act 3 · Is the new prompt better? (80 s)

**S17 · The golden set is the spec** — 15 s · `LF/datasets/cmuz1xuy1050yad0es71jynk1` → one item
- Highlight: expected output, expected sources, must-include facts.
- Caption: "Written before the prompt"
- VO: "Now, is the new prompt better? The golden dataset is the spec. Each item has an expected answer, the expected sources, and the facts the answer must include."

**S18 · Baseline vs candidate vs other model** — 25 s · compare runs `d0a9afed…` (v1), `384eebee…` (v5), `b686530c…` (gpt-4.1)
- Highlight: S18a, the `correctness` column (0.91 / 0.91); S18b, `no-unsolicited-upsell` (0.92 → 1.00) and `formal-register` (0.00 → 1.00).
- Caption: "The judge didn't move. The checks did."
- VO: "Compare the baseline prompt, the candidate, and the baseline on a different model. The judge's correctness doesn't move: point nine one and point nine one. The candidate wins exactly where it was designed to, on checks you can read: no upselling, and formal Spanish from zero to one."

**S19 · Model comparison** — 16 s · same compare view, one item side by side
- Highlight: `cites-expected-source` 0.56 vs 0.88; a GPT-4.1 answer without the `[KB-xxx]` citation next to a Claude one with it.
- Caption: "Same prompt, half the citations"
- VO: "Same prompt, different model: GPT-4.1 cites the expected policy article about half as often. Open two items side by side to see why. Cost and latency are on the same page."

**S20 · Spanish set** — 14 s · `LF/datasets/cmuz2wt0m056oad0edbc59izw` compare `aa375259…` (v1) vs `e86bc13b…` (v5)
- Highlight: `formal-register` 0.00 → 1.00; `correctness` 0.85 = 0.85.
- Caption: "tú → usted, every answer"
- VO: "On the Spanish set, the baseline addresses every customer informally. The candidate uses the formal usted in every answer. The judge's correctness is unchanged."

**S21 · Decide on what you can read** — 10 s · slide
- VO: "Decide on the metrics you can read. The judge's average is a smoke alarm, not a verdict."

### Act 4 · Let the gate say no (59 s)

**S22 · The red build** — 24 s · `https://github.com/doneyli/clickhouse-llm-observability/actions/runs/37786226009` → job summary
- Highlight: S22a, `avg-correctness 0.969 PASS (soft)`; S22b, the two `FAIL (hard)` rows and the `GATE FAILED` line.
- Caption: "Judge: 0.97. Gate: blocked."
- VO: "Then automate the no. This prompt tells the assistant to grow customer relationships and to always answer in English. It reads fine. The gate runs it on the golden set. The judge's correctness, point nine seven, passes. Upselling and language are hard failures, and the build goes red."

**S23 · Hard vs soft** — 10 s · `https://github.com/doneyli/clickhouse-llm-observability/blob/main/demos/bank-assistant/cicd/thresholds.json`
- Highlight: the `hard` and `soft` blocks.
- Caption: "Hard blocks. Soft warns."
- VO: "Hard thresholds on deterministic metrics block the release. Judge averages only warn. A missing metric counts as a failure."

**S24 · How it's wired** — 18 s · slide (diagram)
- Content: Langfuse prompt saved / label moved → automation → GitHub `repository_dispatch` → `prompt_gate.py` EN + ES → exit 1 = red check.
- VO: "It's wired to prompt management. Saving a version or moving a label in Langfuse triggers the GitHub workflow, which runs the gate in English and Spanish. The judge would have shipped this regression. The deterministic checks stopped it."

**S25 · The candidate passes** — 7 s · `…/actions/runs/37786230303`
- Highlight: the green check.
- VO: "The candidate prompt passes the same gate in both languages."

### Act 5 · Production teaches the dataset (75 s)

**S26 · The business symptom** — 22 s · `LF` → Dashboards → *Northwind — Business value & failure modes*
- Highlight: *Dispute self-service rate by trace version*, the `assistant-1.5.0 · prompt v1` bar (0.20).
- Caption: "8 in 10 disputes leaked to the phone"
- VO: "Offline evals cover only what you thought to test. In production, the business dashboard shows dispute self-service by release. One release sits at twenty percent: eight in ten customers who asked for a dispute left without one, and they will call the contact centre."

**S27 · Faithful to the wrong data** — 26 s · `LF/traces/a4e261601983c78e3fd00fcebacec479?observation=a60d0396885d223a`
- Capture: S27a, the trace with Scores (`faithfulness` 1.0, `banking-compliance` 1.0) and the answer ("…not in your last 30 days"); S27b, the MCP server span output, `window_days: 30`.
- Caption: "Judges passed it. The trace explains it."
- VO: "Yet the judges gave this answer full marks for faithfulness and compliance, because it was faithful to what the tool returned. The trace shows why. The agent's MCP client asked for thirty days of transactions, a code default, and the core-banking service, in the same trace, returned only thirty. The policy allows sixty."

**S28 · The failure becomes a test** — 18 s · `LF/datasets/cmuzk1tip00erad0e3rlr0cz7` compare `58fd32ff…` (1.5.0) vs `a0d3edd2…` (1.5.1)
- Highlight: `dispute-opened` 0.20 → 1.00.
- Caption: "0.20 → 1.00, now a regression test"
- VO: "Turn the failure into a test: ten dispute cases, English and Spanish, scored on whether a dispute was actually opened. The old release scores point two. The fix, a sixty-day lookback, scores one."

**S29 · After the fix** — 9 s · `LF/traces/1be9e4f473f5f7a0e939f77f47e5341a`
- Highlight: the `DSP-######` case id in the answer.
- VO: "The same request after the fix: the dispute is opened on the right transaction, with a case number."

### Close (41 s)

**S30 · The loop** — 15 s · slide (cycle diagram)
- VO: "That's the loop: define good, grade every answer, grade the grader, prove changes offline, gate the release, watch outcomes in production, and turn every failure into a test."

**S31 · The first two weeks** — 20 s · slide
- VO: "To start: in week one, agree the questions with risk and compliance, and have an expert read fifty real traces. In week two, calibrate one judge against expert labels, seed the golden set from real failures, and put the gate in CI."

**S32 · End card** — 6 s · slide
- Content: repo path `demos/bank-assistant` · "Northwind Bank is fictional; all data is synthetic."
- No VO.

**Running time:** about 661 s planned (≈ 11 min) before VO padding.

---

## Build steps

1. **Slides:** render S00, S01, S08, S15, D1, D4, S21, S24, S30, S31, S32 as
   HTML at 1920×1080 → `slides/Sxx.png` (`slides/Dx.png`).
2. **Screens:** for each remaining scene, navigate, wait for the data to load, take
   the still → `shots/Sxx[a|b].png`. Record the highlight box (x, y, w, h in
   capture pixels) and any zoom crop in `shots.json`. Check every number on
   screen against the scene's highlight line. If the live UI differs, fix the
   storyboard first.
3. **VO:** `say -v <voice> -o vo/Sxx.aiff "<narration>"` → measure with
   `ffprobe` → final scene length.
4. **Clips:** per scene, `ffmpeg -loop 1 -i still` + `drawbox` (highlight) +
   `drawtext` (caption) + VO → `clips/Sxx.mp4`.
5. **Assemble:** `xfade` 0.3 s between scenes → `loudnorm` →
   `northwind-evals-first.mp4` + `northwind-evals-first.srt`.
6. **Review pass:** watch the whole video once at 1×. Check that no frame shows
   settings, keys or account menus.
