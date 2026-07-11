"""
추출(Extraction) 단계 수동 실행 스크립트.

사용법:
  # 특정 URL 추출 테스트 — 파일 미저장, 결과만 출력
  python scripts/run_extraction.py --url "https://finance.naver.com/item/board_read.naver?code=000660&nid=421731371" --dry-run

  # 특정 URL 추출 + 저장 (SINK_TYPE 환경변수에 따라 file/solr)
  python scripts/run_extraction.py --url "https://..." --source NAVER_STOCK --keyword 000660

  # DB 에서 discovered URL 하나 꺼내 추출
  python scripts/run_extraction.py

  # 특정 소스 URL 만 꺼내 추출
  python scripts/run_extraction.py --source NAVER_NEWS
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import config


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="추출 단계 수동 실행")
    p.add_argument("--url",      default=None, help="직접 지정 URL (생략 시 DB 에서 꺼냄)")
    p.add_argument("--source",   default=None, help="소스 타입 (예: NAVER_STOCK)")
    p.add_argument("--keyword",  default="",   help="키워드 컨텍스트 (기본: 빈 문자열)")
    p.add_argument("--dry-run",  action="store_true", help="파일 미저장, 결과만 출력")
    p.add_argument("--worker-id", default="script", help="워커 식별자 (기본: script)")
    return p.parse_args()


def _make_components(engine, dry_run: bool):
    """추출에 필요한 컴포넌트를 생성해 반환한다."""
    from app.extraction.extractor import DefaultExtractor
    from app.fetch.headless import HeadlessFetcher
    from app.fetch.http_client import HttpFetcher
    from app.fetch.rate_limit import RateLimiter
    from app.repository.domain_repo import DomainRepo
    from app.sink import make_sink

    domain_repo = DomainRepo(engine)
    fetcher     = HttpFetcher()
    headless    = HeadlessFetcher()
    limiter     = RateLimiter(domain_repo)
    extractor   = DefaultExtractor(domain_repo=domain_repo)
    sink        = make_sink(engine) if not dry_run else None

    return domain_repo, fetcher, headless, limiter, extractor, sink


def _run_url_mode(args: argparse.Namespace) -> None:
    """URL 직접 지정 모드."""
    from app.fetch.headless import fetch_by_render_mode
    from app.types import ExtractionFailure, RenderMode
    from app.repository.db import db_context

    url  = args.url
    host = urlparse(url).netloc

    print(f"URL    : {url}")
    print(f"host   : {host}")
    print(f"source : {args.source or '(미지정)'}")
    print(f"keyword: {args.keyword or '(없음)'}")
    print(f"mode   : {'dry-run' if args.dry_run else '저장'}\n")

    if not args.dry_run:
        config.validate()

    with db_context() as engine:
        (domain_repo, fetcher, headless,
         limiter, extractor, sink) = _make_components(engine, args.dry_run)
        try:
            domain = domain_repo.get(host)
            render_mode = (domain or {}).get("render_mode", RenderMode.STATIC)
            print(f"render_mode : {render_mode}")
            wait_for_selector = None
            if domain and domain.get("rules_enabled") and domain.get("rules_json"):
                import json as _json
                rules = domain["rules_json"]
                if isinstance(rules, str):
                    rules = _json.loads(rules)
                rule_type = next((t for t in ("json_api", "amp_url", "next_data") if t in rules), "css/xpath")
                wait_for_selector = rules.get("headless_wait_for")
                print(f"domain rule : {rule_type}\n")
            elif domain and domain.get("rules_json") and not domain.get("rules_enabled"):
                print("domain rule : 있으나 rules_enabled=False → 라이브러리 체인\n")
            else:
                print("domain rule : 없음 (라이브러리 체인)\n")

            limiter.wait(host)

            print("=== Fetch ===")
            fr = fetch_by_render_mode(url, render_mode, fetcher, headless,
                                      wait_for_selector=wait_for_selector)
            print(f"status : {fr.status_code}")
            print(f"html   : {len(fr.html):,} bytes\n")

            if fr.status_code >= 400:
                print(f"오류: HTTP {fr.status_code}")
                return

            print("=== Extract ===")
            result = extractor.extract(
                url=fr.url,
                html=fr.html,
                host=host,
                source_type=args.source or "",
                keyword=args.keyword,
            )

            if isinstance(result, ExtractionFailure):
                print(f"실패: [{result.error_code.value}] {result.error_msg}")
                print(f"      permanent={result.is_permanent}")
                return

            print(f"method      : {result.extraction_method}")
            print(f"title       : {result.title}")
            print(f"author      : {result.author}")
            print(f"published_at: {result.published_at}")
            print(f"body_len    : {result.body_len}")
            print(f"body:\n{result.body}")

            if args.dry_run:
                print("\n(dry-run — 파일 미저장)")
                return

            print("\n=== Sink ===")
            sink.write(result)
            print("저장 완료")
        finally:
            headless.close()


def _run_db_mode(args: argparse.Namespace) -> None:
    """DB 에서 discovered URL 하나를 꺼내 추출한다."""
    from app.fetch.headless import fetch_by_render_mode
    from app.repository.crawl_url_repo import CrawlUrlRepo
    from app.repository.db import db_context
    from app.types import ExtractionFailure, RenderMode

    config.validate()

    with db_context() as engine:
        (domain_repo, fetcher, headless,
         limiter, extractor, sink) = _make_components(engine, args.dry_run)
        try:
            url_repo = CrawlUrlRepo(engine)
            source_filter = args.source.upper() if args.source else None
            item = url_repo.claim_next(worker_id=args.worker_id, source=source_filter)

            if item is None:
                print(f"처리할 discovered URL 없음 (source={args.source or 'all'})")
                return

            url     = item["url"]
            host    = item["host"]
            source  = item["source_type"]
            keyword = item.get("keyword", "")

            print(f"URL    : {url}")
            print(f"host   : {host}")
            print(f"source : {source}")
            print(f"id     : {item['id']}\n")

            domain = domain_repo.get(host)
            render_mode = (domain or {}).get("render_mode", RenderMode.STATIC)

            limiter.wait(host)

            print("=== Fetch ===")
            try:
                fr = fetch_by_render_mode(url, render_mode, fetcher, headless)
            except Exception as exc:
                print(f"fetch 오류: {exc}")
                from app.types import ErrorCode
                url_repo.mark_failed(item["id"], error_code=ErrorCode.UNKNOWN,
                                     error_msg=str(exc), is_permanent=False,
                                     next_retry_at=None, worker_id=args.worker_id)
                return

            print(f"status : {fr.status_code}")
            print(f"html   : {len(fr.html):,} bytes\n")

            if fr.status_code >= 400:
                print(f"오류: HTTP {fr.status_code}")
                return

            print("=== Extract ===")
            result = extractor.extract(
                url=fr.url, html=fr.html, host=host,
                source_type=source, keyword=keyword,
            )

            if isinstance(result, ExtractionFailure):
                print(f"실패: [{result.error_code.value}] {result.error_msg}")
                url_repo.mark_failed(item["id"], error_code=result.error_code,
                                     error_msg=result.error_msg,
                                     is_permanent=result.is_permanent,
                                     next_retry_at=None, worker_id=args.worker_id)
                return

            print(f"method      : {result.extraction_method}")
            print(f"title       : {result.title}")
            print(f"author      : {result.author}")
            print(f"published_at: {result.published_at}")
            print(f"body_len    : {result.body_len}")
            print(f"body:\n{result.body}")

            if args.dry_run:
                print("\n(dry-run — 파일 미저장, DB 상태 미변경)")
                return

            print("\n=== Sink ===")
            sink.write(result)
            # 이 스크립트는 단발성 실행이라 배치를 기다릴 이유가 없다 — write() 직후
            # 바로 flush() 해서 실제로 저장소에 반영됐는지 확인하고, 성공했을 때만
            # stored 로 표시한다(안 그러면 SolrSink 는 버퍼링만 하고 끝나 실제로는
            # Solr 에 안 들어갔는데 DB만 stored 로 남는다).
            sink.flush()
            url_repo.mark_stored(item["id"], extraction_method=result.extraction_method, worker_id=args.worker_id)
            domain_repo.upsert_health(host, success=True, body_len=result.body_len)
            print("저장 완료.")
        finally:
            headless.close()


def main() -> None:
    args = _parse_args()

    if args.url:
        _run_url_mode(args)
    else:
        _run_db_mode(args)


if __name__ == "__main__":
    main()
