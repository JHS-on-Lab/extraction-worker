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

# 이미지를 빌드하는 사람의 UID/GID로 작업용 계정을 만든다(build.sh가
# --build-arg 로 전달). deploy/run.sh 는 --user 를 따로 지정하지 않고 이
# 계정을 그대로 상속해 실행하므로, 빌드 시점과 실행 시점의 UID가 항상
# 자동으로 일치한다 — 서버마다/프로젝트마다 실제 배포 계정 UID가 다를 수
# 있어(예: 어떤 프로젝트는 1000, 어떤 프로젝트는 1001) 값을 하드코딩하면
# 오히려 어긋난다.
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