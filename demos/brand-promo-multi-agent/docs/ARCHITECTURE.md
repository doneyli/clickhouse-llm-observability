# Architecture: Brand Promo Multi-Agent Demo

## System Diagram

```
                          demo.config.yaml + .env
                                  |
                                  v
+-------------------------------------------------------------------------+
|                       PromoPlanner (hero agent)                          |
|                                                                          |
|   User Query                                                             |
|       |                                                                  |
|       v                                                                  |
|   +---+-------------------------------+                                  |
|   | LangGraph Orchestrator             |  routes by intent, composes brief|
|   +---+----+----------+---------------+                                  |
|       |    |          |                                                  |
|       v    v          v                                                  |
|   Research  Strategy  Compliance                                         |
|   Crew      Crew      Agent                                              |
|  (CrewAI)  (CrewAI)  (LangGraph node)                                    |
|       |       |       |                                                  |
|       +---+---+---+---+                                                  |
|           |       |                                                      |
|           v       v                                                      |
|   Tools layer (mock SAP/Salesforce/vector/regulatory + error inject)     |
|   query_sales | query_inventory | get_market_trends                      |
|   check_brand_guidelines | check_regulatory                              |
+-------------------------------+-----------------------------------------+
                                |
                                v Langfuse CallbackHandler + SDK
                                |
                       +--------+--------+
                       |   Langfuse v3   |  ClickHouse + Postgres
                       |  localhost:3001 |
                       +-----------------+
                              |
              +---------------+------------------+
              |               |                  |
              v               v                  v
        Persona           Online evals     Datasets +
        dashboards        (LLM-as-judge)   Experiments
     (3 per persona)      10% sampling     golden-v1
```

## Agent Fleet

| Agent | Type | Share | Model Tier |
|---|---|---|---|
| PromoPlanner (hero) | LangGraph + CrewAI | 20% | Sonnet/Opus/Haiku |
| CustomerCareBot | Simple RAG | 30% | Sonnet |
| SupplyChainPlanner | Tool chain | 15% | Sonnet |
| ShelfImageAnalyzer | Vision | 10% | Sonnet |
| InternalKBSearch | RAG | 20% | Sonnet |
| FinanceCloseBot | SQL | 5% | Sonnet |

## PromoPlanner Span Tree

```
trace: promo_planner_run
  span: classify_intent (generation, model=sonnet, ~400ms, ~150 tok)
  span: research_crew
       span: data_analyst
             span: tool.query_sales (~250ms, may inject timeout)
             span: tool.query_inventory (~150ms)
             span: generation.summarize (sonnet, ~1500 tok)
       span: market_researcher
             span: tool.market_trends (~600ms)
             span: generation.summarize (sonnet, ~800 tok)
       span: historian
             span: tool.query_historical_promos (~100ms)
             span: generation.summarize (sonnet, ~600 tok)
  span: strategy_crew
       span: promo_strategist
             span: generation.generate_options (opus, ~3000 tok)
       span: lift_estimator
             span: generation.estimate (opus, ~1200 tok)
  span: compliance_agent
       span: check_brand_guidelines (haiku, ~600 tok)
       span: check_regulatory (haiku, ~600 tok)
  span: generation.compose_brief (sonnet, ~2500 tok)
  score: tool-call-correctness (online, 10% sampled)
  score: response-factuality (online, 10% sampled)
  score: compliance-adherence (online, 10% sampled)
```

## Langfuse Object Model

| Object | Count | Purpose |
|---|---|---|
| Traces | ~50,000 synthetic + live | One per agent run |
| Spans | ~15-20 per PromoPlanner trace | Structural nodes |
| Generations | ~8 per PromoPlanner trace | LLM calls with token counts |
| Scores | ~5% of PromoPlanner traces | LLM-as-judge evaluations |
| Prompts | 12 | Version-controlled templates |
| Dataset | 1 (25 items) | Golden eval set |
| Evaluators | 3 | Online LLM-as-judge |
| Dashboards | 3 | Executive, Ops, Engineer |
| Annotation Queue | 1 (10 items) | Human review queue |

## Model Assignments

| Role | Model | Rationale |
|---|---|---|
| Orchestrator (classify, compose) | claude-sonnet-4-6 | Balanced cost/quality |
| Research crew | claude-sonnet-4-6 | Balanced cost/quality |
| Strategy crew | claude-opus-4-7 | Deep reasoning for promo options |
| Compliance agent | claude-haiku-4-5 | Fast/cheap keyword checks |
| LLM-as-judge | claude-opus-4-7 | High-quality evaluation |

## Key Design Decisions

**No sampling on ingestion**: All traces are captured (Langfuse engineering position). 10% sampling applies only to online evaluators to control cost.

**Error injection**: Seeded RNG per process using `demo_run_id` UUID. Failure modes: `sales_api_timeout` (5%), `hallucinated_sku` (2%), `compliance_rejection` (8%), `crew_max_iterations` (1%), `tool_error` (4%). Sum = 20% < 1.0.

**Synthetic history**: Direct Langfuse SDK ingestion (no LLM calls). Business-hours weighting: 75% of traces land 9am-6pm in local timezone.

**Prompt caching**: System prompt on orchestrator uses LangChain-Anthropic cache_control. Reduces cost on repeated system-prompt prefixes.

**CrewAI + LangGraph nesting**: CrewAI crews are invoked from LangGraph nodes. Span context is passed via the Langfuse CallbackHandler to nest sub-crew spans under orchestrator traces.
