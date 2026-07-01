"""
Solr 더미 데이터 투입 테스트 스크립트.

SolrSink 를 통해 CollectedContent 더미 데이터를 Solr 에 저장하고
실제 문서가 인덱싱됐는지 확인한다.

사용법:
  # 환경변수 모드 (기본)
  python scripts/test_solr_sink.py

  # RDB 조회 모드 (t_crawl_runtime 에서 설정 가져옴)
  python scripts/test_solr_sink.py --rdb

환경변수 (.env 또는 실제 환경변수):
  [공통]
  RDS_HOST / RDS_USER / RDS_PASSWORD / RDS_DB  (RDB 접속 정보)

  [환경변수 모드]
  SOLR_URL=http://localhost:8983/solr/<core>
  SOLR_CRAWLER_TYPE=test_crawler      (선택, 기본 "")

  [RDB 조회 모드 --rdb]
  SOLR_RUNTIME_NAME=<runtime_name>    (t_crawl_runtime.runtime_name)
  SOLR_URL, SOLR_CRAWLER_TYPE 은 무시된다.
"""

from __future__ import annotations

import argparse
import socket
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from app import config
from app.domain_logic.url_normalizer import normalize, url_hash, crawl_id
from app.repository.crawl_runtime_repo import CrawlRuntimeRepo
from app.repository.db import db_context
from app.sink.solr_sink import SolrSink
from app.types import CollectedContent

KST = timezone(timedelta(hours=9))

_DUMMY_ITEMS = [
    {
        "url":         "https://example.com/post/1",
        "source_type": "DAUM_NEWS",
        "keyword":     "테스트",
        "keyword_id":  1,
        "title":       "테스트 문서 1",
        "body":        "이것은 Solr 싱크 테스트를 위한 더미 본문입니다. " * 10,
        "author":      "테스터",
    },
    {
        "url":         "https://example.com/post/2",
        "source_type": "NAVER_NEWS",
        "keyword":     "키워드",
        "keyword_id":  2,
        "title":       "테스트 문서 2",
        "body":        "두 번째 더미 콘텐츠입니다. Solr 인덱싱을 검증합니다. " * 10,
        "author":      None,
    },
    {
        "url":         "https://example.com/post/3",
        "source_type": "GOOGLE_NEWS",
        "keyword":     "검색어",
        "keyword_id":  None,
        "title":        "테스트 문서 3 — keyword_id 없음",
        "body":         "keyword_id 가 None 인 경우를 검증합니다. " * 10,
        "author":       None,
    },
]


def _make_content(item: dict) -> CollectedContent:
    norm = normalize(item["url"])
    return CollectedContent(
        url=norm,
        url_hash=url_hash(norm),
        source_type=item["source_type"],
        keyword=item["keyword"],
        keyword_id=item["keyword_id"],
        title=item["title"],
        body=item["body"],
        published_at=None,
        author=item["author"],
        collected_at=datetime.now(KST),
        extraction_method="test",
    )


def _resolve_sink_via_env() -> tuple[SolrSink, str]:
    """환경변수에서 Solr 설정을 읽어 SolrSink 를 생성한다."""
    solr_url = config.SOLR_URL
    if not solr_url:
        raise RuntimeError("SOLR_URL 환경변수가 설정되지 않았습니다.")
    runtime_name = config.SOLR_RUNTIME_NAME
    crawl_runtime_key = f"{socket.gethostname()}_{runtime_name}" if runtime_name else socket.gethostname()
    print(f"[모드] 환경변수  solr_url={solr_url}  crawler_type={config.SOLR_CRAWLER_TYPE}  key={crawl_runtime_key}")
    return SolrSink(solr_url, config.SOLR_CRAWLER_TYPE, crawl_runtime_key), solr_url


def _resolve_sink_via_rdb(engine) -> tuple[SolrSink, str]:
    """t_crawl_runtime 을 조회해 SolrSink 를 생성한다. runtime_name 은 SOLR_RUNTIME_NAME env 사용."""
    runtime_name = config.SOLR_RUNTIME_NAME
    if not runtime_name:
        raise RuntimeError("SOLR_RUNTIME_NAME 환경변수가 설정되지 않았습니다.")
    info = CrawlRuntimeRepo(engine).get_runtime(runtime_name)
    if not info:
        raise RuntimeError(
            f"t_crawl_runtime 에서 runtime_name='{runtime_name}' 을 찾을 수 없거나 use_yn='N' 입니다."
        )
    crawl_runtime_key = f"{socket.gethostname()}_{runtime_name}"
    print(f"[모드] RDB 조회  solr_url={info.solr_url}  crawler_type={info.crawler_type}  key={crawl_runtime_key}")
    return SolrSink(info.solr_url, info.crawler_type, crawl_runtime_key), info.solr_url


def _commit(solr_url: str) -> None:
    resp = httpx.get(f"{solr_url.rstrip('/')}/update", params={"commit": "true"}, timeout=30)
    resp.raise_for_status()


def _verify(solr_url: str, url_hashes: list[str]) -> None:
    ids = " OR ".join(url_hashes)
    resp = httpx.get(
        f"{solr_url.rstrip('/')}/select",
        params={"q": f"id:({ids})", "fl": "id,title,crawler_type,keyword_id", "wt": "json"},
        timeout=10,
    )
    resp.raise_for_status()
    docs = resp.json()["response"]["docs"]
    print(f"\n[검증] 조회된 문서 수: {len(docs)} / {len(url_hashes)}")
    for doc in docs:
        print(f"  id={doc['id'][:16]}  title={doc.get('title', '')[:30]}  "
              f"crawler_type={doc.get('crawler_type')}  keyword_id={doc.get('keyword_id')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Solr 더미 데이터 투입 테스트")
    parser.add_argument(
        "--rdb",
        action="store_true",
        help="RDB 조회 모드: SOLR_RUNTIME_NAME 으로 t_crawl_runtime 을 조회해 Solr 설정을 가져온다.",
    )
    args = parser.parse_args()

    config.validate()
    contents = [_make_content(item) for item in _DUMMY_ITEMS]

    with db_context() as engine:
        if args.rdb:
            sink, solr_url = _resolve_sink_via_rdb(engine)
        else:
            sink, solr_url = _resolve_sink_via_env()

        print(f"[투입] {len(contents)}건 → Solr")
        for content in contents:
            sink.write(content)
        sink.flush()
        print("[완료] flush 성공")

        _commit(solr_url)
        _verify(solr_url, [crawl_id(c.url) for c in contents])


if __name__ == "__main__":
    main()
