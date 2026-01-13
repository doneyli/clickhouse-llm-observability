FROM mcp/clickhouse

# Install OpenTelemetry packages
RUN pip install --no-cache-dir \
    opentelemetry-distro \
    opentelemetry-exporter-otlp

# Bootstrap OpenTelemetry instrumentation
RUN opentelemetry-bootstrap -a install

RUN mkdir -p /app/logs

# Run with OpenTelemetry instrumentation
CMD ["sh", "-c", "OTEL_EXPORTER_OTLP_HEADERS=\"authorization=$CLICKSTACK_API_KEY\" opentelemetry-instrument python3 -m mcp_clickhouse.main >> /app/logs/mcp.log 2>&1"]
