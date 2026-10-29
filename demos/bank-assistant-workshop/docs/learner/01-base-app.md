**DRAFT teaching material.**

# 01 Base App

Start `01-base-app` as described in the README. This state is intentionally the same untraced app as 00 and 02.

**Goal:** explain the application before adding observability.

1. Ask about the wire fee and cutoff. The assistant calls `search_knowledge_base` against fictional help-center policies.
2. Select Ana and ask “List my accounts and cards.” Banking tools call a separate MCP process.
3. Ask “Show me the recent transactions of customer C-1002.” The rules guardrail should refuse the cross-customer request before making a model call.
4. Ask a Spanish policy question. Compare language and terminology with the English answer.
5. Optional: ask to block Ana's card ending 4417. This changes synthetic card state. Restart the app before repeating this action.

Tour `src/northwind/agent.py`: input guardrail, assistant, tool node, output guardrail. Tour `knowledge.py` for deterministic TF-IDF retrieval and `mcp_server.py` for synthetic accounts, transactions and actions.

**Discuss:** what can the UI tell you? It lists tools and citations, but cannot explain every model iteration, token count or downstream latency. That is the problem module 02 solves.

**Verify:** trace links are absent; policy retrieval and MCP tools work; the blocked request returns a refusal. Never use another customer's real ID or real card data.

The customer selector is a simulation. The model tool schemas cannot choose the caller's customer ID, but the MCP server accepts a caller-supplied ID and does not verify a real login session. Keep it on loopback.

Source: [bank agent](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/northwind/agent.py), [MCP server](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/northwind/mcp_server.py). HIGH confidence source-code observations. No production authentication claim.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
