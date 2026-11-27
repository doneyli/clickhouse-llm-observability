---
name: new-demo
description: Start a new demo under demos/ or bring an existing one up to the repo's demo standard — scaffold from demos/_template, write SPEC.md and get it agreed, build, write the README from a live run and the demo script after a rehearsal, then pass check-demo-structure.py and regenerate the demo index. Use when the user says "add a demo", "new demo", "build a demo for X", "scaffold a demo", "make this demo conform", "fix the demo-structure check", or when a demo's demo.yaml, README, DEMO_SCRIPT.md, verify_demo or tests need creating or reshaping.
---

# Build a New Demo

The standard is [demos/CONTRIBUTING.md](../../../demos/CONTRIBUTING.md). Read it before
step 1 and follow it; this skill only fixes the order of work and the points where you
stop and ask. Skeletons live in [demos/_template/](../../../demos/_template/). Do not copy
structure from an older demo: many predate the standard.

For an existing demo, start from `python scripts/check-demo-structure.py --demo <name>
--strict`, skip the scaffold, and work through steps 4 to 8 for whatever it reports
(step 3 too if a `standalone` or `workshop` demo has no `SPEC.md`). Remove each waiver you
fix. Never add a waiver to get past the checker, and never rename service names, ports, profiles, Langfuse project
names or workflow files (CONTRIBUTING.md > Change an existing demo).

## 1. Collect the inputs

Ask for, or confirm from context, before touching files:

| Input | Notes |
|---|---|
| `name` | kebab-case folder name, not already under `demos/` |
| `profile` | `container`, `standalone` or `workshop` (CONTRIBUTING.md > Pick a profile). Most new demos are `standalone` |
| `title` and `pitch` | Pitch is one sentence, at most 160 characters, no trailing period |
| `covers` | Ordered, from `trace`, `monitor`, `datasets`, `experiment`, `evaluate`, `deploy`, `gateway`, `governance`, `operations` |
| Langfuse target and project | `self-hosted`, `cloud`, `either` or `dual`, and the project name it writes to |
| Language | Python by default. For TypeScript, follow CONTRIBUTING.md > TypeScript demos after scaffolding |
| Audience and length | Who it is for, and the full and short-path minutes to aim for |

Use a fictional company. Never put a real customer name in the demo folder.

## 2. Scaffold

```bash
python scripts/new-demo.py <name> --profile <profile> --title "<title>" --pitch "<pitch>"
python scripts/check-demo-structure.py --demo <name>
```

The scaffolder copies the template, fills the tokens, drops files the profile does not
need and regenerates the index (`--no-index` skips that). Fill `covers`, `stack`,
`langfuse` and `length` in `demo.yaml` now.

## 3. Write SPEC.md, then stop

Write `demos/<name>/SPEC.md`: what the demo proves and for whom, the scenarios and the
trace shape, the Langfuse artifacts it seeds (prompts, datasets, evaluators, queues,
dashboards), what is out of scope, and open decisions with a default for each.

**Show the spec to the user and wait for a clear yes before building.** Revise it until
they agree. Building against an unagreed spec is the costliest mistake in this flow.

## 4. Build

Build the app and, alongside it, the assets in CONTRIBUTING.md > Build the assets:

- `scripts/seed_all.py`: idempotent, reconciles drift, prints created/updated/unchanged.
- `scripts/verify_demo.py`: reads state back, counts real rows, ends with `READY` or
  `NOT READY`, supports `--links`, makes no model calls by default.
- `tests/`: no network, Langfuse, model calls or Docker; cover the deterministic logic.
- `.env.example` listing every variable the code reads, for `standalone` and `workshop`.
- `scripts/reset_demo.py` if a rehearsal leaves state behind.

Follow the SDK and read-API conventions in [CLAUDE.md](../../../CLAUDE.md).

Before any live run or verify, clear shell keys. Shell exports outrank `.env` in compose
and send traces to another project without an error:

```bash
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
```

## 5. Write the README from a live run

Run seed, run and verify for real (`commands.*` in `demo.yaml`; container demos run them
from the repo root, others from the demo folder). Then write the README in the section
order from CONTRIBUTING.md > Write the README, using what you saw: real trace names, real
ports, real commands, real costs and durations. Do not write it from the spec or from
older docs.

## 6. Write the script after a rehearsal

Rehearse the demo end to end once from a clean state (reset, seed, verify, run), timing
each act. Then write `DEMO_SCRIPT.md` (or `WORKSHOP_GUIDE.md` for a workshop) in the
skeleton from CONTRIBUTING.md > Write the demo script, with the minutes you measured.
Check every `file:line` in `Show me the code` against the code. Keep the voice
consultative and plain, and do not name a sales method.

If you cannot rehearse it yourself (no API key, no Langfuse access), say so to the user,
write the script from the live run in step 5, and leave the rehearsal as an open item.

## 7. Check and register

```bash
python scripts/check-demo-structure.py --demo <name> --strict   # must pass with no waivers
python scripts/gen-demo-index.py                                 # index table + demo card
python scripts/gen-demo-index.py --check
(cd demos/<name> && <commands.test>)                             # no services needed
```

Then go through the hand-written checklist in CONTRIBUTING.md > Register the demo
(`AI_ENGINEERING_LOOP.md`, `docs/USE_CASES.md`, `docs/SA_FIELD_GUIDE.md`) and update the
pages the demo changes.

## 8. Definition of done

Report each item to the user as done or not done, with evidence:

- [ ] The checker passes with no waivers.
- [ ] `commands.verify` prints `READY` on a fresh setup.
- [ ] The tests pass (and in CI once the PR is open).
- [ ] The script was rehearsed end to end from a clean state, and its minutes measured.
- [ ] The index is regenerated and committed.

Do not commit, push or open a PR unless the user asks.
