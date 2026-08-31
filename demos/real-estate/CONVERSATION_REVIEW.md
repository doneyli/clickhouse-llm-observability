# Human review of whole conversations

How a domain expert reviews a **multi-turn conversation** in Langfuse — not one
answer at a time — and where those labels land.

Companion to the automated cross-turn evaluation in
[README → "Evaluating the conversation, not just the turn"](README.md#evaluating-the-conversation-not-just-the-turn).
Everything here is seeded by
[`scripts/seed_annotation_queue.py`](scripts/seed_annotation_queue.py).

---

## Why a second queue instead of more items in the first

A constraint the buyer states once in turn 3 and the agent breaks in turn 9 looks
**perfectly fine in every individual trace**. A queue whose items are traces
structurally cannot surface it: the reviewer never sees turn 3 and turn 9
together.

An annotation queue item carries an `objectType`, and the enum is
`TRACE | OBSERVATION | SESSION`. That type is what the reviewer is shown — so a
queue of *conversations* is a queue whose items are sessions:

```
POST /api/public/annotation-queues/{queueId}/items
{"objectId": "<session_id>", "objectType": "SESSION", "status": "PENDING"}
```

This is also the **only human route to a session-scoped score**. No managed
evaluator can target a session, because Langfuse never learns that a conversation
ended (see README for the three app-side workarounds). From code you write one
with `create_score(session_id=…)`; from a person, you use this queue.

Two queues rather than one mixed queue: a single score schema would have to serve
both units, and the reviewer would switch units mid-queue.

## What gets created

| Queue | Items | Score configs | The reviewer sees |
|---|---|---|---|
| `Property Concierge - human review` | `TRACE` | `reviewer-verdict`, `expert-usefulness` | one turn |
| `Property Concierge - conversation review` | `SESSION` | `conversation-outcome`, `stated-constraint-respected`, `reference-resolved` | the whole conversation, turn by turn |

The session queue's schema is deliberate:

- **`conversation-outcome`** — CATEGORICAL: `resolved` / `partially-resolved` /
  `abandoned`. Human-only. Whether the buyer actually got anywhere is not a
  per-turn question, and no automated score in this demo answers it.
- **`stated-constraint-respected`**, **`reference-resolved`** — BOOLEAN, and
  **the same names the code evaluators and the conversation judge emit**
  (`agent/conversation_scoring.py`, `scripts/seed_managed_evaluators.sh`). Reused
  on purpose: the human label becomes a gold standard *for the machine's score*
  rather than a parallel vocabulary. Same concept, different `source` — see
  "Reading the labels back".

## Seeding it

```bash
cd demos/real-estate
./.venv/bin/python scripts/seed_annotation_queue.py                  # both queues
./.venv/bin/python scripts/seed_annotation_queue.py --only sessions  # conversations only
./.venv/bin/python scripts/seed_annotation_queue.py --min-turns 5 --max-sessions 3
```

Idempotent — re-running reuses the existing score configs and queues and only
adds items that aren't there yet. `--max-items 0 --max-sessions 0` is a
zero-write dry run that still prints what it *would* queue.

A session qualifies as a candidate when it has at least `--min-turns` turns
(default 3). Turn count is the number of **root observations** sharing the
`session_id` — one per turn, since every turn is its own trace — counting only
those named `handle-concierge-chat-message`. Both halves of that rule matter:

- **Length**, because a 2-turn session cannot contain a cross-turn failure, and
  the portal/smoke-test sessions that accumulate in a demo project are all short.
- **Name**, because verification scripts leave sessions behind too
  (`verify-multimodal` has 4 "turns" of its own spans) — long enough to qualify,
  useless to a reviewer.

Candidates are queued longest-conversation-first, recency breaking ties.

To get a conversation worth reviewing:

```bash
./.venv/bin/python scripts/run_live_traffic.py         # includes a 3-turn session
./.venv/bin/python scripts/simulate_long_session.py    # one 12-turn session
```

`run_demo.sh` seeds both queues at step 5 but does **not** run
`simulate_long_session.py` (12 turns + judges); run it first if you want the long
conversation in the queue for a demo.

## What the reviewer does

**Human Annotation → Property Concierge - conversation review → Process queue.**
The item opens the session — header `Total traces: 12`, every turn in order — with
the three score fields in the **Annotate** panel on the right. Processing is
keyboard-driven: `↑`/`↓` between fields, `1`–`9` to pick an option, `→` for the
next item, `Cmd/Ctrl+Enter` to complete and advance, `?` for the cheatsheet.

Reviewers can also **Add comment** on the session. To capture what the agent
*should* have said, open the offending turn and use its **Corrected Output**
field: a correction is a score with `name: "output"` and `dataType: "CORRECTION"`,
and it attaches to a **trace or observation**, never to a session — so it lands on
the turn that went wrong, which is exactly the granularity an N+1 dataset item
needs for a real `expected_output`. (`CORRECTION` is a score data type only; it is
not a valid score-*config* type, so it never appears in the queue's schema.)

Adding sessions by hand, any time, without the script: **Sessions** → select with
the checkboxes → **Actions → Add to queue**, or **Annotate** on a single session
page. (The equivalent control on the *Observations* tab can only add
observations — one reason the API route above exists.)

## Reading the labels back

Human labels are scores whose subject is the session, so
`GET /api/public/v3/scores` finds them by `sessionId`, `queueId`, `source`,
`authorUserId`, or `name`:

```bash
# every score on one conversation
GET /api/public/v3/scores?sessionId=sess-madrid-buyer-longconvo-002&fields=subject
# only what humans wrote in this queue
GET /api/public/v3/scores?source=ANNOTATION&queueId=<queueId>&fields=subject,details
```

`source` is `ANNOTATION` | `API` | `EVAL` — which is what makes the shared score
names useful rather than confusing. On a session that already carries the
deterministic aggregate, `session-grounded-turns = 1` comes back with
`source: API`; a reviewer's `stated-constraint-respected` on the same session
comes back with `source: ANNOTATION`. Same subject, same vocabulary, different
author — so "does the judge agree with the expert?" is a query, not a vibe.

That comparison is the point of the queue: a human-labelled set is what you
calibrate an LLM judge against before you trust it on 100% of traffic.

## API notes and gotchas

Verified against Langfuse Cloud v4.22.0, project `real-estate`.

- **`SESSION` is in the `AnnotationQueueObjectType` enum** alongside `TRACE` and
  `OBSERVATION`; the queue item renders the full session in the UI.
- **Filtering `v2/observations` by `sessionId` needs `stringOptions` / `"any of"`**
  (value = a list). `{"type": "string", "column": "sessionId", "operator": "="}`
  returns **200 with zero rows** — a silent empty result, so a per-session lookup
  reads as "that conversation has no turns". `root_observations_by_sessions()` in
  `agent/config.py` batches every id into one `any of` call instead.
- **Session discovery** uses the deprecated page-based `GET /api/public/sessions`
  (the only single-call answer to "which sessions exist?"; removed on Cloud
  2026-11-16, after which you group `v2/observations` rows by `sessionId` — which
  is what the helper already does for turn counts). Its rows carry no turn count.
- **Cloud/v4 only**, like the rest of this demo: `v2/observations` 404s on the
  repo's self-hosted v3 server, and the error carries a version hint saying so.
- **`source: ANNOTATION` cannot be produced from the API.** `ScoreBody` has no
  `source` field, so every score you POST is `API` no matter what — a genuine
  human annotation only comes from the UI flow. Plan demos accordingly: you can
  seed the queue programmatically, but somebody has to click.
- **Session-level notes go through the comments API** (`POST /api/public/comments`
  with `objectType: "SESSION"`, `projectId`, `objectId`, `content`), which is the
  only way to attach prose to a session without the UI. Score *comments* are
  UI-only in the same way `source` is.
- **There is no API to delete a queue** — only to delete items
  (`DELETE /api/public/annotation-queues/{queueId}/items/{itemId}`). Remove the
  queue itself from the UI; score configs can be archived, not deleted.
