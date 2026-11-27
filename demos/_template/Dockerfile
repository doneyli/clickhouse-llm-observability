# Container image for the root docker-compose.yaml `demo` profile. Compose mounts this
# folder at /app and passes the root .env, so no .env lives in the image.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
