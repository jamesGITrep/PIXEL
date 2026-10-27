FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY services/worker/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy common library and service code
COPY services/common/ /app/services/common/
COPY services/worker/ /app/services/worker/

ENV PYTHONPATH=/app
WORKDIR /app/services/worker

STOPSIGNAL SIGTERM
EXPOSE 8080
CMD ["python", "worker.py"]
