# Demo Standard Spec

**Status:** proposed, not implemented · **Written:** 2026-11-26

This is the build plan for one consistent structure across every demo in `demos/`.
Once it is implemented, `demos/CONTRIBUTING.md` is the living standard and this file
stays as the design record.

## 1. Summary

Every demo folder gets the same core assets, a README with the same sections in the
same order, and a demo script with the same skeleton. Four pieces hold that in place:

| Piece | What it is | Why it is needed |
|---|---|---|
| `demos/CONTRIBUTING.md` | The standard, written for a person or an agent starting a demo | Says what "done" means |
| `demos/_template/` and a `new-demo` skill | A copyable skeleton with every required file and heading | New demos start out conforming |
| `demo.yaml` in each demo | A small manifest: profile, what it covers, commands, add-ons, waivers | Lets tooling treat a 5-minute container demo and a standalone workshop differently, and feeds the index |
| `scripts/check-demo-structure.py` in CI | Fails a PR when a demo lacks a required file, heading or index entry | A written standard alone drifts (§2) |

The demo index in `demos/README.md` is generated from the manifests, so adding a demo
means filling in one file instead of editing four hand-kept lists.

## 2. Current state

Measured on 2026-11-26 against `main` at `dfbe6ee`, from file and heading checks, not a
full read of every doc. The checker (§12) will produce the exact list when it exists.

