# {{TITLE}} — Spec

**Status:** draft · **Written:** {{DATE}}

Write this before the build and confirm it with whoever asked for the demo. Afterwards it
stays as the design record: why the demo is built this way, and what was measured.

## 1. The question it answers

{{The one question the audience brings, and why it matters to them.}}

## 2. Audience and setting

{{Who is in the room, how long they have, and which neighbouring demo they would otherwise
see.}}

## 3. What the demo must prove

| # | Claim | How it is shown | Acceptance |
|---|---|---|---|
| 1 | {{claim}} | {{act and screen}} | {{what verify_demo.py checks, or the measurement}} |

## 4. The fictional company and data

{{Company name and domain, both fictional. Where the data comes from, and that it is
synthetic.}}

## 5. Architecture

```
{{processes, ports, and the one Langfuse client per process}}
```

## 6. What lands in Langfuse

| Object | Name | Rule |
|---|---|---|
| Trace | `{{NAME}}` | One per run; root observation carries input and output |
| Session | `{{NAME}}-<8 hex>` | {{one per run, or one per conversation}} |
| Prompt | `{{NAME}}-answer` | Label `production`, seeded by `scripts/seed_all.py` |
| Score | `answered` | {{what writes it}} |
| {{dataset, evaluator, dashboard}} | {{name}} | {{rule}} |

## 7. Act plan

| Act | Minutes | What it shows | Depends on |
|---|---|---|---|
| 1 | {{minutes}} | {{…}} | {{seeded artifact or fresh run, checked by verify_demo.py}} |

## 8. Measurements

{{Each number the README or script will quote, with the command that produces it.}}

## 9. Risks and open questions

- {{…}}

## 10. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | {{…}} | {{…}} | {{…}} |
