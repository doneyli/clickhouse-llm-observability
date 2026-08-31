FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Default: run the FastAPI front door on :8000 (mapped to host :8008).
# Run the CLI batch demo with:
#   docker compose --profile demo run --rm query-router python main.py
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
