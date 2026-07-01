"""
핵심 데이터 타입 — 설계 문서 4절 포트 시그니처 기준.
모든 모듈은 이 타입만 임포트하고 서로를 직접 참조하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ---------------------------------------------------------------------------
# 상수 / Enum
# ---------------------------------------------------------------------------

class SourceType(str, Enum):
    NAVER_NEWS  = "NAVER_NEWS"
    DAUM_NEWS   = "DAUM_NEWS"
    GOOGLE_NEWS = "GOOGLE_NEWS"
    BAIDU_NEWS  = "BAIDU_NEWS"
    NAVER_STOCK = "NAVER_STOCK"


class CrawlUrlStatus(str, Enum):
    DISCOVERED        = "discovered"
    EXTRACTING        = "extracting"
    STORED            = "stored"
    FAILED_TRANSIENT  = "failed_transient"
    FAILED_PERMANENT  = "failed_permanent"
    DEAD              = "dead"


class RenderMode(str, Enum):
    STATIC          = "static"
    HEADLESS        = "headless"
    HEADLESS_IFRAME = "headless_with_iframe"  # iframe 내용을 외부 HTML에 주입


# ---------------------------------------------------------------------------
# Fetcher 결과
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    url: str
    html: str
    status_code: int
    render_mode: RenderMode
    elapsed_ms: float


# ---------------------------------------------------------------------------
# Discovery 결과
# ---------------------------------------------------------------------------

@dataclass
class DiscoverResult:
    urls: list[str]
    next_cursor: str | None     # 다음 페이지/스크롤 커서. None이면 마지막 페이지.
    has_more: bool


# ---------------------------------------------------------------------------
# 추출 결과
# ---------------------------------------------------------------------------

@dataclass
class CollectedContent:
    url: str
    url_hash: str
    source_type: str
    keyword: str
    keyword_id: int | None
    title: str
    body: str
    published_at: datetime | None
    author: str | None
    collected_at: datetime
    extraction_method: str      # e.g. "trafilatura", "readability", "rule:css"
    body_len: int = field(init=False)

    def __post_init__(self) -> None:
        self.body_len = len(self.body)


class ErrorCode(str, Enum):
    # fetch
    FETCH_TIMEOUT      = "FETCH_TIMEOUT"
    FETCH_CONNECTION   = "FETCH_CONNECTION"
    FETCH_429          = "FETCH_429"
    FETCH_403          = "FETCH_403"
    FETCH_404          = "FETCH_404"
    FETCH_5XX          = "FETCH_5XX"
    FETCH_BLOCKED      = "FETCH_BLOCKED"
    # extraction
    BODY_TOO_SHORT     = "BODY_TOO_SHORT"
    TITLE_EMPTY        = "TITLE_EMPTY"
    PAYWALL            = "PAYWALL"
    PARSE_ERROR        = "PARSE_ERROR"
    # misc
    UNKNOWN            = "UNKNOWN"


class SinkUnavailableError(Exception):
    """Sink(Solr 등)가 일시적으로 사용 불가 — circuit open 상태."""


class BotBlockedError(Exception):
    """디스커버리 어댑터가 봇 차단을 감지 — dispatcher 가 단기 재시도로 처리."""


@dataclass
class ExtractionFailure:
    url: str
    error_code: ErrorCode
    error_msg: str
    is_permanent: bool          # False → 재시도 가능, True → failed_permanent
