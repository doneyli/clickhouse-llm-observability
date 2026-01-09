FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY test-scenarios/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY test-scenarios/ .

ENTRYPOINT ["python", "export_test_scenarios.py"]
