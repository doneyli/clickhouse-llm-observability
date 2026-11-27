# Bank Assistant Workshop — Workshop Guide

Engineers work through nine modules on their own laptops. They start with an untraced
assistant for Northwind Bank, a fictional retail bank, and finish with a prompt change
they compared against a dataset and can roll back. They leave with the loop applied to
one example from their own work. The facilitator's talk track for each module (Frame,
Show, Learner work, Land, Ask, pitfalls, recovery) is in `docs/instructor/`; this page is
the run of show that ties them together.

- **Format:** hands-on, one dedicated Langfuse Cloud project and one private `.env` per
  learner, a second facilitator for setup and Python problems
- **Observability:** Langfuse Cloud, one project per learner, trace `northwind-assistant`;
  `verify_demo.py` expects project `bank-workshop` unless `DEMO_LANGFUSE_PROJECT` names
  another (the 2026-10-12 instructor rehearsal used `banking-workshop`)
- **Run length:** 180 min. The per-module minutes are a teaching estimate from the
  [delivery guide](docs/preparation_and_materials/README.md), not a measured completion
  time. A 90-minute guided variant demonstrates modules 05 to 07 from prepared runs

> Setup lives in [README.md](README.md). Preparation in detail, delivery options and
> optional extensions: [delivery guide](docs/preparation_and_materials/README.md). Slide
> outline: [slides.md](docs/preparation_and_materials/slides.md).

---

## Before the session

In the instructor's project, the day before. `serve` keeps its terminal, so run the rest
in a second one:

```bash
cd demos/bank-assistant-workshop
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
.venv/bin/python scripts/seed_all.py --allow-draft   # both prompt labels + the dataset; idempotent
.venv/bin/python workshop.py serve                    # terminal 1: ask a wire question and an account question
.venv/bin/python workshop.py experiment --prompt-label workshop-baseline    # terminal 2: prepared runs for modules 06-07
.venv/bin/python workshop.py experiment --prompt-label workshop-candidate   # model calls; cost not measured
.venv/bin/python scripts/checkpoint.py --build-all --output dist/checkpoints   # the nine starting states
.venv/bin/python scripts/verify_demo.py --links      # with serve up: READY, plus the link for every module
```

On the day, start `serve` again and re-run `scripts/verify_demo.py --links` before the
room fills. `--prep` skips the portal and MCP checks while `serve` is down, and its
verdict says so; it is not session readiness.

Use `--reviewed` instead of `--allow-draft` once an instructor has reviewed the eight
references and cleared their markers.

