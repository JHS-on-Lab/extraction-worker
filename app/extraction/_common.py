"""
추출 전략(library_chain.py, rule_engine.py) 공통 헬퍼.

여러 추출 전략이 각자 반복하던 title/body 검증 + CollectedContent 조립 로직을
한 곳에 모았다. min_body_len "기본값 자체"는 전략마다 의도적으로 다르므로
(HTML 스크래핑은 노이즈가 많아 200, next_data 는 100, json_api 는 이미
정제된 필드라 5) 여기서 통일하지 않는다 — 각 호출부가 자기 상수를 명시적으로
넘긴다. 통일하려던 게 아니라, 그 값들이 왜 다른지 안 보이고 매직넘버로
흩어져 있던 걸 정리하는 게 목적이다.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.domain_logic.url_normalizer import normalize, url_hash
from app.types import CollectedContent, ErrorCode, ExtractionFailure

KST = timezone(timedelta(hours=9))


def check_title(url: str, title: str, context: str) -> ExtractionFailure | None:
    """title 이 비어있으면 ExtractionFailure, 문제없으면 None."""
    if not title:
        return ExtractionFailure(
            url=url,
            error_code=ErrorCode.TITLE_EMPTY,
            error_msg=f"{context}: empty title",
            is_permanent=True,
        )
    return None


def check_body_length(url: str, body: str, min_body_len: int, context: str) -> ExtractionFailure | None:
    """body 가 없거나 min_body_len 미만이면 ExtractionFailure, 문제없으면 None."""
    if not body or len(body) < min_body_len:
        return ExtractionFailure(
            url=url,
            error_code=ErrorCode.BODY_TOO_SHORT,
            error_msg=f"{context}: body_len={len(body or '')} < {min_body_len}",
            is_permanent=False,
        )
    return None


def build_content(
    url: str,
    title: str,
    body: str,
    source_type: str,
    keyword: str,
    keyword_id: int | None,
    extraction_method: str,
    published_at: datetime | None = None,
    author: str | None = None,
) -> CollectedContent:
    """URL 정규화 + url_hash + CollectedContent 조립을 한 번에 처리한다."""
    norm = normalize(url)
    return CollectedContent(
        url=norm,
        url_hash=url_hash(norm),
        source_type=source_type,
        keyword=keyword,
        keyword_id=keyword_id,
        title=title.strip(),
        body=body.strip(),
        published_at=published_at,
        author=author,
        collected_at=datetime.now(KST),
        extraction_method=extraction_method,
    )
