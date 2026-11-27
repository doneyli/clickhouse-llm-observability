# {{TITLE}} — {{PITCH}}

{{What the app is, in one to three short paragraphs. Name the fictional company and say
the data is synthetic.}}

**The question it answers:** {{one question the audience brings, such as "how do we know
the agent is right before a customer sees it?"}}

<!-- demo-card:start -->
<!-- demo-card:end -->

<!-- @if container standalone -->
- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 15 minutes, 9-minute short path)
<!-- @endif -->
<!-- @if workshop -->
- **Present it:** [WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md), the facilitator's run of show (about 90 minutes)
<!-- @endif -->
<!-- @if standalone workshop -->
- **Design record:** [SPEC.md](SPEC.md)
<!-- @endif -->

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| Tracing with Langfuse SDK v4 | One `{{NAME}}` trace per run: a root span with a generation under it (`main.py`) |
| Prompt management | `{{NAME}}-answer`, served by the `production` label and seeded by `scripts/seed_all.py` |
| {{capability}} | {{act, screen or command where it shows up}} |

## Who it's for

{{The audience, such as platform engineers evaluating observability, and when to pick this
demo over a neighbouring one in the demo index, `demos/README.md`.}}

## What runs where

```
{{diagram: replace with the real processes and ports}}
 you ── main.py (Python 3.11) ── answer(): the model call
              │
              ▼  one Langfuse client, flushed before exit
 Langfuse project `{{PROJECT}}`
   trace `{{NAME}}` · session `{{NAME}}-<8 hex>` per run · tag `{{NAME}}` · score `answered`
```

## Set up from scratch

<!-- @if container -->
Prerequisites: Docker and the root stack, set up with `./setup.sh`. Run these from the
repo root:

```bash
./setup.sh                          # the root stack and its .env (idempotent)
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
{{CMD_SEED}}       # idempotent: created / updated / unchanged per item
{{CMD_RUN}}   # one fresh trace
{{CMD_VERIFY}}     # should print READY
```

The root `.env` is the only source of `LANGFUSE_*`. Compose resolves `${...}` from the
shell first, so unset shell-exported keys before running; `verify_demo.py` and
`seed_all.py` ignore them. `./setup.sh` does not run this demo's seed yet: {{say whether
it does once it is wired in}}.
<!-- @endif -->
<!-- @if standalone workshop -->
Prerequisites: Python 3.11 and a Langfuse project named `{{PROJECT}}` with its API keys.

```bash
cd demos/{{NAME}}
{{CMD_INSTALL}}
cp .env.example .env        # then fill in the keys
{{CMD_SEED}}   # idempotent: created / updated / unchanged per item
{{CMD_RUN}}             # one fresh trace
{{CMD_VERIFY}} # should print READY
```

The `.env` is the only source of `LANGFUSE_*`. Shell-exported keys are ignored.
`./setup.sh` does not run this demo's seed.
<!-- @endif -->

<!-- @if container standalone -->
## Before a demo
<!-- @endif -->
<!-- @if workshop -->
## Before a session
<!-- @endif -->

| When | Do | Why |
|---|---|---|
| The day before | `{{CMD_SEED}}`, then `{{CMD_RUN}}` | Seeded artifacts match the code, and a fresh trace exists |
| 30 minutes before | `{{CMD_VERIFY}} --links` | Prints READY and the link each act opens |
| 5 minutes before | Open the links it printed | {{the tabs the first act needs}} |

## Run it

```bash
{{CMD_RUN}}                                  # one run with the default question
{{CMD_RUN}} --question "{{a question}}"      # one run with your own
```

**What you see in Langfuse:** a `{{NAME}}` trace with input and output on the root span, an
`answer` generation linked to the `{{NAME}}-answer` prompt version, and an `answered`
score. {{Replace with the real trace shape and scores.}}

<!-- @if evaluation -->
## Evaluation

| What | Command | Notes |
|---|---|---|
| {{evaluator, dataset, experiment or gate}} | `{{command}}` | {{cost and time when over a minute or about fifty cents}} |

<!-- @endif -->
## Configuration

| Variable | Default | Effect |
|---|---|---|
<!-- @if container -->
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | from the root `.env` | Keys for the `{{PROJECT}}` project |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Where the host-side scripts read and seed; the container uses `LANGFUSE_INTERNAL_URL` |
<!-- @endif -->
<!-- @if standalone workshop -->
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | blank | Keys for the `{{PROJECT}}` project |
| `LANGFUSE_BASE_URL` | `{{BASE_URL}}` | Langfuse region or host |
<!-- @endif -->
| `ANTHROPIC_API_KEY` | blank | Model key for `answer()`, once it calls a model |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` | Project name `verify_demo.py` expects the keys to map to |

## Layout

```
demos/{{NAME}}/
├── demo.yaml               manifest: profile, coverage, commands (feeds the index)
├── README.md               this file
<!-- @if container standalone -->
├── DEMO_SCRIPT.md          the talk track, act by act
<!-- @endif -->
<!-- @if workshop -->
├── WORKSHOP_GUIDE.md       the facilitator's run of show
<!-- @endif -->
<!-- @if standalone workshop -->
├── SPEC.md                 design record, written before the build
├── .env.example            copy to .env
<!-- @endif -->
<!-- @if container -->
├── Dockerfile              image for the root compose `demo` profile
<!-- @endif -->
├── main.py                 one traced run (Langfuse SDK v4)
├── scripts/
│   ├── seed_all.py         the one seed entry point (idempotent, reconciles drift)
│   └── verify_demo.py      readiness check: READY or NOT READY
├── tests/                  no services, no model calls
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

## Tests

```bash
cd demos/{{NAME}}
{{CMD_INSTALL}}
{{CMD_TEST}}                    # no services, no model calls, a few seconds
```

They cover `verify_demo.py`'s check, summary and exit-code logic, the `.env` override, the
manifest parse, and `seed_all.py`'s reconcile decision, against a fake Langfuse.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `keys map to project '…' (want '{{PROJECT}}')` | The keys belong to another project. Put this project's keys in the `.env`; shell exports are ignored |
| `keys authenticate …: HTTP 401` | Wrong key pair, or `LANGFUSE_BASE_URL` points at another region or host |
| `a '{{NAME}}' run in the last 7 days` fails | Nothing has run yet: `{{CMD_RUN}}` |
| `latest run has 0 observation(s)` | The trace landed before its observations. Wait 30 seconds and run the check again |

## Known limits

- `answer()` in `main.py` is a placeholder until it calls a model. {{Remove once it does.}}
- {{What the demo does not do or prove, candidly.}}
