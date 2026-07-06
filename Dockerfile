# 이 태그의 버전(v1.59.0)은 requirements.txt 의 playwright 핀과 반드시 일치시킬 것.
# 어긋나면 이미지에 내장된 브라우저/드라이버와 pip 로 설치되는 playwright 클라이언트
# 버전이 안 맞아 headless fetch 가 전부 실패한다.
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