For the room: a project per learner with write access (prompts, datasets, evaluators), a
spend limit sized for eight agent turns plus eight judge calls per experiment run, Python
3.12 and `uv` on every laptop, and ports 8090 and 8765 free. Distribute the checkpoint
archive without any `.env`. The readiness check is for the facilitator's project; a
learner's own project only passes it once module 07 is done. Full checklist: the
[delivery guide](docs/preparation_and_materials/README.md#workshop-delivery-guide).

---

## Run of show

Add breaks to suit the room; the times below have none.

| Time | Module | Outcome |
|---|---|---|
| 0:00 | Module 0 · Setup | Everyone runs the same untraced app; no trace yet |
| 0:15 | Module 1 · Base app | The visible answer is only the last step of a multi-step application |
| 0:25 | Module 2 · Tracing | An answer is debugged by reading the steps that produced it |
| 0:55 | Module 3 · Prompt management | A linked version makes a prompt change explainable and reversible |
| 1:15 | Module 4 · Monitoring | A signal points to evidence; it does not prove overall quality |
| 1:40 | Module 5 · Dataset | Reviewed expected behaviour is the basis of a comparison |
| 2:00 | Module 6 · Experiments | The real app runs against reusable examples with explicit scores |
| 2:25 | Module 7 · Evaluation | A release decision rests on examples and constraints, not one average |
| 2:50 | Module 8 · Wrap-up | Learners map the loop onto one use case of their own |
| 3:00 | End | Learners keep their experiment links and version metadata |

---

## Module 0 · Setup (15 min)

**Goal.** Every learner has the untraced app answering a policy question.

**Steps.** [Learner lesson](docs/learner/00-setup.md) · [instructor notes](docs/instructor/00-setup.md).
Install, `cp .env.example .env`, generate `00-setup`, run `workshop.py --env-file ../../.env preflight`
and `serve`, ask "How much does an outgoing international wire cost?".

**Check.** The portal says tracing disabled and the answer cites a policy lookup.

---

## Module 1 · Base app (10 min)

**Goal.** Learners can name the graph's steps before they trace them.

**Steps.** [Learner lesson](docs/learner/01-base-app.md) · [instructor notes](docs/instructor/01-base-app.md).
Wire fee, Ana's accounts over MCP, a cross-customer request, a Spanish question.

**Check.** Retrieval and MCP tools work, and the cross-customer request is refused.

---

## Module 2 · Tracing (30 min)

**Goal.** One turn becomes one agent root with generations, retrieval, guardrails and the
MCP server's spans in the same trace.

**Steps.** [Learner lesson](docs/learner/02-tracing.md) · [instructor notes](docs/instructor/02-tracing.md).
Complete `src/northwind/tracing.py`, restart both processes, run preflight again, open the
trace link.

**Check.** `kb-retrieval` shows document ids, two turns share a session, an injection
request has a blocked guardrail and no generation, and the synthetic card shows as
`[REDACTED_CARD]`.

---

## Module 3 · Prompt management (20 min)

**Goal.** The app serves `bank-workshop/<WORKSHOP_ID>/assistant` by label, and the version
shows on every generation.

**Steps.** [Learner lesson](docs/learner/03-prompt-management.md) · [instructor notes](docs/instructor/03-prompt-management.md).
`workshop.py prompt --variant baseline --label workshop-baseline`, complete
`prompt_source.py`, restart, then the missing-label fallback exercise.

**Check.** The generation links the prompt version, and the portal shows "local fallback"
for a label that does not exist.

---

## Module 4 · Monitoring (25 min)

**Goal.** Learners can find turns worth reviewing without reading every trace.

**Steps.** [Learner lesson](docs/learner/04-monitoring.md) · [instructor notes](docs/instructor/04-monitoring.md).
Add the three BOOLEAN signals in `monitoring.py`, send the four test messages, then set up
an observation-level LLM judge for customer disagreement and preview it.

**Check.** `guardrail-blocked`, `pii-in-input` and `all-caps-signal` sit on the root
observation, and "WHAT IS MY BALANCE" shows the capitals signal's false positive.

---

## Module 5 · Dataset (20 min)

**Goal.** Eight reviewed examples separate input, expected output and metadata.

**Steps.** [Learner lesson](docs/learner/05-dataset.md) · [instructor notes](docs/instructor/05-dataset.md).
Review each reference against `knowledge.py`, clear its marker, then
`workshop.py dataset --reviewed`. Record the dataset version timestamp.

**Check.** Exactly the intended item ids exist in `bank-workshop/<WORKSHOP_ID>/regression-v1`.

---

## Module 6 · Experiments (25 min)

**Goal.** The real agent runs against the dataset with repeatable item scores.

**Steps.** [Learner lesson](docs/learner/06-experiments.md) · [instructor notes](docs/instructor/06-experiments.md).
Complete `make_task`, add the callback evaluators, then
`workshop.py experiment --prompt-label workshop-baseline` (or `--no-judge` on a tight budget).

**Check.** All eight items complete with their scores and the recorded prompt version; the
command exits zero.

---

## Module 7 · Evaluation (25 min)

**Goal.** Learners decide whether to serve a one-instruction change, on evidence.

**Steps.** [Learner lesson](docs/learner/07-evaluation.md) · [instructor notes](docs/instructor/07-evaluation.md).
Publish `--variant candidate --label workshop-candidate`, run both labels on the same
`--dataset-version`, compare, open one improved and one unchanged item, then rehearse the
rollback with `NORTHWIND_PROMPT_LABEL`.

**Check.** Each learner states what improved, what did not, and which versions they compared.

---

## Module 8 · Wrap-up (10 min)

**Goal.** Learners explain the loop with one bank example and apply it to their own app.

**Steps.** [Learner lesson](docs/learner/08-wrap-up.md) · [instructor notes](docs/instructor/08-wrap-up.md).
Walk one policy question from trace to prompt version, signal, dataset item and
comparison.

**Check.** Each learner writes down a use case, one review signal and three reference
examples.

---

## Known limits

- The bank, its customers and its policies are fictional; customer selection simulates a
  login and is not authentication.
- Masking is regex-based and does not catch every kind of personal data.
- The eight references are synthetic and still pending instructor review; eight items do
  not prove a change is better.
- The platform judge from module 04 has not been run in a preview yet, so rehearse it in
  your own project before showing it live.
- The minutes above are an estimate. Time one rehearsal and adjust.

The full list is in the README's [Known limits](README.md#known-limits).

---

## Reset / re-run

```bash
# stop serve with Ctrl-C, then start it again: resets synthetic card state (e.g. a blocked card)
.venv/bin/python workshop.py serve
# put NORTHWIND_PROMPT_LABEL back to workshop-baseline in .env after the module 07 rollback rehearsal
.venv/bin/python scripts/seed_all.py --allow-draft   # puts both labels and the dataset back to their definitions
.venv/bin/python scripts/checkpoint.py 02-tracing --output work/02-tracing-again   # a fresh starting state; old folders are kept
.venv/bin/python scripts/verify_demo.py              # READY again
```

Nothing deletes project data. Prompt versions and experiment runs accumulate; the dataset
upsert never removes remote items.
