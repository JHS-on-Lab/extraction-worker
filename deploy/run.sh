#!/usr/bin/env bash
# run.sh — extraction-worker 컨테이너 실행
#
# 사용법:
#   ./deploy/run.sh <worker_id> [source]
#
# 인자:
#   worker_id  컨테이너 고유 식별자 (예: extr-1, extr-2)
#   source     처리할 소스 필터 (기본: all)
#              NAVER_NEWS | DAUM_NEWS | GOOGLE_NEWS | NAVER_STOCK | all
#
# 예시:
#   ./deploy/run.sh extr-1
#   ./deploy/run.sh extr-naver NAVER_NEWS

set -e

WORKER_ID="${1}"
SOURCE="${2:-all}"

if [[ -z "${WORKER_ID}" ]]; then
    echo "오류: worker_id 가 필요합니다."
    echo ""
    echo "사용법: $0 <worker_id> [source]"
    echo ""
    echo "예시:"
    echo "  $0 extr-1"
    echo "  $0 extr-naver NAVER_NEWS"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

APP_ENV="${APP_ENV:-dev}"
ENV_FILE="${PROJECT_ROOT}/.env.${APP_ENV}"

LOG_DIR="${HOME}/apps/data/extraction-worker/logs"
OUTPUT_DIR="${HOME}/apps/data/extraction-worker/output"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "오류: 환경 설정 파일을 찾을 수 없습니다: ${ENV_FILE}"
    exit 1
fi

mkdir -p "${LOG_DIR}" "${OUTPUT_DIR}"

CONTAINER_NAME="${WORKER_ID}"
IMAGE="extraction-worker:latest"

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "▶ 기존 컨테이너 제거: ${CONTAINER_NAME}"
    docker rm -f "${CONTAINER_NAME}"
fi

echo "▶ 컨테이너 시작: ${CONTAINER_NAME}"
echo "  이미지   : ${IMAGE}"
echo "  소스     : ${SOURCE}"
echo "  환경설정 : ${ENV_FILE}"

docker run \
    --detach \
    --name "${CONTAINER_NAME}" \
    --user "$(id -u):$(id -g)" \
    --restart unless-stopped \
    --env-file "${ENV_FILE}" \
    -e APP_ENV="${APP_ENV}" \
    -e WORKER_ID="${WORKER_ID}" \
    -v "${LOG_DIR}:/app/logs" \
    -v "${OUTPUT_DIR}:/app/output" \
    "${IMAGE}" \
    python -m app --source "${SOURCE}"

echo "✓ 시작 완료: ${CONTAINER_NAME}"
echo ""
echo "확인 명령어:"
echo "  실시간 로그   → docker logs -f ${CONTAINER_NAME}"
echo "  상태 확인     → docker ps | grep ${CONTAINER_NAME}"
echo "  컨테이너 중지 → docker stop ${CONTAINER_NAME}"
