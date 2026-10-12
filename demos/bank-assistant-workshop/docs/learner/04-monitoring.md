**DRAFT teaching material.**

# 04 Monitoring

Start `04-monitoring`. Tracing and managed prompts are complete; application signal recording is still empty.

**Goal:** find turns worth reviewing without reading every trace.

Replace the stub in `src/northwind/monitoring.py` with the reference module from the package root. Read each score before restarting. It attaches three BOOLEAN signals to the agent observation, with both trace and observation IDs:

- `guardrail-blocked`: the runtime guardrail blocked the turn.
- `pii-in-input`: one of the regex patterns detected sensitive-looking input.
- `all-caps-signal`: at least 12 alphabetic characters, with more than 70% uppercase.

The key call is:

```python
client.create_score(
    trace_id=trace_id, observation_id=observation_id,
    name="guardrail-blocked", value=int(check["blocked"]),
    data_type="BOOLEAN", comment="Runtime rules blocked this request."
)
```

Restart. Send a normal wire question, an injection request, a synthetic card number, and “THIS CHARGE IS STILL WRONG”. Inspect the corresponding scores. Filter root observations using the score you want to review. Test “WHAT IS MY BALANCE” too: the capitals signal can fire without actual frustration.

**Semantic monitor in Langfuse:** create an observation-level LLM judge for customer disagreement or complaint escalation. Use the current evaluator UI and a provider connection available to your project. Filter to root name `northwind-assistant`, type Agent, environment `workshop`, and Is Root Observation. Map conversation context from input `$.messages`, latest request from input `$.question`, and response from output `$.answer`. Preview on one normal turn and one complaint before activating it. An example rubric: “Did the customer push back on an earlier answer or request escalation?” Use a distinct score name such as `customer-disagreement`.

The SDK signals are emitted by the app. The semantic monitor is executed by the platform. Do not configure a second platform evaluator with the same SDK signal name. Exact UI selector labels may change; inspect the preview payload rather than assuming a template mapping.

**Verify:** signals attach to the right root; the semantic judge previews the intended fields; normal and anomalous turns are distinguishable.

**Review before sending: nuanced tradeoffs involved (Zone B, MEDIUM).** Capitals and regex matches are review signals, not evidence of anger, complete DLP or overall answer quality. The semantic rubric requires human calibration before operational use.

Source: [MCP score targets](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk), [MCP LLM judges](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge). SDK mechanics are HIGH confidence, level 1; the selected banking signals are a teaching assessment.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
