# 이 태그의 버전(v1.59.0)은 requirements.txt 의 playwright 핀과 반드시 일치시킬 것.
# 어긋나면 이미지에 내장된 브라우저/드라이버와 pip 로 설치되는 playwright 클라이언트
# 버전이 안 맞아 headless fetch 가 전부 실패한다.
FROM mcr.microsoft.com/playwright/python:v1.59.0-noble

ARG APP_UID=1001
ARG APP_GID=1001

WORKDIR /app

# 타임존: 서울(KST)
ENV TZ=Asia/Seoul

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libxml2-dev \
        libxslt1-dev \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 이 서버의 extraction-worker 배포 계정 UID/GID(1001, build.sh가 --build-arg
# 로 전달)로 작업용 계정을 만든다. deploy/run.sh 는 --user 를 따로 지정하지
# 않고 이미지가 빌드 시점에 갖게 된 이 계정을 그대로 상속해 실행한다.
RUN groupadd --gid "${APP_GID}" appgroup \
    && useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --create-home \
        --shell /bin/bash \
        appuser \
    && chown -R appuser:appgroup /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appgroup app/ app/
COPY --chown=appuser:appgroup .env .
COPY --chown=appuser:appgroup masking_list.json .
ENV HOME=/home/appuser

USER appuser