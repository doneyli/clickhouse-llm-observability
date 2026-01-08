-- Daily cost estimation (GPT-4o pricing)
SELECT
    toDate(Timestamp) AS date,
    ServiceName,
    count() AS requests,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.prompt_tokens'])) AS input_tokens,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.completion_tokens'])) AS output_tokens,
    -- GPT-4o: $2.50/1M input, $10/1M output
    round((input_tokens * 2.50 + output_tokens * 10.0) / 1000000, 4) AS estimated_cost_usd
FROM otel_traces
WHERE SpanAttributes['gen_ai.system'] = 'openai'
GROUP BY date, ServiceName
ORDER BY date DESC
