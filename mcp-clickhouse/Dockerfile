FROM mcp/clickhouse

RUN mkdir -p /app/logs

CMD ["sh", "-c", "python3 -m mcp_clickhouse.main >> /app/logs/mcp.log 2>&1"]
