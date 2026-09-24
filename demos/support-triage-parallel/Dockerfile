FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl gcc python3-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# The demo is a CLI batch (no long-running server). Run it with:
#   docker compose --profile demo run --rm support-triage-parallel python main.py
# Flags: --interactive | --sequential | --ticket TCK-002 ; FAULT=slow-branch env.
CMD ["python", "main.py"]
