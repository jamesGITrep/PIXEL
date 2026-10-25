FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/gateway/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy common library and service code
COPY services/common/ /app/services/common/
COPY services/gateway/ /app/services/gateway/

ENV PYTHONPATH=/app
WORKDIR /app/services/gateway

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
