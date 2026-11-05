FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ammadis_edge ./ammadis_edge

EXPOSE 8080
CMD ["uvicorn", "ammadis_edge.main:app", "--host", "0.0.0.0", "--port", "8080"]
