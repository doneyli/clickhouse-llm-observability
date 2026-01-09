FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc python3-dev && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir trulens-dashboard>=1.0.0

ENV PYTHONUNBUFFERED=1

CMD ["python", "-c", "import os; from trulens.core import TruSession; s = TruSession(database_url=os.getenv('TRULENS_DATABASE_URL', 'sqlite:///default.sqlite')); s.run_dashboard(port=8501, force=True)"]
