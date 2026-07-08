"""
라이브러리를 이용한 본문 추출.

trafilatura 를 먼저 시도하고, 결과가 없거나 너무 짧으면(200자 미만) readability 로도
시도해 더 긴 쪽을 채택한다. 그래도 둘 다 실패하거나 짧으면 ExtractionFailure 를 반환한다.

trafilatura 가 1순위인 이유:
  웹 콘텐츠 본문 추출에 특화돼 있어 광고·메뉴 등 노이즈를 잘 걸러낸다.
  단, favor_precision=True 모드라 본문 대신 바이라인/날짜 같은 엉뚱한 짧은 영역만
  잡아내는 경우가 있다(예: www.fomos.kr, "박상진2026-07-06 17:21" 24자만 반환).
  trafilatura 가 완전히 빈 결과를 반환하는 경우도 있어, 두 상황 모두 범용 라이브러리인
  readability 를 대비책으로 둔다.

이 라이브러리 체인 폴백은 최후의 안전망이다 — 특정 도메인에서 반복 실패하면
scripts/seed_domain_rules.py 에 전용 CSS/XPath 규칙을 추가하는 게 더 정확하고 우선한다
(도메인 규칙이 있으면 rule_engine 이 이 체인보다 먼저 시도됨, app/extraction/extractor.py 참고).

JS 렌더링이 필요한 페이지(SPA, 페이월 등)는 정적 HTML 만으로는 추출이 불가능하다.
이런 경우 PARSE_ERROR 또는 BODY_TOO_SHORT 로 실패하며, headless 렌더링이 필요하다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.domain_logic.url_normalizer import normalize, url_hash
from app.types import CollectedContent, ErrorCode, ExtractionFailure

# trafilatura 가 반환하는 byline 에서 이름만 추출하기 위한 패턴
_EMAIL_RE       = re.compile(r'\s*[\w.+-]+@\S+')
_TITLE_RE       = re.compile(r'\s+(?:기자|특파원|칼럼니스트|논설위원|편집장|앵커|PD)\b.*$')
_KOREAN_NAME_RE = re.compile(r'^([가-힣]{2,})')


def _clean_author(raw: str | None) -> str | None:
    """
    trafilatura byline 에서 이름만 추출. 이메일·직함·소속 제거.

    '김철화 기자 kkk@mt.co.kr' → '김철화'
    '김철화 머니투데이 기자'    → '김철화'  (소속사도 제거)
    'John Smith 기자'          → 'John Smith'
    """
    if not raw:
        return None
    name = _EMAIL_RE.sub('', raw).strip()
    name = _TITLE_RE.sub('', name).strip()
    # 한글 byline: '이름 소속사' 형태에서 첫 한글 이름만 추출
    m = _KOREAN_NAME_RE.match(name)
    if m:
        return m.group(1)
    return name or None

KST = timezone(timedelta(hours=9))

_MIN_BODY_LEN = 200


class LibraryChain:
    def extract(
        self,
        url: str,
        html: str,
        host: str,
        source_type: str = "",
        keyword: str = "",
        keyword_id: int | None = None,
    ) -> CollectedContent | ExtractionFailure:
        """HTML → CollectedContent. 실패 시 ExtractionFailure."""
        result = _try_trafilatura(html)
        # trafilatura 가 아예 실패했거나(None) 너무 짧은 영역만 잡았으면(예: 바이라인만)
        # readability 도 시도해서 더 긴 쪽을 채택한다.
        if result is None or len(result[1]) < _MIN_BODY_LEN:
            fallback = _try_readability(html)
            if fallback is not None and (result is None or len(fallback[1]) > len(result[1])):
                result = fallback

        if result is None:
            return ExtractionFailure(
                url=url,
                error_code=ErrorCode.PARSE_ERROR,
                error_msg="trafilatura and readability both returned nothing",
                is_permanent=False,
            )

        title, body, method, author = result

        if not title:
            return ExtractionFailure(
                url=url,
                error_code=ErrorCode.TITLE_EMPTY,
                error_msg="title is empty after extraction",
                is_permanent=True,
            )

        if len(body) < _MIN_BODY_LEN:
            return ExtractionFailure(
                url=url,
                error_code=ErrorCode.BODY_TOO_SHORT,
                error_msg=f"body_len={len(body)} < {_MIN_BODY_LEN}",
                is_permanent=False,
            )

        norm = normalize(url)
        return CollectedContent(
            url=norm,
            url_hash=url_hash(norm),
            source_type=source_type,
            keyword=keyword,
            keyword_id=keyword_id,
            title=title.strip(),
            body=body.strip(),
            published_at=None,
            author=author,
            collected_at=datetime.now(KST),
            extraction_method=method,
        )


def _try_trafilatura(html: str) -> tuple[str, str, str, str | None] | None:
    try:
        import trafilatura
        body = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            no_fallback=True,
            output_format="txt",
        )
        if not body:
            return None
        meta = trafilatura.extract_metadata(html)
        title  = (meta.title  or "") if meta else ""
        author = _clean_author(meta.author if meta else None)
        return title, body, "trafilatura", author
    except Exception:
        return None


def _try_readability(html: str) -> tuple[str, str, str, str | None] | None:
    try:
        from readability import Document
        from selectolax.parser import HTMLParser
        doc = Document(html)
        title = doc.title() or ""
        body = HTMLParser(doc.summary()).text(separator="\n").strip()
        if not body:
            return None
        return title, body, "readability", None
    except Exception:
        return None
