# Dockerfile for LibreChat Exporter
# Reads conversations from MongoDB and exports to ClickHouse via OTLP

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Environment defaults
ENV PYTHONUNBUFFERED=1
ENV MONGO_URI=mongodb://librechat-mongodb:27017/LibreChat
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://clickstack:4318/v1/traces
ENV OTEL_SERVICE_NAME=librechat-conversations

# Default command - export last 24 hours
CMD ["python", "main.py", "--hours", "24"]
