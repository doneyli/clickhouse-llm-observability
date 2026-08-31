FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl gcc python3-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# No HTTP server — this is a CLI agent. Compose passes the real command, e.g.:
#   docker compose --profile demo run --rm slow-query-tuner python main.py --query q1
# Default prints usage so an accidental bare run never spends on the API.
CMD ["python", "main.py", "--help"]
