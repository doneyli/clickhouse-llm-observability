# {{TITLE}} — Demo Script

{{One paragraph: what the audience sees. For the template as shipped: one traced run of a
small assistant, then a prompt change that reaches the next run without a deploy.}}

- **App:** {{the customer-facing surface and its URL; as shipped, `main.py` in a terminal}}
- **Observability:** Langfuse {{TARGET}}, project `{{PROJECT}}`, trace `{{NAME}}`
- **Models:** {{agent and judge models; as shipped, a placeholder answer()}}
- **Run length:** 15 min; short path is Act 1 (9 min)

> Setup lives in [README.md](README.md).

---

## How to run this script

It's a conversation, not a walkthrough. Every act does four things:

- **Frame** — the problem, in their terms. Say it before you touch the screen.
- **Show** — the exact clicks and commands.
- **Land** — the benefit, not the feature.
- **Ask** — an open question that maps it to their world.

The Ask answers tell you which act gets the time. The short path is the Opening, Act 1
and the Close.

---

## 0 · Pre-flight

<!-- @if container -->
From the repo root:

```bash
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
docker compose --profile langfuse up -d          # the root stack
{{CMD_RUN}}   # one fresh trace to open live
{{CMD_VERIFY}} --links          # READY, plus the link for every act
```
<!-- @endif -->
<!-- @if standalone -->
```bash
cd demos/{{NAME}}
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
{{CMD_RUN}}                  # one fresh trace to open live
{{CMD_VERIFY}} --links # READY, plus the link for every act
```
<!-- @endif -->

`verify_demo.py` exits 1 when a required check fails. Fix what it names before the meeting.

**Tabs to open:** the latest trace and the prompt, both printed by `--links`. {{Add the rest.}}

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| Tracing: one trace per run, input and output on the root | Act 1 |
| Prompt management: a label decides what production serves | Act 2 |

---

## Opening · Where answers come from (2 min)

**Frame.** {{The problem in the room's terms, before any screen. For example: "Once an
assistant is live, the first question after a bad answer is which prompt and which input
produced it."}}

**Ask.** {{Two or three open questions, such as "How do you find out today why one answer
went wrong?" and "Who can change a prompt, and how long does that take?"}}

**Land.** {{A sentence that previews the demo: "Let me show you one run end to end, then a
prompt change that reaches production without a deploy."}}

---

## Act 1 · One run, end to end (6 min)

**Frame.** {{The problem this act answers, in the audience's words.}}

**Show.** Run `{{CMD_RUN}}`, then open the trace link from `--links`. Point at the root
span's input and output, the `answer` generation under it, the prompt version it links to,
and the `answered` score. {{Replace with the exact clicks and what to read on screen.}}

**Land.** {{The benefit, as a sentence you can say aloud: "Every answer carries its input,
its prompt version and its score, so a bad one takes a click to explain."}}

**Ask.** {{An open question that maps the act to their stack: "Where would this sit in
your own request path?"}}

> **Fallback:** if the trace is not there yet, open the latest one from the Tracing list;
> observations can take a few seconds to land.

---

## Act 2 · Change the prompt without a deploy (6 min)

**Frame.** {{The problem: a prompt fix waits for a release.}}

**Show.** Open the `{{NAME}}-answer` prompt, create a new version, move the `production`
label to it, and run `{{CMD_RUN}}` again. The new trace links to the new version.
{{Replace with the exact clicks.}}

**Land.** {{"The people who own the wording can change it, and every trace says which
version answered."}}

**Ask.** {{"Who changes prompts on your team today, and what review do they go through?"}}

> **Fallback:** if the label move does not show, the client caches prompts for a short
> time: wait a minute and run again.

---

## Close · What to take away (1 min)

1. {{First takeaway, tied to Act 1.}}
2. {{Second takeaway, tied to Act 2.}}
3. {{Third takeaway: what they can do next.}}

Hand over the repo: `demos/{{NAME}}` runs on their own Langfuse project with the steps in
the README.

---

## Show me the code

**1 · One root observation per run, carrying the run's input and output — `main.py:56`**
The root span is opened first and updated with the overall input and output at
`main.py:63`. SDK v4 derives the trace's input and output from it.

**2 · Trace name, session and tags on every observation — `main.py:57`**
`propagate_attributes` sets them once, so child observations carry them too.

**3 · Flush before exit — `main.py:65`**
A short-lived process otherwise exits before its spans are sent.

**4 · Readiness counts real rows — `scripts/verify_demo.py:286`**
It finds the latest root observation through Observations API v2, then counts that trace's
observations and reads its scores through Scores API v3. An HTTP 200 alone is not proof.

---

## Talking points & objections

- **"Isn't this just logging?"** {{Answer: logs are lines; a trace keeps the steps of one
  request together with their inputs, outputs, cost and scores.}}
- **"{{objection}}"** {{answer}}

---

## Known limits

- `answer()` is a placeholder until it calls a model. {{Remove once it does.}}
- {{Three to six limits, phrased for the room.}}

The full list is in the README's [Known limits](README.md#known-limits).

---

## Reset / re-run

<!-- @if container -->
```bash
{{CMD_SEED}}       # puts the prompt back: a new version with the seeded text takes the label
{{CMD_RUN}}   # a fresh trace
{{CMD_VERIFY}}     # READY again
```
<!-- @endif -->
<!-- @if standalone -->
```bash
{{CMD_SEED}}   # puts the prompt back: a new version with the seeded text takes the label
{{CMD_RUN}}             # a fresh trace
{{CMD_VERIFY}} # READY again
```
<!-- @endif -->
