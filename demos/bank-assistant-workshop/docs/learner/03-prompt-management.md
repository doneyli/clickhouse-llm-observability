**DRAFT teaching material.**

# 03 Prompt Management

Start `03-prompt-management`. Tracing is complete; the prompt still comes from local code.

**Goal:** serve a labeled, versioned prompt and see its version on the generation.

Publish the baseline from this exercise folder:

```bash
../../.venv/bin/python workshop.py --env-file ../../.env prompt --variant baseline --label workshop-baseline
```

This creates a version of `bank-workshop/<WORKSHOP_ID>/assistant`. Repeating the command creates another version. It never moves the original banking demo's prompt or production label.

Replace `src/northwind/prompt_source.py`:

```python
def resolve(client, name, fallback, *, label, version=None):
    options = {"version": version} if version is not None else {"label": label}
    return client.get_prompt(name, type="text", cache_ttl_seconds=0,
                             fallback=fallback, **options)
```

The existing `prompts.get_system_prompt` compiles `{{bank_name}}` into Northwind Bank. The agent propagates the resolved prompt client to its generations. Cache TTL zero makes workshop changes visible on the next turn; production caching is a separate latency/availability decision.

Restart both processes and repeat the wire question. Open its generation. Verify the linked prompt name and version, and the portal's prompt badge.

**Fallback exercise:** set `NORTHWIND_PROMPT_LABEL` to a label that does not exist. Restart, ask again, and see “local fallback” in the portal. Restore `workshop-baseline`. A fallback lets the teaching app answer; it must never be mistaken for successful prompt publication.

**Verify:** real version linkage, variable compilation, and visible fallback. Experiments resolve strictly and pin the version so a moved label or failed prompt fetch cannot silently change the comparison.

Source: [MCP prompt linkage](https://langfuse.com/docs/prompt-management/features/link-to-traces), [MCP prompt quickstart](https://langfuse.com/docs/prompt-management/get-started). HIGH confidence, level 1. Python SDK 4.14.0+ is required for the propagation call used here.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
