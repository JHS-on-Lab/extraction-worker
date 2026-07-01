"""
환경변수에서 설정을 읽는다.

값은 .env 파일 또는 실제 환경변수 어느 쪽에서든 넣을 수 있다.
서버에서는 보통 환경변수로, 로컬 개발에서는 .env 파일로 설정한다.
.env 파일이 없어도 오류가 나지 않는다.

필수 변수(RDS_*)가 없으면 워커 시작 시 validate() 가 오류를 출력하고 종료한다.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# .env (공통) 먼저 로드 후 .env.{APP_ENV} 로 override.
#   로컬 Windows : APP_ENV 미설정 → .env + .env.local
#   Ubuntu 서버  : APP_ENV=dev   → .env + .env.dev
_root = Path(__file__).parent.parent
load_dotenv(_root / ".env")
_app_env = os.getenv("APP_ENV", "local")
load_dotenv(_root / f".env.{_app_env}", override=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


# SSH Tunnel
TUNNEL_ENABLED      = _env_bool("TUNNEL_ENABLED")
TUNNEL_SSH_HOST     = _env("TUNNEL_SSH_HOST")
TUNNEL_SSH_PORT     = _env_int("TUNNEL_SSH_PORT", 22)
TUNNEL_SSH_USER     = _env("TUNNEL_SSH_USER", "ubuntu")
TUNNEL_SSH_KEY_PATH = _env("TUNNEL_SSH_KEY_PATH")
TUNNEL_LOCAL_PORT   = _env_int("TUNNEL_LOCAL_PORT", 13306)

# RDS
RDS_HOST     = _env("RDS_HOST")
RDS_PORT     = _env_int("RDS_PORT", 3306)
RDS_USER     = _env("RDS_USER")
RDS_PASSWORD = _env("RDS_PASSWORD")
RDS_DB       = _env("RDS_DB")

# Worker
WORKER_ID              = _env("WORKER_ID", "worker-1")

# Fetcher
DEFAULT_CRAWL_DELAY_MS  = _env_int("DEFAULT_CRAWL_DELAY_MS", 1000)
DEFAULT_RENDER_MODE     = _env("DEFAULT_RENDER_MODE", "static")
PROXY_PROVIDER          = _env("PROXY_PROVIDER", "direct")
HTTP_VERIFY_SSL         = _env_bool("HTTP_VERIFY_SSL", True)   # 사내 자체서명 인증서 환경에서는 false

# Google 발견 모드
# search: google.com/search?tbm=nws 스크랩 (기본)
# rss:    Google News RSS + Chrome CBMi URL 변환 (봇 차단 시 대안)
GOOGLE_DISCOVERY_MODE   = _env("GOOGLE_DISCOVERY_MODE", "search")

# Daum 뉴스 수집 범위 (기본: 전체 언론사)
# false 로 설정하면 뉴스제휴 언론사만 수집 (SHOW_DNS=1)
DAUM_NEWS_ALL         = _env_bool("DAUM_NEWS_ALL", True)

# 소스별 발견 최대 페이지 수 (키워드 1회 실행당)
NAVER_MAX_PAGES       = _env_int("NAVER_MAX_PAGES",       10)
DAUM_MAX_PAGES        = _env_int("DAUM_MAX_PAGES",        10)
GOOGLE_MAX_PAGES      = _env_int("GOOGLE_MAX_PAGES",       5)
BAIDU_MAX_PAGES       = _env_int("BAIDU_MAX_PAGES",        5)
NAVER_STOCK_MAX_PAGES = _env_int("NAVER_STOCK_MAX_PAGES",  5)

# Sink
SINK_TYPE       = _env("SINK_TYPE", "file")   # file | solr
FILE_SINK_DIR   = _env("FILE_SINK_DIR", "./output")
LOG_DIR         = _env("LOG_DIR", "./logs")

# Solr (SINK_TYPE=solr 일 때만 필요)
# [직접 모드] SOLR_DIRECT_ENABLED=true → SOLR_URL 로 직접 접속
# [DB 조회 모드] SOLR_DIRECT_ENABLED=false → SOLR_RUNTIME_NAME 으로 t_crawl_runtime 조회
SOLR_DIRECT_ENABLED   = _env_bool("SOLR_DIRECT_ENABLED")
SOLR_URL              = _env("SOLR_URL", "")
SOLR_RUNTIME_NAME     = _env("SOLR_RUNTIME_NAME", "")  # t_crawl_runtime.runtime_name
SOLR_CRAWLER_TYPE     = _env("SOLR_CRAWLER_TYPE", "")  # SOLR_DIRECT_ENABLED=true 일 때 사용
SOLR_BATCH_SIZE           = _env_int("SOLR_BATCH_SIZE", 100)
SOLR_COMMIT_WITHIN_MS     = _env_int("SOLR_COMMIT_WITHIN_MS", 5000)
SOLR_CONNECT_TIMEOUT_S    = _env_int("SOLR_CONNECT_TIMEOUT_S", 5)   # TCP 연결 타임아웃
SOLR_READ_TIMEOUT_S       = _env_int("SOLR_READ_TIMEOUT_S", 30)     # 응답 수신 타임아웃

# Masking
MASKING_ENABLED = _env_bool("MASKING_ENABLED", True)

# Retry / Backoff
MAX_ATTEMPTS              = _env_int("MAX_ATTEMPTS", 5)
BACKOFF_BASE_SECONDS      = _env_int("BACKOFF_BASE_SECONDS", 30)
BACKOFF_MAX_SECONDS       = _env_int("BACKOFF_MAX_SECONDS", 3600)
CLAIM_TIMEOUT_SECONDS     = _env_int("CLAIM_TIMEOUT_SECONDS", 300)
DISCOVERY_403_RESCHEDULE_SEC = _env_int("DISCOVERY_403_RESCHEDULE_SEC", 1800)
BOT_DETECT_RETRY_SEC         = _env_int("BOT_DETECT_RETRY_SEC",         1800)

# Rules cache
RULES_CACHE_TTL_SECONDS = _env_int("RULES_CACHE_TTL_SECONDS", 60)

# Logging
LOG_LEVEL                  = _env("LOG_LEVEL", "INFO")
LOG_ROTATION               = _env("LOG_ROTATION", "daily")
LOG_RETAIN_DAYS            = _env_int("LOG_RETAIN_DAYS", 30)   # daily 모드: 보관 일수
LOG_BACKUP_COUNT           = _env_int("LOG_BACKUP_COUNT", 10)  # size 모드: 보관 파일 수
HEARTBEAT_INTERVAL_SECONDS = _env_int("HEARTBEAT_INTERVAL_SECONDS", 60)


# ---------------------------------------------------------------------------
# 시작 시 검증
# ---------------------------------------------------------------------------

_REQUIRED_ALWAYS = ["RDS_HOST", "RDS_USER", "RDS_PASSWORD", "RDS_DB"]
_REQUIRED_TUNNEL = ["TUNNEL_SSH_HOST", "TUNNEL_SSH_KEY_PATH"]
_REQUIRED_SOLR   = ["SOLR_RUNTIME_NAME"]


def validate() -> None:
    """
    필수 환경변수를 일괄 검증한다.
    누락 항목이 있으면 목록을 stderr 에 출력하고 sys.exit(1).
    __main__.py 에서 워커 루프 진입 전에 호출한다.
    """
    missing = [k for k in _REQUIRED_ALWAYS if not os.getenv(k)]

    if TUNNEL_ENABLED:
        missing += [k for k in _REQUIRED_TUNNEL if not os.getenv(k)]

    if SINK_TYPE == "solr":
        if SOLR_DIRECT_ENABLED:
            if not SOLR_URL:
                missing.append("SOLR_URL")
        else:
            missing += [k for k in _REQUIRED_SOLR if not os.getenv(k)]

    if not missing:
        return

    print("ERROR: 다음 필수 환경변수가 설정되지 않았습니다:", file=sys.stderr)
    for key in missing:
        print(f"  - {key}", file=sys.stderr)
    print("  .env 파일 또는 환경변수를 확인하세요.", file=sys.stderr)
    sys.exit(1)
