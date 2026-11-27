# Contributing a demo

This is the standard every folder under `demos/` follows. Use it when you start a demo or
change one. [`docs/DEMO_STANDARD_SPEC.md`](../docs/DEMO_STANDARD_SPEC.md) is the design
record: why the standard looks like this and what the repo looked like before it. When
the two disagree, this file wins.

[`_template/`](_template/) is the source of truth for every skeleton named below
(`demo.yaml`, `README.md`, `DEMO_SCRIPT.md`, `SPEC.md`, `.env.example`, the seed and
verify scripts, the tests). Copy from it, not from an older demo.
`scripts/check-demo-structure.py` enforces the rules in CI. Code conventions (SDK version,
read APIs, key handling) live in [`CLAUDE.md`](../CLAUDE.md).

## Start a new demo

Coding agents: follow the [`new-demo` skill](../.agents/skills/new-demo/SKILL.md). It runs
the steps below in order.

```bash
python scripts/new-demo.py <name> --profile standalone \
  --title "Northwind Widgets" --pitch "One sentence, no trailing period"
python scripts/check-demo-structure.py --demo <name>   # a fresh scaffold passes; keep it that way
```

The scaffolder copies `demos/_template/`, fills the `{{TOKEN}}` placeholders, drops the
files the profile does not need and regenerates the index. Pass `--no-index` to skip the
regeneration.

