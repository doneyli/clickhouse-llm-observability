# Guided labs

Three short labs for attendees, placed inside the workshop (see DEMO_SCRIPT.md).
They work in two modes:

- **Viewer mode** — attendees are invited as **Viewer** to the demo project and
  explore in the Langfuse UI (Labs 1, and the read-only parts of 2–3).
- **Driver mode** — the presenter drives; the room calls the clicks.

Each lab works in English or Spanish: the portal has an EN | ES toggle, and the
Spanish golden dataset `northwind-golden-qa-es-v1` mirrors the English one.

---

## Lab 1 — Find the failure (8 min) · OBS-01, OBS-03, OBS-04, OBS-05

1. **Sessions** → find Carla Mendes' (`C-1003`) WhatsApp conversation about the
   overdraft fee. Which help-center article did the assistant use? *(Open a turn
   → `kb-retrieval` → output documents → `KB-302`.)*
2. **Traces**, last hour, sort by cost. What drove the most expensive turn — the
   number of tool iterations, or the context size? *(Count `ChatAnthropic`
   generations; compare input tokens.)*
3. Filter tags `risk:prompt_injection`. Open a blocked turn: what did the
   `input-guardrail` decide, and did the model ever see the message?
4. Bonus: open a voice call (`channel:voice`) and play the caller audio. What
   did speech-to-text get wrong, if anything?

## Lab 2 — Build your own evaluator (8 min) · EVA-03, EVA-04, EVA-05

1. **Evaluators → + New evaluator → LLM-as-a-Judge**. Name: `complaint-escalation`.
   Prompt (English or Spanish):

   > Did the assistant offer a human call-back or the complaint process when the
   > customer was distressed, angry, or asked to complain? Score 1 if yes or if
   > not needed, 0 if it was needed and missing.
   >
   > Customer: {{query}} — Assistant: {{generation}}

2. Map `{{query}}` → observation **input**, `{{generation}}` → **output**.
3. Target observations named `northwind-assistant`; sampling 100%.
4. Run it on (a) **existing traces** of the last 24 h and (b) the
   `northwind-golden-qa-v1` dataset as an experiment. Where does it fire?
5. Discuss: would you trust this judge without calibration? *(→ Act 2.4:
   `judge-calibration/faithfulness` shows how.)*

## Lab 3 — Prompt experiment in the UI (5 min, optional) · EXP-02, EXP-04

1. **Prompts → `northwind-assistant-system`** → compare v1 (`production`) with
   v4 (`staging`): what changed? *(Citations, investment rule, formal Spanish.)*
2. **Datasets → `northwind-golden-qa-es-v1` → runs** → compare
   `production` vs `staging`: which metric moved? *(`formal-register`.)*
3. Create a new version of the prompt with your own change and run a prompt
   experiment on the Spanish dataset from the UI.

---

## For attendees who want to try it on their own project

```python
# pip install "langfuse>=4.13" langchain-anthropic langgraph
import os
from langfuse import get_client, observe, propagate_attributes

langfuse = get_client()        # reads LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL

@observe(as_type="agent", name="my-assistant")
def answer(question: str) -> str:
    with propagate_attributes(user_id="C-1001", session_id="demo-1", tags=["channel:web"]):
        return f"echo: {question}"     # call your LangGraph app here

answer("¿Cuánto cuesta una transferencia internacional?")
langfuse.flush()
```

LangGraph/LangChain: pass `langfuse.langchain.CallbackHandler()` in the run
config — see `northwind/agent.py`.
