FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl gcc python3-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Default: run the FastAPI server on :8000 (mapped to host :8006).
# Build the vector index first with:  docker compose run --rm agentic-rag python ingest.py
# Run the CLI demo with:              docker compose run --rm agentic-rag python main.py
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
