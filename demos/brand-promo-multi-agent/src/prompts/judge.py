"""LLM-as-judge prompt templates for online evaluation."""

PROMPTS: dict[str, str] = {
    "tool_call_correctness": """You are evaluating whether an AI agent called the right tools for the user's query.

User Query: {{query}}
Intent Classified: {{intent}}
Tools Called: {{tools_called}}

Expected tool usage by intent:
- plan_promo: should call query_sales, query_inventory, get_market_trends, check_brand_guidelines, check_regulatory
- compare_brands: should call query_sales at minimum
- compliance_check_only: should call check_brand_guidelines and check_regulatory

Score the tool usage from 0.0 to 1.0:
- 1.0: All expected tools called, no unexpected tools
- 0.8: Most expected tools called, minor omissions
- 0.5: Some expected tools called, significant omissions or wrong tools
- 0.2: Few expected tools called or major wrong tools
- 0.0: No relevant tools called

Respond with JSON only:
{"score": 0.0-1.0, "rationale": "one sentence"}""",
    "response_factuality": """You are evaluating whether the final campaign brief contains only real, verifiable information.

Campaign Brief:
{{brief}}

Known valid SKUs: {{valid_skus}}
Known valid brands: {{valid_brands}}
Known valid regions: {{valid_regions}}

Check for:
1. SKU codes that don't exist in the known SKU list
2. Brand names that don't exist in the known brand list
3. Regions that don't exist in the known region list
4. Fabricated statistics or figures not grounded in provided data

Score 0.0 to 1.0:
- 1.0: All referenced entities are real and verifiable
- 0.8: Minor inaccuracies that don't materially mislead
- 0.5: Some hallucinated entities or figures
- 0.3: Multiple hallucinated SKUs or fabricated statistics
- 0.0: Majority of specific claims are hallucinated

Respond with JSON only:
{"score": 0.0-1.0, "hallucinated_entities": ["list", "if", "any"], "rationale": "one sentence"}""",
    "compliance_adherence": """You are evaluating whether a campaign brief respects brand guidelines and compliance requirements.

Campaign Brief:
{{brief}}

Compliance Check Results:
{{compliance_findings}}
Compliance Status: {{compliance_status}}

Evaluate whether the brief:
1. Appropriately flags or omits content that violated compliance checks
2. Does not recommend actions that triggered HIGH severity findings
3. Includes required disclosures or caveats where compliance flagged them

Score 0.0 to 1.0:
- 1.0: Brief fully respects all compliance findings and includes appropriate caveats
- 0.8: Brief mostly respects compliance with minor oversights
- 0.5: Brief partially respects compliance findings
- 0.2: Brief ignores significant compliance findings
- 0.0: Brief recommends actions that violate HIGH severity rules

Respond with JSON only:
{"score": 0.0-1.0, "violations_ignored": ["list", "if", "any"], "rationale": "one sentence"}""",
}
