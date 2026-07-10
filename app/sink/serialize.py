"""
CollectedContent → dict 직렬화 — Solr 스키마 필드명 기준.

FileSink 와 SolrSink 가 동일한 키 이름을 쓰도록 공유한다.

Solr 문서 필드:
  id                — crawl_id(url) (lookup3ycs64 기반 16자 hex)
  crawler_type      — t_crawl_runtime.crawler_type
  crawl_runtime_key — {$HOSTNAME}_{runtime_name}
  host              — URL 의 netloc
  site              — host 와 동일
  url               — 수집 URL. source_type 이 있으면 "{url}#{source_type}" 형태로 붙여
                      저장한다 — Solr 스키마에 source_type 전용 필드가 없어 row 단위로
                      출처를 구분할 수 있는 유일한 자리이기 때문. id 계산과 실제 fetch는
                      정규화(normalize) 과정에서 fragment 가 제거된 URL을 쓰므로 영향 없다.
  title             — 제목
  content           — 본문
  author            — 저자 (배열, 값이 있을 때만 포함)
  tstamp            — 수집 시각 (UTC)
  doc_version       — 1 고정
  keyword_id        — t_keyword.id 문자열 변환 (배열, 값이 있을 때만 포함)
  etc_exact1        — "1" 고정
"""

from __future__ import annotations

from datetime import timezone
from pathlib import Path
from urllib.parse import urlparse

from app import config
from app.domain_logic.masking import TextMasker, mask_author
from app.domain_logic.url_normalizer import crawl_id
from app.types import CollectedContent

_UTC    = timezone.utc
_masker = TextMasker()

# app/sink/serialize.py → app/sink/ → app/ → project root
_MASKING_LIST = Path(__file__).parent.parent.parent / "masking_list.json"


def init_masker() -> None:
    """masking_list.json 로드. __main__.py 에서 워커 기동 시 1회 호출."""
    _masker.load(_MASKING_LIST)


def to_doc(content: CollectedContent, crawler_type: str, crawl_runtime_key: str) -> dict:
    """CollectedContent 을 Solr 스키마 기준 dict 로 변환한다."""
    host   = urlparse(content.url).netloc
    tstamp = content.collected_at.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    if config.MASKING_ENABLED:
        body   = _masker.mask(content.body or "", label="본문")
        author = mask_author(content.author)
    else:
        body   = content.body
        author = content.author

    url = f"{content.url}#{content.source_type}" if content.source_type else content.url

    doc: dict = {
        "id":                crawl_id(content.url),
        "crawler_type":      crawler_type,
        "crawl_runtime_key": crawl_runtime_key,
        "host":              host,
        "site":              host,
        "url":               url,
        "title":             content.title,
        "content":           body,
        "tstamp":            tstamp,
        "doc_version":       1,
        "etc_exact1":        "1",
    }

    if author:
        doc["author"] = [author]

    if content.keyword_id is not None:
        doc["keyword_id"] = [str(content.keyword_id)]

    return doc


# SolrSink 하위 호환용 별칭
to_solr_doc = to_doc
