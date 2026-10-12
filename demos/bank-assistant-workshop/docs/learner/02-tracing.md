**DRAFT teaching material.**

# 02 Tracing

Start `02-tracing`. It has the same untraced app as 01.

**Goal:** one turn becomes an agent root with LangGraph generations, tools, retrieval, guardrails and cross-process MCP children.

The banking app already has named observation sites. This exercise completes the small integration module that activates those sites, rather than adding a decorator to every function.

Edit `src/northwind/tracing.py` in your exercise folder:

```python
from contextlib import contextmanager
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler

ENABLED = True

def callbacks():
    return [CallbackHandler()]

@contextmanager
def attributes(**kwargs):
    with propagate_attributes(**kwargs):
        yield
```

The callback captures LangGraph/LangChain generations and tool execution. The copied `run_turn` wraps those callbacks in a typed agent observation. `attributes` propagates session, user, tags and the prompt to child observations. `config.get_langfuse` creates the client with export-stage PII masking when tracing is enabled.

Stop and restart both services. Run preflight again to check Langfuse authentication. Ask the wire question, then list Ana's accounts. Open the trace link.

**Verify:**

- One request has one trace and a `northwind-assistant` agent root.
- Root input has `question` and `messages`; output has `answer`, evidence context and tool/source IDs.
- Generations show model, usage and latency.
- `kb-retrieval` shows document IDs and relevance scores.
- Account lookup includes MCP client, MCP server and core-banking steps in the same trace.
- Two conversational turns have the same session ID.
- An injection request has a blocked guardrail and no LLM generation.

For a masking check, use only the synthetic Luhn-valid example `4111 1111 1111 1111`, introduced as a card number. Inspect exported observations for `[REDACTED_CARD]`. Regex masking does not cover all names or addresses.

The root folder's `tracing.py` is the reference solution. If a trace is missing, verify Cloud region, keys and both process restarts. If server spans appear as another trace, inspect W3C `traceparent` and `baggage` handling in `config.py` and `mcp_server.py`.

Source: [MCP integration docs](https://langfuse.com/integrations/frameworks/langchain), [MCP best practices](https://langfuse.com/docs/observability/best-practices), [MCP masking](https://langfuse.com/docs/observability/sdk/advanced-features). HIGH confidence, level 1. Trace shape must be verified live; private callback nesting remains an upgrade sensitivity.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
