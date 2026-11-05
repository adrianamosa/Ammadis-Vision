FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CLOUD_STUB_DB=/data/events.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cloud_stub ./cloud_stub

RUN mkdir -p /data
EXPOSE 8080
CMD ["uvicorn", "cloud_stub.main:app", "--host", "0.0.0.0", "--port", "8080"]
