# {{TITLE}} — Workshop Guide

{{One paragraph: who the learners are, what they build, and what they leave with.}}

- **Format:** {{hands-on, learners on their own laptops, one facilitator per N learners}}
- **Observability:** Langfuse {{TARGET}}, project `{{PROJECT}}`, trace `{{NAME}}`
- **Run length:** 90 min

> Setup lives in [README.md](README.md). Where instructor notes already live under
> `docs/`, this guide may stay short and link to them.

---

## Before the session

```bash
cd demos/{{NAME}}
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
{{CMD_SEED}}   # idempotent
{{CMD_RUN}}             # one fresh trace
{{CMD_VERIFY}} --links # READY, plus the link for every module
```

{{The day before: accounts, keys and projects for learners, and the room setup.}}

---

## Run of show

| Time | Module | Outcome |
|---|---|---|
| 0:00 | Module 1 · First trace | Every learner has a `{{NAME}}` trace in their project |
| 0:45 | Module 2 · {{title}} | {{what learners can do afterwards}} |
| 1:30 | End | {{the hand-over}} |

---

## Module 1 · First trace (45 min)

**Goal.** {{What learners can do at the end of the module.}}

**Steps.** {{Numbered steps, with the commands learners type: `{{CMD_RUN}}`, then open the
trace.}}

**Check.** {{How the facilitator confirms every learner got there.}}

---

## Module 2 · {{title}} (45 min)

**Goal.** {{…}}

**Steps.** {{…}}

**Check.** {{…}}

---

## Known limits

- `answer()` is a placeholder until it calls a model. {{Remove once it does.}}
- {{What the workshop does not cover, phrased for the room.}}

The full list is in the README's [Known limits](README.md#known-limits).

---

## Reset / re-run

```bash
{{CMD_SEED}}   # puts seeded artifacts back to their definitions
{{CMD_VERIFY}} # READY again
```
