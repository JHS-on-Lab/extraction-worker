FROM mcr.microsoft.com/playwright/python:v1.59.0-noble

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libxml2-dev \
        libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY .env .
COPY masking_list.json .

RUN chmod -R o+rX /app