Then, in this order: write `SPEC.md` (what it proves, for whom, the scenarios, what is out
of scope) and get it agreed before you build; build the app, seed, readiness check and
tests; write the README from the code and a live run, not from older docs; rehearse once
from a clean state and write `DEMO_SCRIPT.md` with the minutes you measured; run the
checker and the generator and work through [Register the demo](#register-the-demo).

## Change an existing demo

- Run `python scripts/check-demo-structure.py --demo <name>` before and after.
- Do not add waivers. When your change fixes a waived rule, delete that waiver.
- Do not rename service names, ports, compose profiles, Langfuse project names, seeded
  artifact names, workflow files or their dispatch event types. Langfuse automations fire
  the prompt gates by event type, and other docs link the workflow files by name.
- Do not rename existing script files; the naming rules below are for new files. When you
  rename a heading to its standard name (Appendix C of the spec has the map), search the
  repo for links to the old anchor and fix them.
- Bring a demo up to the standard in one PR per demo: README, script and assets together.

## Pick a profile

A demo declares exactly one profile, and the profile decides which assets are required.

- `container`: runs as a service in the root `docker-compose.yaml` under the `demo`
  profile and uses the root `.env`.
- `standalone`: brings its own toolchain and `.env`, and usually its own Langfuse project.
- `workshop`: a kit that learners work through, led by a facilitator.

Folders whose names start with `_` or `.` (`_template`, `_shared`) are not demos. All
tooling skips them.

## Required assets

R is required, C is conditional on an add-on, a dash means it does not apply.

| Asset | `container` | `standalone` | `workshop` |
|---|---|---|---|
| `demo.yaml` | R | R | R |
| `README.md` in the standard shape | R | R | R |
| `DEMO_SCRIPT.md` in the standard shape | R | R | — |
| `WORKSHOP_GUIDE.md` | — | — | R |
| `.env.example` | — | R | R |
| One seed entry point | R if anything must exist before it runs | R | R |
| `scripts/verify_demo.*` | R | R | R |
| `tests/` that run without services | R | R | R |
| A documented reset path | R | R | R |
| `SPEC.md` | C | R | R |
| Entry in the generated index | R | R | R |

Keep each answer in its own file. `README.md`: what is this and how do I run it.
`DEMO_SCRIPT.md`: what do I say and click, in what order. `SPEC.md` and `docs/`: why is it
built this way, and what was measured. `scripts/verify_demo.*`: is it ready to present right
now. `cicd/README.md`: what CI gates, and how that is set up.

## Write `demo.yaml`

One file at the root of the demo folder. Unknown keys are an error. See
[`_template/demo.yaml`](_template/demo.yaml) for the full shape.

| Key | Value |
|---|---|
| `name`, `title` | The folder name in kebab-case; the display name used in the README title |
| `pitch` | One sentence, at most 160 characters, no trailing period. Becomes the index's "What it shows" cell |
| `profile` | `container`, `standalone` or `workshop` |
| `status` | `active`, `draft` (tagged in the index) or `archived` (left out of it). Every check applies either way |
| `covers` | Ordered list from `trace`, `monitor`, `datasets`, `experiment`, `evaluate`, `deploy`, `gateway`, `governance`, `operations` |
| `stack` | Short list, such as `python`, `langgraph`, `mcp` |
| `langfuse` | `target`: `self-hosted`, `cloud`, `either` or `dual`. `project`: the project it writes to, which the readiness check confirms the keys map to |
| `length` | `full_min`, such as `15-20`; `short_min` if there is a short path |
| `commands` | Required: `run`, `verify`, `test`. Optional: `install`, `seed` (omit only when nothing needs seeding), `reset` |
| optional | `scripts` (variants as `file`, `title`, `length_min`), `addons`, `waivers`, `index_order` (pins a position in the index) |

**Working directory.** `install` and `test` always run from the demo folder. `seed`, `run`,
`verify` and `reset` run from the repo root for `container` demos and from the demo folder
for the others. Write each command so it works from that directory.

## Write the README

Above the first `##` heading, in this order:

1. `# <Title> — <one-line pitch>`.
2. One to three short paragraphs: what the app is and who the company is. Say when the
   company is fictional and the data synthetic.
3. `**The question it answers:**` and one question, or a numbered list of up to three.
4. The demo card markers. The generator fills them; never edit between them by hand.
5. A link list: `**Present it:**` the demo script with its length, then whichever of
   `**Design record:**`, `**CI gate:**` and `**Measured numbers:**` exist.

Then `##` headings spelled exactly like this, in this order. A heading may carry a suffix
after a dash or in brackets.

| # | Heading | Required | Put in it |
|---|---|---|---|
| 1 | `What it demonstrates` | yes | Each capability and where it shows up |
| 2 | `Who it's for` | yes | The audience, and when to pick this demo over a neighbouring one |
| 3 | `What runs where` | yes | A diagram, each process with its port, and what lands in Langfuse: trace name, session rule, tags |
| 4 | `Set up from scratch` | yes | Prerequisites in one sentence, install, env, seed, and the verify command last. Say that shell-exported `LANGFUSE_*` keys are ignored, or that they must be unset |
| 5 | `Before a demo` | yes | What to run and when (day before, 30 minutes before), the tabs to open, the verify command |
| 6 | `Run it` | yes | The commands for the main scenarios, then a "What you see in Langfuse" paragraph |
| 7 | `Evaluation` | if `covers` has `datasets`, `experiment` or `evaluate` | Evaluators, datasets, experiments and any gate, with the command for each |
| — | your own sections | no | Seeded faults, trace shape, safety rails. Only here, between `Run it` and `Configuration` |
| 8 | `Configuration` | yes | A table of environment variables: name, default, effect |
| 9 | `Layout` | yes | An annotated file tree |
| 10 | `Tests` | yes | The command, what is covered, and that no services are needed |
| 11 | `Troubleshooting` | yes | A table of symptom, and cause and fix |
| 12 | `Known limits` | yes | A candid list of what the demo does not do or prove |

Rules:

- Keep the talk track, design rationale and long reference material out of the README.
  They go in `DEMO_SCRIPT.md`, `SPEC.md` and `docs/`. Aim for 250 lines or fewer.
- Put every command in a fenced `bash` block, with a trailing comment for the result,
  duration or cost when it is not obvious. Always state the cost and time of anything over
  a minute or about fifty cents.
- Name the command that produced each measured number, or link to `docs/MEASUREMENTS.md`.
- Use fictional companies only. No real customer names anywhere in a demo folder.

A `workshop` README uses the same sections. `Before a session` is accepted in place of
`Before a demo`, and the link list points to `WORKSHOP_GUIDE.md`.

## Write the demo script

Above the first `##` heading: `# <Title> — Demo Script` (optionally with the angle in
brackets); one paragraph on what the audience sees; bullets labelled `**App:**`,
`**Observability:**` (Langfuse target, project, trace name), `**Models:**` and
`**Run length:**` (full, and the short path), with `**Data:**` and `**Prompts:**` optional;
and a blockquote pointing to the README for setup.

Then these `##` headings in this order, separated by `---` rules:

| # | Heading | Required | Put in it |
|---|---|---|---|
| 1 | `How to run this script` | yes | That it is a conversation, the four beats, and which acts form the short path |
| 2 | `0 · Pre-flight` | yes | The `unset LANGFUSE_...` line, the commands to run before the meeting, the verify command, the tabs to open. A `### The day before` subsection is allowed |
| 3 | `What each act proves` | yes | A table of capability and where in the demo |
| 4 | `Opening · <title> (N min)` | yes | No screen. A Frame, two or three Ask questions, and a Land that previews the demo |
| 5 | `Act N · <title> (N min)` | at least two | Optional acts read `Act N · <title> (optional, N min)` |
| 6 | `Close · <title> (N min)` | yes | Three takeaways, and handing over the repo as an asset |
| 7 | `Show me the code` | yes | Numbered snippets: what it does, the `file:line` it lives at, why it matters |
| 8 | `Talking points & objections` | yes | Each objection in bold, as the audience would say it, then the answer |
| 9 | `Known limits` | yes | Three to six limits phrased for the room, linking to the README's full list |
| 10 | `Reset / re-run` | yes | Commands to return to a clean state and to re-run |

Give each act four beats as bold run-in labels at the start of a paragraph, in this order:

- `**Frame.**` The problem in the audience's terms, said before touching the screen.
- `**Show.**` The exact clicks or commands, and what to read on screen.
- `**Land.**` The benefit, in a sentence the presenter can say aloud.
- `**Ask.**` An open question that maps the act to the audience's own stack.

A label may carry a qualifier in brackets, such as `**Show (force a fail).**`, when an act
shows more than one thing.

End an act with `> **Fallback:**` when the live step can misbehave. An act may repeat Show
and Land, as long as the first occurrences keep the order above.

Rules:

- Lead with the benefit, and let the answers to each Ask decide which act gets the time.
  Follow the structure without naming a method for it, and keep sales jargon out.
- Say the limits as presenter beats, not footnotes.
- Check every `file:line` citation against the code before you commit.
- Make the minutes in the act headings add up to the header's run length, and measure
  them in a rehearsal.
- Scale sections down for a short demo, but keep every heading so presenters find things
  in the same place.
- Name a second script for another audience or length `DEMO_SCRIPT_<VARIANT>.md`, give it
  the same skeleton, and list it under `scripts` in `demo.yaml`.

**Workshop guide.** A `workshop` demo has `WORKSHOP_GUIDE.md` instead: the facilitator's
run of show. Required headings: `Before the session` (with the verify command),
`Run of show` (a table of time, module, outcome), one `## Module N · <title> (N min)` per
module, `Known limits`, `Reset / re-run`. If instructor notes already live under `docs/`,
the guide may be a short page that links to them. One-page handouts are optional.

## Build the assets

**Environment.** `standalone` and `workshop` demos commit `.env.example`. Its first line
says to copy it to `.env`. Group variables under comment headers, leave secrets blank, and
list every variable the code reads. The demo loads its own `.env` and ignores
shell-exported `LANGFUSE_*` keys, which otherwise send traces to another project without
an error. `container` demos use the root `.env`: add any new key to the root
`.env.example` and to the README's `Configuration` table. Never commit a `.env`.

**Seed.** One entry point, `scripts/seed_all.py` or your toolchain's equivalent, named in
`commands.seed`. It may call other seeders, including those in the root `scripts/`. Make it
idempotent and make it reconcile drift: update a changed definition instead of skipping it
because the name exists. Print what was created, updated and left unchanged, and exit
non-zero on failure. Say in the README whether `./setup.sh` runs it. A seeder that nothing
calls is a defect.

**Readiness check.** `scripts/verify_demo.py` (or `.ts`, `.sh`), named in
`commands.verify`. Start from the template's version, which has the `check()` helper
inline so it needs no shared import.

- Read state back and count real rows. An HTTP 200 is not proof: a trace can exist before
  its observations land.
- Print one line per check (pass, fail, or warn for optional acts), then `READY` or
  `NOT READY` with a count. Exit 0 when no required check fails, else 1. `--links` prints
  the link each act opens.
- Make no model calls by default and finish in seconds. Put live or costly checks behind a
  flag that says what they cost.
- Check at least: the keys map to `langfuse.project`; local services are up; every seeded
  artifact exists with the expected counts and labels; a recent trace exists with
  observations and its expected scores; anything a specific act depends on is in place.
- Use the current read APIs named in `CLAUDE.md`. The legacy trace endpoints are gone on
  Langfuse Cloud.

**Tests.** A `tests/` folder whose tests use no network, Langfuse, model calls or Docker,
and finish in under a minute. Cover the deterministic logic at least: evaluators, gates,
parsers, policy checks, and any script with real logic such as `verify_demo`. New Python
demos use pytest with `pytest.ini` and `requirements-dev.txt` (or a dev dependency group
in `pyproject.toml`). An existing demo keeps its runner. CI runs `commands.install` then
`commands.test`.

**Reset.** The script's `Reset / re-run` section is always there. When a rehearsal can
leave state behind, such as moved prompt labels, a ledger or a half-filled queue, ship
`scripts/reset_demo.*` and name it in `commands.reset`.

**Add-ons.** Declare them under `addons`. The checker confirms their files exist.

| Add-on | Files | Use it when |
|---|---|---|
| `spec` | `SPEC.md` | Always for `standalone` and `workshop`; optional for `container`. Written before the build, kept as the design record |
| `cicd` | `cicd/README.md`, `cicd/thresholds.json`, a workflow named `<name>-prompt-gate.yml` | `covers` has `deploy` and the demo shows a CI gate |
| `capability-matrix` | `CAPABILITY_MATRIX.md` with columns Area, Capability, How it is shown, Status, Where | The demo is built against a list of requirements |
| `architecture` | `docs/ARCHITECTURE.md` | More than two processes, or tracing across processes |
| `measurements` | `docs/MEASUREMENTS.md`, each number with its command | The README or script quotes measured numbers |
| `questions` | `DEMO_QUESTIONS.md` | The presenter types inputs live and needs a tested bank of them |
| `labs` | `LABS.md` | Hands-on exercises go with the demo |
| `portal` | `scripts/run_portal.sh` | The demo has its own UI |

An existing demo that keeps a file under another name (decision D8 in the spec) points
the add-on at it with a one-key mapping. The listed paths replace the defaults, relative
to the demo folder:

```yaml
addons:
  - spec
  - questions: DEMO_SYMPTOMS.md
  - cicd: [cicd/README.md, cicd/thresholds.json, ../../.github/workflows/claims-prompt-gate.yml]
```

New demos use the default names.

**Naming, for new files.** Folder in kebab-case. Top-level docs in upper snake case
(`DEMO_SCRIPT.md`). Files under `scripts/` in snake_case (`seed_all.py`, `verify_demo.py`,
`run_services.sh`). Prompt-gate workflow `<name>-prompt-gate.yml`.

## TypeScript demos

The template is Python. For a Node or TypeScript demo, scaffold it, then swap:

| Python template | TypeScript equivalent |
|---|---|
| `requirements.txt`, `requirements-dev.txt`, `pytest.ini` | `package.json` and a committed `package-lock.json`; add `tsconfig.json` |
| `tests/test_example.py` | `tests/*.test.ts`, run by `tsx --test` (node:test) or vitest |
| `scripts/seed_all.py`, `scripts/verify_demo.py` | `scripts/seed_all.ts`, `scripts/verify_demo.ts` with the same output contract and `--links` flag |
| `python-dotenv` | `dotenv` with `override: true`, so the demo's `.env` beats shell exports |

Expose every command as a `package.json` script and point the manifest at them:

```yaml
stack: [typescript, node, ...]
commands:
  install: npm ci
  seed: npm run seed           # tsx scripts/seed_all.ts
  run: npm run demo
  verify: npm run verify       # tsx scripts/verify_demo.ts
  test: npm test               # no services, no model calls
```

Load `.env` from the demo folder by path, not from the current directory, and hard-set
`LANGFUSE_*` from it before any Langfuse client starts. `grocery-assistant/src/env.ts` does
this. A provisioning script may fill in `.env` for you, but still commit `.env.example`
with every variable listed.

## Register the demo

```bash
python scripts/gen-demo-index.py          # renders the index table and each README's demo card
python scripts/gen-demo-index.py --check  # exits non-zero when the committed output is stale
```

The index in [`README.md`](README.md) is generated from the manifests: one table per
profile, with Demo, What it shows, Covers, Length, Run and Present. Do not edit between the
`demo-index` or `demo-card` markers. Commit the regenerated files with your change.

These pages stay hand-written. Check each one when you add a demo or change what one
covers. CI does not check them.

- [ ] [`AI_ENGINEERING_LOOP.md`](../AI_ENGINEERING_LOOP.md): which loop steps the demo shows.
- [ ] [`docs/USE_CASES.md`](../docs/USE_CASES.md): add or update the 2-minute paths the demo supports.
- [ ] [`docs/SA_FIELD_GUIDE.md`](../docs/SA_FIELD_GUIDE.md): when to pick this demo, and the objections it answers.

## Check it

```bash
python scripts/check-demo-structure.py                    # every demo
python scripts/check-demo-structure.py --demo <name>      # one demo
python scripts/check-demo-structure.py --strict           # ignore waivers, for an audit
python scripts/check-demo-structure.py --format github    # CI annotations; also text, json
```

It exits 1 on any failure that is not waived. Headings are read outside fenced code blocks
only, so a `#` comment in a `bash` block is not a heading.

| Rule | Fails when | Fix |
|---|---|---|
| `DS-MANIFEST-01` | `demo.yaml` is missing, does not parse, lacks a required key or has an unknown one | Compare with `_template/demo.yaml`; fix the typo or add the key |
| `DS-MANIFEST-02` | `name` is not the folder name or not kebab-case | Make `name` match the folder exactly |
| `DS-README-01` | `README.md` is missing | Copy `_template/README.md` and fill it from a live run |
| `DS-README-02` | A required README section is missing for the profile | Add the heading with the standard spelling; use the spec's Appendix C for renames |
| `DS-README-03` | Sections are out of order, or a custom section sits outside `Run it`…`Configuration` | Reorder; move custom sections between `Run it` and `Configuration` |
| `DS-README-04` | The demo card is missing or differs from the generator's output | Keep the `demo-card` markers and run `gen-demo-index.py` |
| `DS-README-05` | The README does not link to the script or workshop guide | Add the `**Present it:**` link in the opening block |
| `DS-SCRIPT-01` | `DEMO_SCRIPT.md` (or `WORKSHOP_GUIDE.md` for workshops) is missing | Copy it from `_template/` and write it after a rehearsal |
| `DS-SCRIPT-02` | The header block has no `**Run length:**` line | Add it with the measured full and short-path minutes |
| `DS-SCRIPT-03` | A required script section is missing or out of order | Add or reorder to match the sections table above |
| `DS-SCRIPT-04` | Fewer than two acts, or an act lacks Frame, Show, Land or Ask in order | Add the missing beat as a bold run-in label, or split the act |
| `DS-SCRIPT-05` | Pre-flight lacks the `unset LANGFUSE_PUBLIC_KEY` line or the verify command (workshops: `Before the session` lacks the verify command) | Add them to the `0 · Pre-flight` block, or to `Before the session` |
| `DS-ENV-01` | No `.env.example` in a `standalone` or `workshop` demo | Commit one that lists every variable the code reads |
| `DS-ENV-02` | A `.env` other than `.env.example` is tracked by git | `git rm --cached` it, rotate any key it held, add it to `.gitignore` |
| `DS-SEED-01` | `commands.seed` is unset or names a missing script | Add one seed entry point; container demos with nothing to seed omit the key |
| `DS-VERIFY-01` | `commands.verify` is unset or names a missing script | Start from `_template/scripts/verify_demo.py` |
| `DS-TEST-01` | `commands.test` is unset or no test file exists | Add `tests/` with at least the deterministic logic covered |
| `DS-SPEC-01` | No `SPEC.md` in a `standalone` or `workshop` demo | Write it; for a new demo it comes before the build |
| `DS-ADDON-01` | A declared add-on's files are missing | Add the files, or remove the add-on from `addons` |
| `DS-INDEX-01` | The generated block in `demos/README.md` is stale | Run `gen-demo-index.py` and commit the result |
| `DS-LINK-01` | A relative link in the README or script points to a missing file | Fix the path; links resolve from the file's own folder |
| `DS-LINK-02` | A `file:line` in `Show me the code` names a missing file or a line past its end (warning) | Re-read the code and update the citation |

## Waivers

A waiver in `demo.yaml` names a rule and gives a reason. The checker reports waived rules
as notices instead of failures.

```yaml
waivers:
  - rule: DS-SCRIPT-04
    reason: Acts predate the labelled-beat format; reshape tracked in the migration backlog
    until: 2027-01-31
```

- A waiver with an `until` date fails once that date passes. Give every new waiver one.
- `DS-MANIFEST-01` and `DS-MANIFEST-02` cannot be waived. A waiver whose rule no longer
  fails is reported as a warning: delete it.
- New demos get no waivers. CI's template self-test scaffolds a demo and checks it with
  waivers off; `--strict` does the same for any demo.
- `--write-waivers` records every current failure as a waiver with the reason "predates the
  standard". It was run once, when the manifests were added, so CI was green on day one. Do
  not run it to get a new demo or a change past CI.
- The waivers across all manifests are the migration backlog. Removing them is the work.

## Definition of done

A new demo is done when:

- [ ] `python scripts/check-demo-structure.py --demo <name>` passes with no waivers.
- [ ] `commands.verify` prints `READY` on a fresh setup.
- [ ] The tests pass in CI.
- [ ] The script has been rehearsed once end to end from a clean state, and the minutes in
      its headings were measured in that rehearsal.
- [ ] The index is regenerated and committed (`gen-demo-index.py --check` passes).
- [ ] The hand-written pages in [Register the demo](#register-the-demo) are checked.
