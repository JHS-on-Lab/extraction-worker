#!/usr/bin/env bash
# run.sh — extraction-worker 컨테이너 실행
#
# 사용법:
#   ./deploy/run.sh <worker_id> [source]
#
# 인자:
#   worker_id  컨테이너 고유 식별자 (예: extr-1, extr-2)
#   source     처리할 소스 필터 (기본: all)
#              NAVER_NEWS | DAUM_NEWS | GOOGLE_NEWS | BAIDU_NEWS | NAVER_STOCK | DUCKDUCKGO_NEWS | BAOMOI_NEWS | TINHTE_FORUM | all
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

# source 를 콤마 뒤 띄어쓰기와 함께 따옴표 없이 넘기면(예: NAVER_NEWS, DAUM_NEWS)
# 셸이 공백 기준으로 인자를 쪼개 $3 이후가 조용히 버려지고 --source 는 뒤에 콤마만
# 남은 값으로 전달된다 — app/__main__.py._parse_source() 가 빈 토큰을 걸러내는
# 탓에 에러 없이 소스 일부만 처리되는 채로 넘어간다. $3 이 존재하면 그 상황이므로
# 여기서 바로 막는다.
if [[ -n "${3:-}" ]]; then
    echo "오류: source 인자에 띄어쓰기가 있으면 셸이 별도 인자로 쪼개 일부가 무시됩니다."
    echo "  입력값: worker_id=${WORKER_ID} source=${SOURCE} (뒤에 더 있음: ${*:3})"
    echo ""
    echo "  해결: 띄어쓰기 없이 쓰거나(NAVER_NEWS,DAUM_NEWS), 따옴표로 통째로 묶으세요(\"NAVER_NEWS, DAUM_NEWS\")."
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
    --user "1001:1001" \
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
