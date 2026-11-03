# Live script: one page to present from

Full detail is in [WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md). This page is the version to keep open while presenting.

## Narrative

"Your agent picks its own tools now, so 'why did it do that?' has no single owner. A
trace is the agent's flight recorder. Today you'll answer four questions from it
without an engineer: **why did it do that, did it retrieve the right info, why is it
slow, and why did it loop or skip a step**. For each, we'll see who owns the fix, and
the ones you can fix yourself."

## Script

| Min | Show | Say |
|---|---|---|
| 0–5 | The four questions on screen | "Every investigation starts with one of these." |
| 5–20 | Tracing: `metadata.customer:contoso`, then `scores.reviewer-decision:rejected`; a saved view; Sessions | "Start from a customer or a complaint, never a trace id." |
| 20–40 | An approved Contoso pricing trace: Tree, then Graph (aggregated vs expanded), then a `plan-next-step` (prompt version, tool list, **expand Thinking**, tool calls), then the next step's input, Timeline, Log view + Ctrl-F `$12` | "Every decision has a receipt: what it saw, what it thought, what it did, what came back." |
| 40–50 | **Case 1, Contoso:** retriever output shows archived $12 passages, the **Attributes** tab shows the archive status and date, `verify-claims` "supports" both prices. ⚡ Portal: tick `exclude-archived` and re-run | "The model was never told the doc was archived. It's a data problem, owned by the deployment team." |
| 50–60 | **Case 2, Fabrikam:** Timeline shows three fetches back to back; one raw-HTML page makes a single step 10× the cost. ⚡ Tick `parallel-tools` + `clean-html`: $0.42 → $0.15 | "Slow is rarely the model. Bring platform engineering this step, this many seconds, this many dollars." |
| 60–70 | **Case 3, Tailspin:** Graph shows `search_answer_engine (14/14)`; output stuck at 99%; the prompt says "retry until you have it". ⚡ PM edits the prompt to "retry at most twice", re-run: 9 steps → 4 | "It's following instructions faithfully against a tool that never says done." |
| 70–80 | **Case 4, Tailspin:** no `check_claims` before staging; the 2nd `plan-next-step` thinking says it skipped "because the speed guidance says to"; prompt v2 vs v3 diff and commit message. ⚡ Restore the rule as a new version, re-run | "Instructions are product. Versioned, linked to every run; the label is the deploy." |
| 80–85 | Break | |
| 85–100 | Dashboard (slowest tools, rejections, spend per customer), then Halo: "which runs were rejected and why?" | "One bad run is an anecdote; this tells you if it's a pattern." |
| 100–110 | Comment on an observation, root-cause label in the annotation queue, add to the `regression-cases` dataset | "Find it, explain it, label it, hand it off, or fix it yourself." |
| 110–120 | Q&A | |

## Links

- **Portal:** http://localhost:8097 (start it with `.venv/bin/python webapp/server.py`). The Presenter console holds the fix toggles and the instructions version.
- **Project:** https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru. Set the time range to **Past 7 days**; the default is Past 1 day and the seeded runs are from Nov 2.
- **Case 1:** [018dd48c](https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru/traces/018dd48c3296bc83e7f412328c3145b3) (fact-check "supports" both $12 and $15) · [754a8ff7](https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru/traces/754a8ff7cdebe6293d46546df4bb0eaa) (asks the marketer) · [ed09bf68](https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru/traces/ed09bf6813efb217fb3d74c14b240e4a) (approved, recovered)
- **Case 2:** [12dde9c4](https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru/traces/12dde9c43c20b8004191e0c4759ea633) (88 s; approved but "far too slow")
- **Case 3:** [68be0677](https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru/traces/68be0677c376f436f55e36cf93785a21) (9 steps, rejected)
- **Case 4:** [828eb8c9](https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru/traces/828eb8c9caacf43b6d350fbb97995aa0) (v3, rejected) · [41344e79](https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru/traces/41344e79bb4e1d3939da6541b49769e5) (v2, fact-checked)
- **Dashboard:** [Agent health — deployment view](https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru/dashboards/cmw06mjs602b0ad0edf5lpk1p) · **Session:** [contoso-chat-cf4480e4](https://us.cloud.langfuse.com/project/cmw064gym06s2ad0ef63umyru/sessions/contoso-chat-cf4480e4)

## If something misbehaves

- **Backup clips** are in `recordings/` (not in git): `case1-stale-context`, `case2-latency`, `case3-loop`, `case4-skipped-factcheck`, `find-runs`, `dashboard`, `session` (.mp4).
- **A live run surprises you?** Debug that run. It's more convincing than the script.
- **After the session,** run `.venv/bin/python scripts/seed_prompts.py --reset-tailspin` to put `production` back on v3.

## Case 3 / Case 4 prompt edits (copy-paste)

- **Case 3:** replace the retry line with: `- Weekly visibility reports must cover all five answer engines. Retry an engine at most twice; if it is still capturing, report it as unavailable.`
- **Case 4:** add back:

  ```
  - Before calling stage_cms_draft or queue_correction, run check_claims on every
    factual claim in the draft or correction, and fix anything that is not supported.
  ```
