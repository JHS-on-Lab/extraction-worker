#!/usr/bin/env bash
# run.sh — extraction-worker 컨테이너 실행
#
# 사용법:
#   ./deploy/run.sh <worker_id> [source]
#
# 인자:
#   worker_id  컨테이너 고유 식별자 (예: extr-1, extr-2)
#   source     처리할 소스 필터 (기본: all)
#              NAVER_NEWS | DAUM_NEWS | GOOGLE_NEWS | NAVER_STOCK | DUCKDUCKGO_NEWS | all
#
# 예시:
#   ./deploy/run.sh extr-1
#   ./deploy/run.sh extr-naver NAVER_NEWS

############################## 변수 설정 ################################

set -e
# deployment.env 로드
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV_FILE="${SCRIPT_DIR}/deployment.env"

if [ -f "$DEPLOY_ENV_FILE" ]; then
    echo "[INFO] $DEPLOY_ENV_FILE 파일을 로드합니다."
    export $(grep -v '^#' "$DEPLOY_ENV_FILE" | xargs)
else
    echo "[ERROR] $DEPLOY_ENV_FILE 파일이 존재하지 않습니다!"
    exit 1
fi

WORKER_ID="${1}"
SOURCE="${2:-all}"
ENV_FILE="${3:-env.dev}"
IMAGE=$4
APP_ENV="${APP_ENV:-dev}"
CONTAINER_NAME="${WORKER_ID}"
BASE_OUTPUT_DIR="/data001/crawler/apps/data/${CONTAINER_NAME}"
LOG_DIR="${BASE_OUTPUT_DIR}/logs"
OUTPUT_DIR="${BASE_OUTPUT_DIR}/output"

###########################################################################


############################## 실행 조건 확인 ##############################

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
# 셸이 공백 기준으로 인자를 쪼개 source 일부가 뒤쪽 인자로 밀린다. 이 스크립트는
# Jenkins 배포 시 env 파일과 이미지까지 총 4개 인자를 받으므로, 인자가 3개이거나
# 5개 이상이면 잘못 분리된 source 로 간주해 여기서 바로 막는다.
if (( $# == 3 || $# > 4 )); then
    echo "오류: source 인자에 띄어쓰기가 있으면 셸이 별도 인자로 쪼개 일부가 무시됩니다."
    echo "  입력값: worker_id=${WORKER_ID} source=${SOURCE} (뒤에 더 있음: ${*:3})"
    echo ""
    echo "  해결: 띄어쓰기 없이 쓰거나(NAVER_NEWS,DAUM_NEWS), 따옴표로 통째로 묶으세요(\"NAVER_NEWS, DAUM_NEWS\")."
    exit 1
fi

# source_type 이 실제 DB에 존재하는 값인지는 여기서 알 수 없다(위 주석 참고 —
# discovery-worker 쪽 소스 목록과 일부러 분리했다). 대신 형식만 검사해 명백한
# 오타(빈 토큰, 허용 문자 밖의 값 등)만 여기서 걸러낸다 — 나머지는 쿼리 결과
# 0건(계속 idle)으로 나타나므로, 그 전 단계에서 최소한의 방어선 역할만 한다.
if [[ "${SOURCE}" != "all" && "${SOURCE}" != "ALL" ]]; then
    if [[ ! "${SOURCE}" =~ ^[A-Za-z0-9_]+([[:space:]]*,[[:space:]]*[A-Za-z0-9_]+)*$ ]]; then
        echo "오류: source 형식이 올바르지 않습니다: '${SOURCE}'"
        echo "  영문/숫자/언더스코어만 사용하고, 콤마로 여러 개를 구분하세요 (예: NAVER_NEWS,DAUM_NEWS)."
        exit 1
    fi
fi


if [[ ! -f "${ENV_FILE}" ]]; then
    echo "오류: 환경 설정 파일을 찾을 수 없습니다: ${ENV_FILE}"
    exit 1
fi

###########################################################################


############################## 배포 시작  ##################################

mkdir -p "${LOG_DIR}" "${OUTPUT_DIR}"

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
###########################################################################