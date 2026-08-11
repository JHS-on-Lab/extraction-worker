#!/usr/bin/env bash
# 사용법:
#   ./deploy/build.sh           # latest 태그
#   ./deploy/build.sh v1.2.3    # 버전 태그 지정
# build.sh — extraction-worker Docker 이미지 빌드
# Artifact Registry 이미지용

set -e

# deployment.env 로드 
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/deployment.env"

if [ -f "$ENV_FILE" ]; then
    echo "[INFO] $ENV_FILE 파일을 로드합니다."
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo "[ERROR] $ENV_FILE 파일이 존재하지 않습니다!"
    exit 1
fi

# 빌드 환경 변수 설정
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT_REGISTRY_REPO="${GAR_LOCATION}-docker.pkg.dev/${GCP_PROJECT_ID}/${GAR_REPOSITORY}"
BASE_IMAGE_NAME="${IMAGE_NAME}"
NEW_IMAGE_NAME="${ARTIFACT_REGISTRY_REPO}/${BASE_IMAGE_NAME}"
TAG="${1:-latest}"


echo "▶ 빌드 시작: ${NEW_IMAGE_NAME}:${TAG}"
echo "  프로젝트 루트: ${PROJECT_ROOT}"

# APP_UID/APP_GID 를 빌드하는 사람(호스트 계정)의 UID/GID로 맞춘다. 배포
# 계정 하나로 build→run 을 항상 순서대로 실행하는 운영 방식이라, run.sh 가
# --user 를 따로 지정하지 않고 이 값을 그대로 상속해도 항상 일치한다.
docker build \
    --build-arg APP_UID="$(id -u)" --build-arg APP_GID="$(id -g)" \
    -t "${NEW_IMAGE_NAME}:${TAG}" \
    "${PROJECT_ROOT}"

echo ""
echo "✓ 빌드 완료: ${NEW_IMAGE_NAME}:${TAG}"
echo ""
echo "다음 단계:"
echo "  워커 시작  → ./deploy/run.sh <worker_id>"
echo "  이미지 확인 → docker images ${NEW_IMAGE_NAME}"