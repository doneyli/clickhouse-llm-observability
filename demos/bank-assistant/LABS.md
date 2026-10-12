# Guided labs

Three attendee labs for the [workshop](DEMO_SCRIPT.md). Use one of two modes:

- **Viewer mode** — attendees are invited as **Viewer** to the demo project and
  explore in the Langfuse UI (Labs 1, and the read-only parts of 2–3).
- **Driver mode** — the presenter drives; the room calls the clicks.

The portal supports English and Spanish. The dedicated Spanish dataset is
`northwind-golden-qa-es-v1`.

## Before the Labs

The presenter completes [setup and evaluations](README.md#prepare-evaluations)
and confirms project access for attendees.

| Lab | Required preparation | Completion |
|---|---|---|
| 1 | Recent core, security, and optional voice traffic | Identify the retrieval source, cost driver, and guardrail decision. |
| 2 | LLM connection and rights to create evaluators | Create a complaint-escalation judge and inspect its reasoning. |
| 3 | English/Spanish prompt experiments | Compare prompt changes and experiment scores. |

Use the time range that contains the prepared traffic. Commands run from
`demos/bank-assistant`.

---

## Lab 1 — Find the failure (8 min) · OBS-01, OBS-03, OBS-04, OBS-05

Viewer mode works for this lab. Use **last 1 hour** after generating fresh traffic.

1. **Sessions** (or Traces filtered by tag `channel:whatsapp`) → find a WhatsApp
   conversation about an overdraft fee. Which help-center article did the
   assistant use? *(Open a turn → `kb-retrieval` → output documents → `KB-302`.)*
2. **Traces**, sort by cost. What drove the most expensive turn — the number of
   tool iterations, or the context size? *(Count `ChatAnthropic` generations;
   compare input tokens.)*
3. Filter tags `risk:prompt_injection`. Open a blocked turn: what did the
   `input-guardrail` decide, and did the model ever see the message?
4. Bonus: open a voice call (`channel:voice`) and play the caller audio. What did
   speech-to-text get wrong, if anything?

## Lab 2 — Build your own evaluator (8 min, presenter drives) · EVA-03, EVA-04, EVA-05

Creating evaluators needs Member rights or above, so the presenter drives and
the room writes the rubric.

1. **Evaluators → + New evaluator → LLM-as-a-Judge**. Name `complaint-escalation`.
   Rubric (English or Spanish), written with the room:

   > Did the assistant offer a human call-back or the complaint process when the
   > customer was distressed, angry, or asked to complain? Score 1 if yes or if
   > not needed, 0 if it was needed and missing.
   >
   > Customer: {{query}} — Assistant: {{generation}}

2. Map `{{query}}` → observation **input**, `{{generation}}` → **output**; target
   observations named `northwind-assistant`; sampling 100%.
3. Run it on **existing observations** of the last few hours (the run-on-history
   option when creating the rule) and watch scores arrive; open one high and one
   low score and read the reasoning.
4. Discuss: would you trust this judge without calibration? *(→ Act 2.4,
   `judge-calibration/faithfulness`.)* Applying it to a dataset run is the same
   evaluator with a target of experiments — show it on
   `northwind-golden-qa-v1` if time allows.

## Lab 3 — Prompt experiment in the UI (5 min, optional) · EXP-02, EXP-04

1. **Prompts → `northwind-assistant-system`** → compare the versions currently
   labelled `production` and `staging`: what changed? *(Citations, investment
   rule, formal Spanish.)* If both labels resolve to the same version, choose
   the recorded baseline and candidate instead.
2. **Datasets → `northwind-golden-qa-es-v1` → runs** → compare `production` vs
   `staging`: which metric moved? *(`formal-register`.)*
3. **Datasets → `northwind-golden-qa-es-v1` → Start experiment** (UI) → pick a
   prompt version, a model and an evaluator. Note what a UI experiment is: it
   runs the **prompt** against each item — no tools, no retrieval — so it is the
   quick way to compare prompt wording; the full agent (RAG + MCP tools) is
   compared with SDK experiments (`scripts/run_experiment.py`), which is how the
   runs in step 2 were produced.

---

## For attendees who want to try it on their own project

Use your own project's `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and
`LANGFUSE_BASE_URL` environment variables for this standalone example.

```python
# pip install "langfuse>=4.13.1,<5.0"
from langfuse import get_client, observe, propagate_attributes

langfuse = get_client()        # reads LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL

@observe(as_type="agent", name="my-assistant")
def answer(question: str) -> str:
    with propagate_attributes(user_id="C-1001", session_id="demo-1", tags=["channel:web"]):
        return f"echo: {question}"     # call your agent application here

answer("¿Cuánto cuesta una transferencia internacional?")
langfuse.flush()
```

For callback-based tracing, import `CallbackHandler` from `langfuse.langchain`
and pass it in the run configuration; see [agent.py](northwind/agent.py).
