FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY templates ./templates
COPY static ./static

RUN pip install --no-cache-dir -e .

ENV PLUTUS_DATA_DIR=/data
ENV PLUTUS_HOST=0.0.0.0
ENV PLUTUS_PORT=8030
ENV PLUTUS_PUBLIC_URL=http://127.0.0.1:8030

RUN mkdir -p /data

EXPOSE 8030

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8030"]