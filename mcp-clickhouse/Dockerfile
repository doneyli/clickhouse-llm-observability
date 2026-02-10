# Note: mcp/clickhouse only publishes a `latest` tag on Docker Hub.
# Pin to a specific version when one becomes available.
FROM mcp/clickhouse:latest

RUN mkdir -p /app/logs

CMD ["sh", "-c", "python3 -m mcp_clickhouse.main >> /app/logs/mcp.log 2>&1"]