| Area | Finding |
|---|---|
| Demo folders | 17 on `main`. Two more in open PRs: `marketing-agent-debugging` (#113) and `multi-agent-handoff` (#118). 19 in total. |
| README | Missing in `text-to-sql`, `vector-rag`, `agentic-rag`. The other READMEs use at least four different section sets. |
| Demo script | 16 of the 17 demos on `main` have `DEMO_SCRIPT.md`; `bank-assistant-workshop` does not. 7 follow the full skeleton: `agentic-rag`, `brand-promo-multi-agent`, `langfuse-rls`, `real-estate`, `support-triage-parallel`, `text-to-sql`, `vector-rag`. |
| Readiness check | 6 of 17 have one, under four names: `verify_demo.py`, `smoke_test.py`, `verify.sh`, `run_demo.sh`. |
| Tests | None in `vector-rag` or `langfuse-rls`. `real-estate` has no `tests/` folder. One test file each in `grocery-assistant`, `insurance-claims`, `litellm-gateway`. No workflow runs any demo's tests. |
| Index | `demos/README.md` omits `insurance-claims` and `bank-assistant-workshop`. `docs/README.md` links 8 of 16 scripts. `CLAUDE.md` names 9 demos. The `run-demo` skill offers 3 choices. |
| CI | The `deps-resolve` matrix in `demo-integrity.yml` is a hand-kept list of 9 demos. It omits `bank-assistant`, `bank-assistant-workshop` and `insurance-claims`, which all have a `requirements.txt`. |

The same section goes by several names:

| Meaning | Names in use today |
|---|---|
| Install and first run | Quick start, Setup, Set up from scratch, Start Here, Run it |
| File map | Files, Layout, Repository layout, Scripts |
| Honest limits | Known limits, Limits, Notes / caveats, Notes on scope, Honest notes, Honesty notes, Constraints |
| Code appendix | Under the hood, Show me the code, Appendix |
| Presenter preparation | Pre-flight, Presenter Preparation, Presenter prep, Before you start |

Two generations of structure exist. The earlier pattern demos share a strong demo-script
skeleton and a light README. The newer standalone demos share a strong README
(`What runs where`, `Set up from scratch`, `Before a demo`, `Known limits`), a
`verify_demo.py`, a `SPEC.md` and a looser script. The standard takes the README shape
from the newer group and the script shape from the earlier group.

## 3. Goals and non-goals

Goals:

1. A new demo starts from a template and has every core asset before its first PR merges.
2. Any README answers the same questions in the same place.
3. A presenter who has run one demo script can pick up any other.
4. One command says whether a demo is ready to present.
5. Adding a demo touches one manifest, not four lists.
6. Drift fails CI.

Non-goals:

- No rewrite of demo content, talk tracks or code behaviour. Migration moves and renames
  sections and fills in the missing ones.
- No renames of service names, ports, compose profiles, Langfuse project names, seeded
  artifact names, existing workflow files or their dispatch event types. Langfuse
  automations fire the prompt gates by event type, and docs link the workflow files by
  name.
- No renames of existing script files. The naming rules in §9.7 apply to new demos.
- Not a code style guide. SDK and tracing conventions stay in `CLAUDE.md`.

## 4. Profiles

A demo declares one profile. The profile decides which assets are required.

| Profile | Definition | Demos |
|---|---|---|
| `container` | Runs as a service in the root `docker-compose.yaml` under the `demo` profile and uses the root `.env` | `text-to-sql`, `vector-rag`, `agentic-rag`, `support-triage-parallel`, `query-router`, `cluster-health-investigator`, `slow-query-tuner`, `litellm-gateway` |
| `standalone` | Brings its own toolchain and `.env`, and usually its own Langfuse project | `real-estate`, `brand-promo-multi-agent`, `langfuse-rls`, `grocery-assistant`, `bank-assistant`, `insurance-claims`, `langfuse-operations`, `multi-agent-handoff`, `analytics-agentic-speed` |
| `workshop` | A kit that learners work through, led by a facilitator | `bank-assistant-workshop`, `marketing-agent-debugging` |

Folders whose names start with `_` (`_template`, `_shared`) are not demos and are skipped
by all tooling.

## 5. The contract

R is required. C is conditional (§9.6). A dash means it does not apply.

| Asset | `container` | `standalone` | `workshop` |
|---|---|---|---|
| `demo.yaml` (§6) | R | R | R |
| `README.md` in the standard shape (§7) | R | R | R |
| `DEMO_SCRIPT.md` in the standard shape (§8) | R | R | — |
| `WORKSHOP_GUIDE.md` (§8.6) | — | — | R |
| `.env.example` (§9.1) | — | R | R |
| One seed entry point (§9.2) | R when the demo needs anything created before it runs | R | R |
| `scripts/verify_demo.*` (§9.3) | R | R | R |
| `tests/` that run without services (§9.4) | R | R | R |
| A documented reset path (§9.5) | R | R | R |
| `SPEC.md` | C | R | R |
| Entry in the generated index (§10) | R | R | R |

Which file answers which question:

| Question | File |
|---|---|
| What is this and how do I run it? | `README.md` |
| What do I say and click, in what order? | `DEMO_SCRIPT.md` |
| Why is it built this way, and what was measured? | `SPEC.md` and `docs/` |
| Is it ready to present right now? | `scripts/verify_demo.*` |
| What does CI gate, and how is that set up? | `cicd/README.md` |

## 6. The manifest: `demo.yaml`

One file at the root of each demo folder. Unknown keys are an error, so typos surface.

| Key | Required | Value |
|---|---|---|
| `name` | yes | The folder name, kebab-case |
| `title` | yes | Display name, as used in the README title |
| `pitch` | yes | One sentence, 160 characters at most, no trailing period. Becomes the index's "What it shows" cell |
| `profile` | yes | `container`, `standalone` or `workshop` |
| `status` | yes | `active`, `draft` or `archived`. Draft demos are tagged in the index. Archived demos are left out of it. All checks apply regardless |
| `covers` | yes | Ordered list from: `trace`, `monitor`, `datasets`, `experiment`, `evaluate`, `deploy`, `gateway`, `governance`, `operations` |
| `stack` | yes | Short list, such as `python`, `langgraph`, `mcp` |
| `langfuse.target` | yes | `self-hosted`, `cloud`, `either` or `dual` |
| `langfuse.project` | yes | The project name the demo writes to. `verify_demo` checks the keys map to it |
| `length.full_min` | yes | Minutes for the full script, such as `15-20` |
| `length.short_min` | no | Minutes for the short path |
| `commands.run` | yes | The one command that shows the demo working |
| `commands.verify` | yes | The readiness check (§9.3) |
| `commands.test` | yes | The no-services test run (§9.4) |
| `commands.install` | no | Dependency install, used by CI before `test` |
| `commands.seed` | no | The seed entry point (§9.2). Omit only when nothing needs seeding |
| `commands.reset` | no | The reset script, when one exists (§9.5) |
| `scripts` | no | Extra script variants: a list of `file`, `title`, `length_min` |
| `addons` | no | Declared conditional assets (§9.6) |
| `waivers` | no | A list of `rule`, `reason`, and optional `until` date. See §12 |
| `index_order` | no | Integer to pin a position in the index. Default is by title within the profile |

Working directory for commands: `install` and `test` always run from the demo folder.
`seed`, `run`, `verify` and `reset` run from the repo root for `container` demos and from
the demo folder otherwise. This matches how each is typed today.

Both examples show the target state. Scripts marked "to be written" do not exist yet, and
project names need confirming against the demo.

Example for a container demo:

```yaml
name: support-triage-parallel
title: Support Triage Parallel
pitch: Parallel ticket triage with 4-branch sectioning and best-of-N SQL voting
profile: container
status: active
covers: [trace, monitor, experiment, evaluate]
stack: [python, anthropic-sdk, asyncio]
langfuse:
  target: self-hosted
  project: default            # replace with the root stack's real project name
length:
  full_min: 15-20
  short_min: 8
commands:
  install: pip install -r requirements.txt
  seed: python demos/support-triage-parallel/scripts/seed_all.py
  run: docker compose --profile demo run --rm support-triage-parallel python main.py
  verify: python demos/support-triage-parallel/scripts/verify_demo.py   # to be written
  test: python -m pytest -q
```

Example for a standalone demo with add-ons and a waiver:

```yaml
name: insurance-claims
title: Northwind Life & Annuity claims
pitch: Death-claims agents checked against an SME answer key, with a prompt CI gate
profile: standalone
status: active
covers: [trace, monitor, datasets, experiment, evaluate, deploy, governance]
stack: [python, langgraph, strands, a2a, mcp]
langfuse:
  target: dual
  project: insurance-claims   # replace with the real project name
length:
  full_min: 50
commands:
  install: uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
  seed: .venv/bin/python scripts/seed_all.py      # to be written: wraps the four seed_*.py scripts
  run: ./scripts/run_services.sh
  verify: .venv/bin/python scripts/verify_demo.py
  test: .venv/bin/python -m unittest discover -s tests -t .
  reset: .venv/bin/python scripts/reset_hero.py
addons: [spec, cicd, capability-matrix]
waivers:
  - rule: DS-SCRIPT-04
    reason: Acts predate the labelled-beat format; reshape tracked in the migration backlog
    until: 2027-01-31
```

## 7. README standard

### 7.1 Opening block

Everything above the first `##` heading:

1. `# <Title> — <one-line pitch>`.
2. One to three short paragraphs: what the app is and who the fictional company is. Say
   that the company is fictional and the data synthetic when that is the case.
3. `**The question it answers:**` followed by one question, or a numbered list of up to
   three.
4. The generated demo card (§10.2).
5. A link list: `**Present it:**` the demo script with its length, then any of
   `**Design record:**`, `**CI gate:**`, `**Measured numbers:**` that exist.

### 7.2 Sections

Headings are `##`, spelled exactly as below, in this order. A heading may carry a suffix
after a dash or in brackets.

| # | Heading | Required | Must contain |
|---|---|---|---|
| 1 | `What it demonstrates` | yes | Bullets or a table mapping each capability to where it shows up |
| 2 | `Who it's for` | yes | The audience, and when to pick this demo over a neighbouring one |
| 3 | `What runs where` | yes | A diagram (ASCII or image), each process with its port, and what lands in Langfuse: trace name, session rule, tags |
| 4 | `Set up from scratch` | yes | Prerequisites in one sentence, install, env, seed, and the verify command as the last step. States that shell-exported `LANGFUSE_*` keys are ignored or must be unset |
| 5 | `Before a demo` | yes | What to run and when (day before, 30 minutes before), the tabs to open, and the verify command |
| 6 | `Run it` | yes | The commands for the main scenarios, then a "What you see in Langfuse" paragraph |
| 7 | `Evaluation` | when `covers` includes `datasets`, `experiment` or `evaluate` | Evaluators, datasets, experiments and any gate, with the command for each |
| — | demo-specific sections | no | Anything else: seeded faults, trace shape, safety rails. Allowed only here, between `Run it` and `Configuration` |
| 8 | `Configuration` | yes | A table of environment variables: name, default, effect |
| 9 | `Layout` | yes | An annotated file tree |
| 10 | `Tests` | yes | The command, what is covered, and that no services are needed |
| 11 | `Troubleshooting` | yes | A table: symptom, cause and fix |
| 12 | `Known limits` | yes | A candid list of what the demo does not do or prove |

### 7.3 Rules

- The README is not the talk track, the design rationale or a long reference. Those go in
  `DEMO_SCRIPT.md`, `SPEC.md` and `docs/`. Aim for 250 lines or fewer and link out.
- Every command sits in a fenced `bash` block, with a trailing comment giving the result,
  duration or cost when it is not obvious. State the cost and time of anything over a
  minute or about fifty cents.
- A measured number names the command that produced it, or links to
  `docs/MEASUREMENTS.md`.
- Write a new or reshaped README from the code and a live run, not from older docs.
  Several existing docs describe behaviour that has since changed.
- No real customer names anywhere in a demo folder. Fictional companies only.

### 7.4 Workshop variant

A `workshop` README uses the same sections. `Before a session` is accepted in place of
`Before a demo`, and the link list points to `WORKSHOP_GUIDE.md` instead of a demo script.

## 8. Demo script standard

### 8.1 Header block

Everything above the first `##` heading:

1. `# <Title> — Demo Script`, optionally with the angle in brackets.
2. One paragraph: what the audience sees.
3. A bullet list with these bold labels: `**App:**`, `**Observability:**` (Langfuse target,
   project, trace name), `**Models:**`, `**Run length:**` (full, and the short path).
   `**Data:**` and `**Prompts:**` are optional.
4. A blockquote pointing to the README for setup.

### 8.2 Sections

Headings are `##`, in this order, separated by `---` rules.

| # | Heading | Required | Must contain |
|---|---|---|---|
| 1 | `How to run this script` | yes | That it is a conversation, the four beats, and which acts form the short path |
| 2 | `0 · Pre-flight` | yes | Commands to run before the meeting, including the `unset LANGFUSE_...` line and the verify command, then the tabs to have open. A `### The day before` subsection is allowed |
| 3 | `What each act proves` | yes | A table: capability, where in the demo |
| 4 | `Opening · <title> (N min)` | yes | No screen. A Frame, two or three Ask questions, and a Land that previews the demo |
| 5 | `Act N · <title> (N min)` | at least two | See §8.3. Optional acts read `Act N · <title> (optional, N min)` |
| 6 | `Close · <title> (N min)` | yes | Three takeaways and the hand-over of the repo as an asset |
| 7 | `Show me the code` | yes | Numbered snippets. Each gives what it does, the `file:line` it lives at, and why it matters |
| 8 | `Talking points & objections` | yes | Each objection in bold as the audience would say it, then the answer |
| 9 | `Known limits` | yes | Three to six limits phrased for the room, with a link to the README's full list |
| 10 | `Reset / re-run` | yes | Commands to return to a clean state and to re-run |

### 8.3 Act format

Each act has four labelled beats, as bold run-in labels at the start of a paragraph, in
this order:

- `**Frame.**` The problem in the audience's terms. Said before touching the screen.
- `**Show.**` The exact clicks or commands, and what to read on screen.
- `**Land.**` The benefit, in a sentence the presenter can say aloud.
- `**Ask.**` An open question that maps the act to the audience's own stack.

An act may end with `> **Fallback:**` for what to do when the live step misbehaves. An act
may repeat Show and Land, but the first occurrences keep the order above.

### 8.4 Rules

- The voice is consultative: lead with the benefit, and let the Ask steer which act gets
  the time. Follow the structure without naming a method for it, and keep sales jargon out
  of these public files.
- Candour is part of the style. Limits appear as presenter beats, not footnotes.
- Verify every `file:line` citation against the code before committing. The checker
  confirms the file exists and the line is within it (§12).
- Minutes in act headings add up to the header's run length.
- Sections scale with the demo. A 5-minute script may have two-line sections, but every
  heading is present so presenters find things in the same place.

### 8.5 Variants

A second script for a different audience or length is named `DEMO_SCRIPT_<VARIANT>.md`,
follows the same skeleton, and is listed under `scripts` in the manifest.

### 8.6 Workshop guide

`WORKSHOP_GUIDE.md` is the facilitator's run of show. Required headings: `Before the
session` (with the verify command), `Run of show` (a table of time, module, outcome), one
`## Module N · <title> (N min)` per module, `Known limits`, `Reset / re-run`. Where
instructor notes already live under `docs/`, the guide may be a short page that links to
them. One-page presenter and participant handouts are optional add-ons.

## 9. Asset contracts

### 9.1 Environment

- `standalone` and `workshop` demos commit a `.env.example`. Its first line says to copy it
  to `.env`. Variables are grouped under comment headers, secrets are blank, and every
  variable the code reads is present.
- The demo loads its own `.env` and ignores shell-exported `LANGFUSE_*` keys. A key for
  another project otherwise sends traces there without an error.
- `container` demos use the root `.env`. Any new key goes into the root `.env.example` and
  the README's `Configuration` table.

### 9.2 Seed

- One entry point, `scripts/seed_all.py` or the toolchain's equivalent, named in
  `commands.seed`. It may call other seeders, including ones in the root `scripts/`.
- Idempotent, and it reconciles drift: a changed definition is updated, not skipped
  because something with that name exists.
- Prints what was created, updated and left unchanged, and exits non-zero on failure.
- The README says whether `./setup.sh` runs it. A seeder that nothing calls is a defect.

### 9.3 Readiness check

`scripts/verify_demo.py`, or `.ts` or `.sh` for other toolchains, named in
`commands.verify`.

- Reads state back and counts real rows. An HTTP 200 is not proof: a trace can exist
  before its observations land.
- Prints one line per check, marked pass, fail or warn. Checks for optional acts warn.
- Ends with `READY` or `NOT READY` and a count. Exits 0 when no required check fails, 1
  otherwise.
- `--links` prints the link each act opens.
- Makes no model calls by default and finishes in seconds. Live or costly checks sit
  behind a flag and say what they cost.
- Minimum checks: the keys map to `langfuse.project`; local services are up; every seeded
  artifact exists with the expected counts and labels; a recent trace exists with
  observations and its expected scores; anything a specific act depends on is in place.
- Uses the current read APIs named in `CLAUDE.md`. The legacy trace endpoints are gone on
  Langfuse Cloud.
- The template ships a self-contained version with the `check()` helper inline, so it runs
  in any demo's environment without a shared import.

### 9.4 Tests

- A `tests/` folder. Tests use no network, no Langfuse, no model calls and no Docker, and
  finish in under a minute.
- Minimum coverage: the demo's deterministic logic, such as evaluators, gates, parsers and
  policy checks. A script with real logic, such as `verify_demo`, gets tests too.
- New Python demos use pytest, with `pytest.ini` and `requirements-dev.txt` or a dev
  dependency group in `pyproject.toml`. New Node demos expose `npm test`. An existing demo
  keeps its runner; `commands.test` is what CI calls.
- CI runs `commands.install` then `commands.test` (§11).

### 9.5 Reset

- The script's `Reset / re-run` section is always present.
- When a rehearsal can leave state behind, such as moved prompt labels, a ledger or a
  half-filled queue, the demo ships `scripts/reset_demo.*` and names it in `commands.reset`.

### 9.6 Conditional add-ons

Declared under `addons`. The checker confirms a declared add-on's files exist.

| Add-on | Files | Use when |
|---|---|---|
| `spec` | `SPEC.md` | Always for `standalone` and `workshop`. Optional for `container`. Written before the build and kept as the design record |
| `cicd` | `cicd/README.md`, `cicd/thresholds.json`, a workflow named `<name>-prompt-gate.yml` | `covers` includes `deploy` and the demo shows a CI gate |
| `capability-matrix` | `CAPABILITY_MATRIX.md` with columns Area, Capability, How it is shown, Status, Where | The demo is built against a list of requirements |
| `architecture` | `docs/ARCHITECTURE.md` | More than two processes, or tracing across processes |
| `measurements` | `docs/MEASUREMENTS.md`, each number with its command | The README or script quotes measured numbers |
| `questions` | `DEMO_QUESTIONS.md` | The presenter types inputs live and needs a tested bank of them |
| `labs` | `LABS.md` | Hands-on exercises accompany the demo |
| `portal` | `scripts/run_portal.sh` | The demo has its own UI |

### 9.7 Naming, for new demos

- Folder: kebab-case.
- Top-level docs: upper snake case, such as `DEMO_SCRIPT.md`.
- Files under `scripts/`: snake_case, such as `seed_all.py`, `verify_demo.py`,
  `run_services.sh`.
- Prompt-gate workflow: `<name>-prompt-gate.yml`.

## 10. Registration and the generated index

### 10.1 Index

`scripts/gen-demo-index.py` renders the table in `demos/README.md` between the markers
`<!-- demo-index:start -->` and `<!-- demo-index:end -->`. One table per profile, with the
columns Demo, What it shows, Covers, Length, Run, Present. The hand-written text around
the table explains the profiles and the shared platform and names no individual demo.

`--check` exits non-zero when the committed file differs from the generated output.

### 10.2 Demo card

The same script renders a card into each README between `<!-- demo-card:start -->` and
`<!-- demo-card:end -->`:

```markdown
| | |
|---|---|
| **Covers** | Trace · Monitor · Experiment · Evaluate |
| **Stack** | Python · Anthropic SDK · asyncio |
| **Runs as** | Container in the root stack (`demo` profile) |
| **Langfuse** | Self-hosted · project `default` |
| **Length** | 15–20 min · short path 8 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
```

### 10.3 Other lists

| Place | Change |
|---|---|
| `docs/README.md`, "I want to present it" | Replace the per-script rows with one row that points to the generated index. Keep the rows for the field guide, use cases and runbooks |
| `CLAUDE.md`, project layout | Replace the per-demo lines with one line that points to `demos/README.md` |
| `.agents/skills/run-demo/SKILL.md` | Pick the demo from the generated index instead of a fixed table of three |
| CI matrices | Built from the manifests (§11) |
| `AI_ENGINEERING_LOOP.md`, `docs/USE_CASES.md`, `docs/SA_FIELD_GUIDE.md` | Stay hand-written. Updating them is a checklist item in `CONTRIBUTING.md`, not a CI rule |

After this, registering a demo means filling in `demo.yaml` and running the generator.

## 11. What to build

| # | Deliverable | Notes |
|---|---|---|
| 1 | `demos/CONTRIBUTING.md` | §4 to §10 of this spec, rewritten as instructions, plus the definition of done below |
| 2 | `demos/_template/` | `demo.yaml`, `README.md`, `DEMO_SCRIPT.md`, `SPEC.md`, `.env.example`, `scripts/seed_all.py`, `scripts/verify_demo.py`, `tests/test_example.py`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`. Placeholders use `{{TOKEN}}` |
| 3 | `scripts/new-demo.py` | `new-demo.py <name> --profile <profile>` copies the template, fills the tokens and drops files the profile does not need |
| 4 | `.agents/skills/new-demo/SKILL.md` and the `.claude/skills/new-demo` symlink | Steps: collect name, profile, pitch, coverage and Langfuse target; scaffold; write `SPEC.md` and confirm it; build; write the README from a live run; write the script after a rehearsal; run the checker and generator |
| 5 | `scripts/check-demo-structure.py` | §12. Python 3.11, PyYAML only |
| 6 | `scripts/gen-demo-index.py` | §10 |
| 7 | A `demo.yaml` for every demo | Bootstrap waivers record today's gaps (§12.2) |
| 8 | `demo-integrity.yml` changes | See below |
| 9 | `CLAUDE.md` and `AGENTS.md` pointers | "Adding or changing a demo: follow `demos/CONTRIBUTING.md`", the `new-demo` skill, and the layout change in §10.3 |

CI changes in `.github/workflows/demo-integrity.yml`:

- Trigger on any change under `demos/**`, and on the two new scripts.
- New job `demo-structure`: run the checker, then `gen-demo-index.py --check`.
- New job `template-self-test`: scaffold a demo from the template into a temp folder and
  run the checker on it with waivers disabled.
- `deps-resolve`: build the matrix from the manifests instead of the hand-kept list.
- New job `demo-tests`: for each demo the PR touches, run `commands.install` then
  `commands.test`. Run all demos on pushes to `main` and on manual dispatch.

Definition of done for a new demo, for `CONTRIBUTING.md`:

- The checker passes with no waivers.
- `commands.verify` prints `READY` on a fresh setup.
- Tests pass in CI.
- The script has been rehearsed once end to end from a clean state, and the minutes in its
  headings were measured.
- The index is regenerated and committed.

## 12. The checker

### 12.1 Rules

Every direct subfolder of `demos/` whose name does not start with `_` or `.` is a demo.
Headings are read outside fenced code blocks only: a `#` comment inside a `bash` block is
not a heading.

| Rule | Checks |
|---|---|
| `DS-MANIFEST-01` | `demo.yaml` exists, parses, has every required key and no unknown key |
| `DS-MANIFEST-02` | `name` equals the folder name and is kebab-case |
| `DS-README-01` | `README.md` exists |
| `DS-README-02` | Every required section in §7.2 is present for the profile |
| `DS-README-03` | Standard sections are in order, and other sections sit only between `Run it` and `Configuration` |
| `DS-README-04` | The demo card is present and matches the generator's output |
| `DS-README-05` | The README links to the demo script or workshop guide |
| `DS-SCRIPT-01` | `DEMO_SCRIPT.md` exists (`WORKSHOP_GUIDE.md` for workshops) |
| `DS-SCRIPT-02` | The header block has a `**Run length:**` line |
| `DS-SCRIPT-03` | Every required section in §8.2 is present and in order |
| `DS-SCRIPT-04` | At least two acts, each with Frame, Show, Land and Ask in order |
| `DS-SCRIPT-05` | Pre-flight contains the `unset LANGFUSE_PUBLIC_KEY` line and the verify command |
| `DS-ENV-01` | `.env.example` exists for `standalone` and `workshop` |
| `DS-ENV-02` | No `.env` file other than `.env.example` is tracked by git |
| `DS-SEED-01` | `commands.seed` is set and the script it names exists, unless the profile is `container` and nothing is seeded |
| `DS-VERIFY-01` | `commands.verify` is set and the script it names exists |
| `DS-TEST-01` | `commands.test` is set and at least one test file exists |
| `DS-SPEC-01` | `SPEC.md` exists for `standalone` and `workshop` |
| `DS-ADDON-01` | Each declared add-on's files exist |
| `DS-INDEX-01` | The generated block in `demos/README.md` is up to date |
| `DS-LINK-01` | Relative links in the README and script resolve to files that exist |
| `DS-LINK-02` | Each `file:line` citation in `Show me the code` names an existing file and a line within it (warning only) |

Command line:

```bash
python scripts/check-demo-structure.py                    # every demo
python scripts/check-demo-structure.py --demo real-estate # one demo
python scripts/check-demo-structure.py --strict           # ignore waivers, for an audit
python scripts/check-demo-structure.py --format github    # annotations for CI; also text, json
```

It exits 1 on any failure that is not waived.

### 12.2 Waivers

A waiver in `demo.yaml` names a rule and gives a reason. The checker reports waived rules
as notices. A waiver with an `until` date fails after that date.

`check-demo-structure.py --write-waivers` records every current failure as a waiver with
the reason "predates the standard". Run it once when the manifests are first added. CI is
then green on day one, new demos get no waivers, and the waiver list is the migration
backlog.

## 13. Migration backlog

From the heading and file checks in §2. Size is S for renames and short additions, M for
new sections that need a live check, L for a new README or new verify and tests. The
checker's first run replaces this table with the exact list.

Two changes apply to almost every script and are not repeated in the rows. `Known limits`
is a new section everywhere except `query-router`, because today limits are mostly said
inside the acts (decision D9). `Under the hood` becomes `Show me the code` and `Closing`
becomes `Close` wherever they appear. "Has the skeleton" below means only those two
changes are needed.

| Demo | README | Script | Other assets | Size |
|---|---|---|---|---|
| `text-to-sql` | Write it | Has the skeleton | `seed_all` wrapper over the existing seeders; `verify_demo` | L |
| `vector-rag` | Write it | Has the skeleton | `seed_all` wrapper; `verify_demo`; first tests | L |
| `agentic-rag` | Write it, linking the architecture doc and runbook in `docs/` | Has the skeleton | `seed_all` over ingest, prompts, judges and the LibreChat agent; `verify_demo`; decide where `SESSION_GROUPING_SPEC.md` lives | L |
| `support-triage-parallel` | Rename `Quick start` and `Files`. Add `What runs where` (from `Trace shape`), `Before a demo`, `Known limits` | Has the skeleton | `verify_demo` | M |
| `query-router` | Add `What runs where`, `Before a demo`, `Configuration`, `Tests`, `Troubleshooting`, `Known limits` | Add `How to run this script`, `What each act proves`, `Opening`, `Close`, `Show me the code`, `Talking points & objections`, `Reset / re-run`. `Honesty notes` becomes `Known limits`. Act headings get `·` and minutes | Confirm the seed entry point; `verify_demo` | M |
| `cluster-health-investigator` | Add `What runs where`, `Before a demo`, `Layout`, `Troubleshooting`, `Known limits` | Add `What each act proves` and `Show me the code`. Add minutes to act headings | `verify_demo`; declare `DEMO_SYMPTOMS.md` as the questions add-on | M |
| `slow-query-tuner` | Rename `Files`, `Testing`, `Notes / caveats`. Add `What runs where`, `Before a demo`, `Troubleshooting` | Add `What each act proves`, `Close`, `Talking points & objections`, `Reset / re-run`. The appendix becomes `Show me the code`. Label the beats | `verify_demo` | M |
| `litellm-gateway` | Add `What it demonstrates`, `Who it's for`, `Set up from scratch`, `Before a demo`, `Layout`, `Tests`, `Troubleshooting`, `Known limits`. `What is configured` becomes `Configuration` | Add short `0 · Pre-flight`, `What each act proves`, `Opening`, `Close`, `Talking points & objections`, `Known limits`, `Reset / re-run` | Wrap the trace check in `run_demo.sh` as `verify_demo` | M |
| `real-estate` | Add `Who it's for`, `Before a demo`, `Configuration`, `Tests`, `Troubleshooting`, `Known limits`. `How the pieces fit` becomes `What runs where`, `Files` becomes `Layout`. At 486 lines, move depth to `docs/` | Has the skeleton. Fold `Leave-behind` into `Close` | One `verify_demo` over `smoke_test.py` and the `verify_*.py` scripts; a `tests/` folder; `SPEC.md` or a waiver | L |
| `brand-promo-multi-agent` | Add `What runs where`, `Before a demo`, `Run it`, `Configuration`, `Layout`, `Tests`, `Troubleshooting`, `Known limits`. Remove the stale "after implementer build is done" and `Status` | Has the skeleton | `verify_demo` | M |
| `langfuse-rls` | `What it demos` becomes `What it demonstrates`. Add `What runs where`, `Before a demo`, `Configuration`, `Layout`, `Tests`, `Known limits` | Has the skeleton | Bring `verify.sh` to the §9.3 contract; first tests (`npm test` is wired to vitest with no test files) | M |
| `grocery-assistant` | Add `What it demonstrates`, `Who it's for`, `What runs where`, `Before a demo`, `Configuration`, `Tests`, `Troubleshooting`. `Notes on scope` becomes `Known limits` | Add `How to run this script`, `What each act proves`, `Opening`, `Close`, `Show me the code`, `Reset / re-run`. `Before you start` becomes `0 · Pre-flight`, `Questions you will get` becomes `Talking points & objections`, `What not to do` becomes `Known limits`. Label the beats | `.env.example` (today a script writes `.env`); `verify_demo`; more tests | L |
| `bank-assistant` | `Start Here`, `Prerequisites` and `Quick Start` become `Set up from scratch`. Split `Services and Troubleshooting`. `Limits` becomes `Known limits`. Add `What it demonstrates`, `Who it's for`, `What runs where`, `Before a demo`, `Run it`, `Configuration`, `Layout`, `Tests` | The 30-minute script reorders to the skeleton. The 2-hour script is module-shaped: see decision D3 | Already has verify, tests and `.env.example` | M |
| `insurance-claims` | Add `What it demonstrates`, `Who it's for`, `Before a demo`, `Run it`, `Configuration`, `Tests`, `Troubleshooting`. `Scripts` becomes `Layout` | Add `How to run this script`, `0 · Pre-flight`, `What each act proves`, `Close`, `Show me the code`, `Talking points & objections`, `Reset / re-run`. Prefix acts with `Act`. Label the beats | Index entry is missing today; `seed_all` wrapper; tests cover the oracle only | M |
| `bank-assistant-workshop` | Add the standard sections. `Repository layout` becomes `Layout`, `Constraints` becomes `Known limits`. Remove the unreviewed-draft checklist at the end | `WORKSHOP_GUIDE.md` as a page linking the instructor notes | `verify_demo` wrapping `workshop.py preflight`; index entry is missing today | M |
| `langfuse-operations` | Reshape to the standard sections | The script has one heading today: build out the skeleton | Env templates exist under other names: add `.env.example` or waive | M |
| `multi-agent-handoff` | Close to the standard. Add `What it demonstrates`, `Who it's for`, `Configuration`, `Tests` | Numbered acts become `Act N ·`. Add `How to run this script`, `What each act proves`, `Talking points & objections`, `Reset / re-run`. Appendix B becomes `Show me the code` | All present | S |
| `analytics-agentic-speed` | Close to the standard. Add `What it demonstrates`, `Who it's for`, `Configuration` | Pre-flight becomes its own `##`. Add `What each act proves`, `Opening`, `Close`, `Talking points & objections`, `Reset / re-run`. `Honest limits` becomes `Known limits` | All present | S |
| `marketing-agent-debugging` | `What's in the box` becomes `What it demonstrates`, `Honest notes` becomes `Known limits`, `Files` becomes `Layout`. Add the rest | `WORKSHOP_GUIDE.md` exists. The one-page script and cheat sheet stay as add-ons | All present | S |

The last four rows were read from `main` or open branches by heading only.

## 14. Rollout

| Phase | Work | Done when |
|---|---|---|
| 0 | Deliverables 1 to 9 in §11, in one PR: standard, template, scaffolder, skill, checker, generator, manifests with bootstrap waivers, generated index, CI jobs `demo-structure` and `template-self-test` | CI is green; a scaffolded demo passes with no waivers; the index lists every demo on `main` |
| 1 | Dynamic `deps-resolve` matrix and the `demo-tests` job | Every demo's tests run in CI; failures are fixed or the gap is waived with a reason |
| 2 | One PR per demo: README, script and assets together, removing that demo's waivers | The demo passes with no waivers, `verify` prints `READY` on a fresh setup, and the script has been rehearsed |
| 3 | Close-out | No waiver without an `until` date remains, except those decided as permanent |

Phase 2 goes by demo, not by asset, because writing a README, a readiness check and tests
all need the same understanding of the demo.

### 14.1 Order of the migration PRs

Most recently active first. A demo's activity date is the later of two dates: its last
commit on any branch, and its last local run. A local run is the newest `.pyc`, `.run/`
file, log or SQLite file under the demo in any worktree. Ties go to the later commit, then
to the demo with no README.

Measured on 2026-11-26. "Open PRs" counts open pull requests that change the demo's README
or script.

| # | Demo | Last commit | Last local run | Open PRs | Size |
|---|---|---|---|---|---|
| 1 | `analytics-agentic-speed` | 2026-11-26 | 2026-11-26 | 0; merged to `main` that day (#122) | S |
| 2 | `bank-assistant` | 2026-11-19 | 2026-11-26 | 6: #105, #107, #108, #110, #117, #119 | M |
| 3 | `insurance-claims` | 2026-10-29 | 2026-11-26 | 2: #107, #112 | M |
| 4 | `langfuse-operations` | 2026-11-25 | none found | 1: #121, which renames the folder to `langfuse-enterprise-operations` | M |
| 5 | `multi-agent-handoff` | 2026-11-22 | 2026-11-22 | 1: #118, its own draft; not on `main` | S |
| 6 | `brand-promo-multi-agent` | 2026-11-11 | 2026-11-10 | 0 | M |
| 7 | `marketing-agent-debugging` | 2026-11-03 | none found | 1: #113, its own draft; not on `main` | S |
| 8 | `real-estate` | 2026-10-29 | 2026-10-28 | 2: #107, #109 | L |
| 9 | `bank-assistant-workshop` | 2026-10-20 | 2026-10-12 | 0 | M |
| 10 | `grocery-assistant` | 2026-10-11 | none found | 0 | L |
| 11 | `text-to-sql` | 2026-10-10 | 2026-08-31 | 0 | L |
| 12 | `litellm-gateway` | 2026-10-10 | none found | 0 | M |
| 13 | `langfuse-rls` | 2026-07-13 | 2026-10-05 | 0 | M |
| 14 | `agentic-rag` | 2026-09-03 | 2026-08-31 | 0 | L |
| 15 | `vector-rag` | 2026-09-03 | 2026-08-31 | 0 | L |
| 16 | `support-triage-parallel` | 2026-09-03 | 2026-08-31 | 0 | M |
| 17 | `query-router` | 2026-09-03 | 2026-08-31 | 0 | M |
| 18 | `cluster-health-investigator` | 2026-09-03 | 2026-08-31 | 0 | M |
| 19 | `slow-query-tuner` | 2026-09-03 | 2026-08-31 | 0 | M |

Rows 14 to 19 tie on both dates: their last commit is one repo-wide PR. The two without a
README go first, and the order of the last four is arbitrary.

The most recent demos are also the ones with work in flight, so three rules apply:

- A demo that is not on `main` yet (rows 5, 7) is migrated on its own branch, inside its
  open PR, not in a separate PR against `main`.
- A demo with open PRs that change its README or script (rows 2, 3, 4, 8) has those
  merged or rebased first. Otherwise the heading moves conflict with them.
  `bank-assistant` has six; decide their merge order before starting its migration.
  #107 changes three demos at once.
- Row 4 uses whichever folder name is on `main` when its migration starts.

What this order costs: the three demos with no README land at rows 11, 14 and 15. Their
missing index entries and CI coverage are still fixed in phase 0, which does not depend on
this order.

The table is a snapshot. Re-measure before starting phase 2:

```bash
git log -1 --all --format=%cs -- demos/<name>          # last commit on any branch
gh pr list --state open --json number,headRefName      # open PRs and their branches
git diff --name-only origin/main...origin/<branch> -- 'demos/<name>/README.md' 'demos/<name>/DEMO_SCRIPT*.md'   # does that PR touch them?
```

After phase 0 merges, each open demo PR rebases and adds its `demo.yaml`. Bootstrap
waivers are allowed there too.

Risks:

| Risk | Mitigation |
|---|---|
| Renaming headings breaks anchors that other docs link to | `DS-LINK-01`, and a repo-wide search for the old anchor in each migration PR |
| The generated index loses the curated order | `index_order`, and hand-written text around the table |
| The standard is too heavy for a small demo | Sections scale down (§8.4); waivers carry a reason |
| Readiness checks rot, since CI has no live Langfuse | Unit-test their parsing logic; they run before every demo by design |
| Per-demo installs slow CI | `demo-tests` runs only the demos a PR touches |
| Other sessions and branches work in the same checkout | Do each phase on its own branch or worktree |

## 15. Decisions

Each has a default so the build can start. Change any before phase 0.

| # | Decision | Default | Alternative |
|---|---|---|---|
| D1 | Manifest and generated index | Yes | Hand-kept index with a checker rule that each demo appears in it |
| D2 | `SPEC.md` | Required for `standalone` and `workshop`, optional for `container` | Required for all, or for none |
| D3 | `bank-assistant`'s 2-hour script | Keep the file and waive the act rules. The 30-minute script is the conforming one | Move it to `WORKSHOP_GUIDE.md` and make the 30-minute script `DEMO_SCRIPT.md`, which changes links |
| D4 | Workshop guide file name | `WORKSHOP_GUIDE.md` | Keep each kit's own names and waive |
| D5 | `CLAUDE.md` and `docs/README.md` | Point to the generated index | Keep the lists and check them in CI |
| D6 | Canonical section names | `Set up from scratch`, `Show me the code`, `Known limits`, `Layout` | `Quick start`, `Under the hood`. Either way about half the files change |
| D7 | Template language | One Python template; `CONTRIBUTING.md` says what to swap for TypeScript | A second template, once a third TypeScript demo appears |
| D8 | Existing file names (`run_demo.sh`, `verify.sh`, `DEMO_SYMPTOMS.md`) | Keep them and point the manifest at them | Rename to the §9.7 names and fix links |
| D9 | `Known limits` section in the demo script | Required, three to six bullets, since it is what a presenter new to the demo reads first | Optional, with limits kept inside the acts as today |

## Appendix A. README skeleton

````markdown
# {{TITLE}} — {{PITCH}}

{{What the app is, in one to three short paragraphs. Name the fictional company and say
the data is synthetic.}}

**The question it answers:** {{one question}}

<!-- demo-card:start -->
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about {{N}} minutes)
- **Design record:** [SPEC.md](SPEC.md)

## What it demonstrates

| Capability | Where it shows up |
|---|---|

## Who it's for

## What runs where

```
{{diagram}}
```

## Set up from scratch

```bash
cd demos/{{NAME}}
{{install}}
cp .env.example .env        # then fill in the keys
{{seed}}                    # idempotent
{{verify}}                  # should print READY
```

The `.env` is the only source of `LANGFUSE_*`. Shell-exported keys are ignored.

## Before a demo

| When | Do | Why |
|---|---|---|

## Run it

```bash
{{run}}
```

**What you see in Langfuse:** {{trace name, shape, scores}}

## Evaluation

## Configuration

| Variable | Default | Effect |
|---|---|---|

## Layout

```
{{tree}}
```

## Tests

```bash
{{test}}                    # no services, no model calls
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|

## Known limits
````

## Appendix B. Demo script skeleton

````markdown
# {{TITLE}} — Demo Script

{{One paragraph: what the audience sees.}}

- **App:** {{what the customer-facing surface is, and its URL}}
- **Observability:** {{Langfuse target, project, trace name}}
- **Models:** {{agent and judge models}}
- **Run length:** {{full}} min; short path is Acts {{…}} ({{short}} min)

> Setup lives in [README.md](README.md).

---

## How to run this script

It's a conversation, not a walkthrough. Every act does four things:

- **Frame** — the problem, in their terms. Say it before you touch the screen.
- **Show** — the exact clicks and commands.
- **Land** — the benefit, not the feature.
- **Ask** — an open question that maps it to their world.

---

## 0 · Pre-flight

```bash
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
{{start services}}
{{verify}} --links          # READY, plus the link for every act
```

**Tabs to open:** {{list}}

---

## What each act proves

| Capability | Where in the demo |
|---|---|

---

## Opening · {{title}} (2 min)

**Frame.**

**Ask.**

**Land.**

---

## Act 1 · {{title}} ({{N}} min)

**Frame.**

**Show.**

**Land.**

**Ask.**

> **Fallback:**

---

## Close · {{title}} (1 min)

---

## Show me the code

**1 · {{what it does}} — `{{file}}:{{line}}`**

---

## Talking points & objections

- **"{{objection}}"** {{answer}}

---

## Known limits

---

## Reset / re-run

```bash
{{reset}}
```
````

## Appendix C. Heading rename map

| Today | Standard |
|---|---|
| Quick start, Quick Start, Setup, Start Here, Prerequisites | `Set up from scratch` |
| Running it | `Run it` |
| Files, Repository layout, Scripts | `Layout` |
| Configuration (env), What is configured | `Configuration` |
| Testing | `Tests` |
| Services and Troubleshooting | `Troubleshooting`, with services moved to `What runs where` |
| Limits, Known limits (say them out loud), Notes / caveats, Notes on scope, Honest notes, Constraints | `Known limits` |
| What it demos, What's in the box | `What it demonstrates` |
| Who this is for | `Who it's for` |
| How the pieces fit, Trace shape, The trace shape | `What runs where`, or a demo-specific section |
| Pre-flight (do this BEFORE the meeting), Presenter prep, Before you start | `0 · Pre-flight` |
| Closing, Leave-behind | `Close` |
| Under the hood, Appendix (code) | `Show me the code` |
| Honesty notes, Honest limits, What not to do | `Known limits` |
| Questions you will get, Backup topics | `Talking points & objections` |
