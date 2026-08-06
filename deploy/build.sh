#!/usr/bin/env bash
# build.sh — extraction-worker Docker 이미지 빌드
#
# 사용법:
#   ./deploy/build.sh           # latest 태그
#   ./deploy/build.sh v1.2.3    # 버전 태그 지정

set -e

IMAGE_NAME="extraction-worker"
TAG="${1:-latest}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "▶ 빌드 시작: ${IMAGE_NAME}:${TAG}"
echo "  프로젝트 루트: ${PROJECT_ROOT}"

# APP_UID/APP_GID 를 빌드하는 사람(호스트 계정)의 UID/GID로 맞춘다. run.sh 는
# --user 를 따로 지정하지 않고 이 계정을 그대로 상속해 실행하므로, 빌드와
# 실행을 같은 계정으로 하면 항상 자동으로 맞는다.
docker build \
    --build-arg APP_UID="$(id -u)" --build-arg APP_GID="$(id -g)" \
    -t "${IMAGE_NAME}:${TAG}" \
    "${PROJECT_ROOT}"

echo ""
echo "✓ 빌드 완료: ${IMAGE_NAME}:${TAG}"
echo ""
echo "다음 단계:"
echo "  워커 시작  → ./deploy/run.sh <worker_id>"
echo "  이미지 확인 → docker images ${IMAGE_NAME}"
