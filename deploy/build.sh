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

# APP_UID/APP_GID 는 이 서버의 extraction-worker 배포 계정 실제 값(1001)으로
# 고정한다. run.sh 는 --user 를 따로 지정하지 않고 이미지가 빌드 시점에
# 갖게 된 이 값을 그대로 상속해 실행한다.
docker build \
    --build-arg APP_UID=1001 --build-arg APP_GID=1001 \
    -t "${IMAGE_NAME}:${TAG}" \
    "${PROJECT_ROOT}"

echo ""
echo "✓ 빌드 완료: ${IMAGE_NAME}:${TAG}"
echo ""
echo "다음 단계:"
echo "  워커 시작  → ./deploy/run.sh <worker_id>"
echo "  이미지 확인 → docker images ${IMAGE_NAME}"
