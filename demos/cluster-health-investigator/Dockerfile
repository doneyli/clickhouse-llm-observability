FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl gcc python3-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Default: run the batch of 6 demo symptoms (varied worker fan-out).
#   docker compose --profile langfuse --profile demo run --rm cluster-health python main.py
#   docker compose --profile langfuse --profile demo run --rm cluster-health python main.py --interactive
#   docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/seed_all.py
CMD ["python", "main.py"]
