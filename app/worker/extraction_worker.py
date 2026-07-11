"""
추출 워커: crawl_url 테이블에서 URL 을 꺼내 본문을 스크랩하고 파일로 저장한다.

한 URL 의 처리 순서:
  1. claim_next()  — DB 에서 URL 하나를 꺼낸다 (다른 워커가 동시에 같은 URL 을 가져가지 않도록 잠금)
  2. RateLimiter   — 같은 도메인에 너무 빨리 요청하지 않도록 대기
  3. HttpFetcher   — URL 의 HTML 을 내려받는다
  4. Extractor     — HTML 에서 제목·본문을 추출한다
  5. Sink          — 결과를 저장한다 (FileSink: JSONL, SolrSink: Solr)
  6. mark_stored / mark_failed / mark_dead — 처리 결과를 DB 에 기록한다

실패 처리:
  - 일시적 오류(네트워크 장애, 서버 500 등) → failed_transient 로 표시, 나중에 자동 재시도
  - 영구 오류(404, 페이월 등)               → failed_permanent 로 표시, 재시도 안 함
  - MAX_ATTEMPTS 초과                       → dead 로 표시

URL 이 없으면 10초 쉬었다가 다시 확인한다. 수동 개입 없이 계속 돌아간다.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from app import config
from app.worker import _healthcheck
from app.domain_logic.backoff import next_retry_at
from app.domain_logic.failure_classifier import classify_http, classify_exception
from app.extraction.extractor import DefaultExtractor
from app.fetch.headless import HeadlessFetcher, fetch_by_render_mode
from app.fetch.http_client import HttpFetcher
from app.fetch.rate_limit import RateLimiter
from app.repository.crawl_url_repo import CrawlUrlRepo
from app.repository.collection_log_repo import CollectionLogRepo, ExtractionLog
from app.repository.db import db_context
from app.repository.domain_repo import DomainRepo
from app.sink import make_sink
from app.ports import Sink
from app.types import ErrorCode, ExtractionFailure, RenderMode

logger = logging.getLogger(__name__)

KST        = timezone(timedelta(hours=9))
_IDLE_SEC  = 10
_ERROR_SEC = 5


@dataclass
class _PendingStore:
    """sink.write() 는 성공했지만 아직 flush() 로 확정되지 않은 항목.

    sink.write() 직후 바로 mark_stored 를 부르면, SolrSink 처럼 write()가 버퍼링만
    하는 구현에서 나중에 flush() 가 실패했을 때 DB엔 stored인데 실제 저장소엔 없는
    문서가 생긴다(관찰된 Critical 버그). flush() 가 성공을 확인해준 뒤에야
    mark_stored 를 부르기 위해 이 목록에 잠시 쌓아둔다.
    """
    item_id: int
    extraction_method: str
    attempt: int


def run_extraction_loop(source: str, worker_id: str) -> None:
    """추출 워커 메인 루프. __main__.py 에서 호출."""
    logger.info(
        f"extraction loop started source={source}",
        extra={"phase": "startup", "worker_id": worker_id, "component": "extractor"},
    )

    # HeadlessFetcher 는 브라우저 프로세스를, HttpFetcher 는 httpx.Client(커넥션 풀)를
    # 루프 밖에서 한 번만 생성해 재사용한다.
    with HeadlessFetcher() as headless_fetcher, HttpFetcher() as fetcher, db_context() as engine:
        url_repo    = CrawlUrlRepo(engine)
        log_repo    = CollectionLogRepo(engine)
        domain_repo = DomainRepo(engine)
        limiter     = RateLimiter(domain_repo)
        extractor   = DefaultExtractor(domain_repo=domain_repo)  # domain_repo 주입 → 규칙 엔진 활성화
        sink        = make_sink(engine)  # SINK_TYPE 환경변수로 file / solr 선택

        processed = urls_success = urls_failed = 0
        heartbeat_interval  = config.HEARTBEAT_INTERVAL_SECONDS
        last_heartbeat      = time.monotonic()
        batch_start_dt      = datetime.now(KST)
        batch_start_mono    = time.monotonic()
        source_filter       = None if source.upper() == "ALL" else source.upper()

        # sink.write() 로 버퍼링만 되고 아직 flush() 로 확정 안 된 항목들.
        # sink.batch_size 개 쌓이면, 또는 idle/heartbeat/종료 시점에 flush 한다.
        pending: list[_PendingStore] = []

        try:
            while True:
                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    logger.info(
                        f"heartbeat processed={processed} success={urls_success} failed={urls_failed}",
                        extra={"phase": "heartbeat", "worker_id": worker_id, "component": "extractor"},
                    )
                    last_heartbeat = now
                    _healthcheck.write()
                    # 안전망 — 배치가 안 찰 만큼 처리량이 적어도 heartbeat 주기마다는 flush 되게 한다.
                    _flush_pending(sink, url_repo, pending, worker_id)
                    _flush_log(log_repo, source, worker_id,
                               batch_start_dt, batch_start_mono,
                               processed, urls_success, urls_failed)
                    processed = urls_success = urls_failed = 0
                    batch_start_dt   = datetime.now(KST)
                    batch_start_mono = time.monotonic()

                try:
                    item = url_repo.claim_next(worker_id=worker_id, source=source_filter)
                except Exception:
                    logger.exception(
                        f"claim_next failed, sleeping {_ERROR_SEC}s",
                        extra={"phase": "claim", "worker_id": worker_id, "component": "extractor"},
                    )
                    time.sleep(_ERROR_SEC)
                    continue

                if item is None:
                    # idle 상태에도 안 쌓인 채로 오래 방치되지 않게 flush.
                    _flush_pending(sink, url_repo, pending, worker_id)
                    _flush_log(log_repo, source, worker_id,
                               batch_start_dt, batch_start_mono,
                               processed, urls_success, urls_failed)
                    processed = urls_success = urls_failed = 0
                    batch_start_dt   = datetime.now(KST)
                    batch_start_mono = time.monotonic()
                    logger.debug(
                        f"no items, sleeping {_IDLE_SEC}s",
                        extra={"phase": "idle", "worker_id": worker_id, "component": "extractor"},
                    )
                    time.sleep(_IDLE_SEC)
                    continue

                success, store_info = _process_one(
                    item, url_repo, domain_repo,
                    fetcher, headless_fetcher, limiter, extractor, sink, worker_id
                )
                processed += 1
                if store_info is not None:
                    pending.append(store_info)
                    if len(pending) >= sink.batch_size:
                        _flush_pending(sink, url_repo, pending, worker_id)
                if success:
                    urls_success += 1
                else:
                    urls_failed += 1
        finally:
            # 정상 종료·예외·SIGTERM(sys.exit) 어느 경로로 빠져나가든 버퍼에 남은
            # 항목을 반드시 flush 하거나(성공) failed_transient 로 되돌린다(실패) —
            # 그냥 두면 DB엔 아무 기록도 없이(여전히 extracting) 그 항목들이 유실된다.
            _flush_pending(sink, url_repo, pending, worker_id)


def _flush_pending(
    sink: Sink,
    url_repo: CrawlUrlRepo,
    pending: list[_PendingStore],
    worker_id: str,
) -> None:
    """pending 에 쌓인 항목을 sink.flush() 로 확정 짓는다.

    flush 성공 → 그제서야 DB 를 stored 로 표시(mark_stored).
    flush 실패(circuit open 포함) → 이 배치 전체를 failed_transient 로 되돌려
    나중에 재시도되게 한다. write() 직후 바로 mark_stored 를 부르던 예전 방식은
    flush 가 나중에 실패하면 DB엔 stored인데 실제 저장소엔 없는 문서가 생겼다.
    """
    if not pending:
        return

    try:
        sink.flush()
    except Exception as exc:
        logger.warning(
            f"sink flush 실패 — {len(pending)}건 failed_transient 처리: {exc}",
            extra={"phase": "sink_flush_error", "worker_id": worker_id, "component": "extractor"},
        )
        for p in pending:
            ok = url_repo.mark_failed(
                p.item_id, ErrorCode.UNKNOWN, f"sink flush failed: {exc}",
                is_permanent=False, next_retry_at=next_retry_at(p.attempt),
                worker_id=worker_id,
            )
            if not ok:
                logger.warning(
                    f"mark_failed 스킵됨(claim 소유권 상실) item_id={p.item_id}",
                    extra={"phase": "sink_flush_error", "worker_id": worker_id, "component": "extractor"},
                )
        pending.clear()
        return

    for p in pending:
        ok = url_repo.mark_stored(p.item_id, extraction_method=p.extraction_method, worker_id=worker_id)
        if not ok:
            logger.warning(
                f"mark_stored 스킵됨(claim 소유권 상실, 이미 다른 워커가 처리했거나 reaper가 회수함) "
                f"item_id={p.item_id} — Solr 에는 이미 반영됐으나 이 워커의 DB 갱신은 무시됨",
                extra={"phase": "claim_lost", "worker_id": worker_id, "component": "extractor"},
            )
    pending.clear()


def _process_one(
    item: dict,
    url_repo: CrawlUrlRepo,
    domain_repo: DomainRepo,
    fetcher: HttpFetcher,
    headless_fetcher: "HeadlessFetcher",
    limiter: RateLimiter,
    extractor: DefaultExtractor,
    sink: Sink,
    worker_id: str,
) -> tuple[bool, _PendingStore | None]:
    """URL 하나를 처리한다.

    반환: (success, store_info)
      - 실패 시 (False, None) — 이미 mark_failed/mark_dead 로 DB 반영 완료.
      - sink 기록 성공 시 (True, PendingStore) — 아직 DB 는 stored 로 표시 안 됨.
        호출측이 _flush_pending() 으로 flush 확인 후 실제로 stored 표시해야 한다.
    """
    item_id    = item["id"]
    url        = item["url"]
    host       = item["host"]
    source     = item["source_type"]
    keyword    = item.get("keyword", "")
    keyword_id = item.get("keyword_id")
    attempt    = item["attempt_count"]

    extra = {
        "phase": "extract", "worker_id": worker_id,
        "host": host, "url_id": str(item_id), "component": "extractor",
        "keyword_id": str(keyword_id) if keyword_id is not None else "-",
    }

    domain = domain_repo.get(host)

    # 레이트 리밋
    limiter.wait(host)

    render_mode = (domain or {}).get("render_mode", RenderMode.STATIC)
    raw_rules = domain.get("rules_json") if domain else None
    if isinstance(raw_rules, str):
        try:
            raw_rules = json.loads(raw_rules)
        except Exception:
            raw_rules = None
    wait_for_selector = (raw_rules or {}).get("headless_wait_for")

    # force_http: HTTPS 접속이 안 되고 HTTP 만 정상 응답하는 도메인용.
    # t_crawl_url 에는 원래 스킴(대개 https)으로 저장돼 있으므로 fetch 직전에만 downgrade.
    if (raw_rules or {}).get("force_http") and url.startswith("https://"):
        url = "http://" + url[len("https://"):]

    # legacy_renegotiation: 구형 TLS 재협상을 요구하는 서버용. OpenSSL 3.x가
    # 기본 거부(UNSAFE_LEGACY_RENEGOTIATION_DISABLED)하는 걸 우회한다.
    allow_legacy_renegotiation = bool((raw_rules or {}).get("legacy_renegotiation"))

    try:
        fr = fetch_by_render_mode(url, render_mode, fetcher, headless_fetcher,
                                  wait_for_selector=wait_for_selector,
                                  allow_legacy_renegotiation=allow_legacy_renegotiation)
    except Exception as exc:
        error_code, is_permanent = classify_exception(exc)
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.warning(
            f"fetch error url={url}",
            extra={**extra, "error_code": error_code.value},
        )
        _handle_failure(url_repo, domain_repo, item_id, host, attempt,
                        error_code, error_msg, is_permanent, worker_id)
        return False, None

    if fr.status_code >= 400:
        error_code, is_permanent = classify_http(fr.status_code)
        error_msg = f"HTTP {fr.status_code}"
        logger.warning(
            f"fetch {error_msg} url={url}",
            extra={**extra, "error_code": error_code.value},
        )
        _handle_failure(url_repo, domain_repo, item_id, host, attempt,
                        error_code, error_msg, is_permanent, worker_id)
        return False, None

    # Extract
    result = extractor.extract(
        url=fr.url, html=fr.html, host=host,
        source_type=source, keyword=keyword, keyword_id=keyword_id,
    )

    if isinstance(result, ExtractionFailure):
        logger.warning(
            f"extract failed url={url} msg={result.error_msg}",
            extra={**extra, "error_code": result.error_code.value},
        )
        _handle_failure(url_repo, domain_repo, item_id, host, attempt,
                        result.error_code, result.error_msg, result.is_permanent, worker_id)
        return False, None

    # 본문 추출 자체는 성공했다 — 이후 sink 기록의 성패와 무관하게 이 host 는
    # fetch/extract 관점에서 정상이었으므로 여기서 바로 반영한다(예전엔 sink.write()
    # 이후에 호출돼서, sink 오류 시 성공/실패 어느 쪽으로도 health 가 갱신 안 됐다).
    domain_repo.upsert_health(host, success=True, body_len=result.body_len)

    # Sink — write() 는 버퍼링만 할 수 있다(SolrSink). 실제 저장 확정은 호출측이
    # _flush_pending() 으로 처리하므로, 여기서는 mark_stored 를 부르지 않는다.
    try:
        sink.write(result)
    except Exception as exc:
        logger.exception(f"sink write failed url={url}", extra=extra)
        url_repo.mark_failed(
            item_id,
            error_code=ErrorCode.UNKNOWN,
            error_msg=f"sink error: {exc}",
            is_permanent=False,
            next_retry_at=next_retry_at(attempt),
            worker_id=worker_id,
        )
        return False, None

    logger.info(
        f"buffered url={url} method={result.extraction_method} body={result.body_len} (flush 대기)",
        extra=extra,
    )
    return True, _PendingStore(item_id=item_id, extraction_method=result.extraction_method, attempt=attempt)


def _handle_failure(
    url_repo: CrawlUrlRepo,
    domain_repo: DomainRepo,
    item_id: int,
    host: str,
    attempt: int,
    error_code: ErrorCode,
    error_msg: str,
    is_permanent: bool,
    worker_id: str,
) -> None:
    """실패를 기록하고 다음 상태를 결정한다.

    결정 순서:
      1. 시도 횟수가 MAX_ATTEMPTS 에 도달하면 → dead (더 이상 재시도 없음)
      2. 영구 오류(404 등) 이면              → failed_permanent (재시도 없음)
      3. 그 외 일시 오류                     → failed_transient (백오프 후 재시도)
    """
    domain_repo.upsert_health(host, success=False, body_len=None)

    if attempt + 1 >= config.MAX_ATTEMPTS:
        url_repo.mark_dead(item_id, error_code, error_msg, worker_id=worker_id)
    elif is_permanent:
        url_repo.mark_failed(item_id, error_code, error_msg, True, None, worker_id=worker_id)
    else:
        url_repo.mark_failed(item_id, error_code, error_msg, False,
                             next_retry_at=next_retry_at(attempt), worker_id=worker_id)


def _flush_log(
    log_repo: CollectionLogRepo,
    source: str,
    worker_id: str,
    started_at: datetime,
    started_mono: float,
    attempted: int,
    success: int,
    failed: int,
) -> None:
    if attempted == 0:
        return
    duration_ms = int((time.monotonic() - started_mono) * 1000)
    try:
        log_repo.insert_extraction(ExtractionLog(
            source_type    = source,
            worker_id      = worker_id,
            started_at     = started_at,
            duration_ms    = duration_ms,
            urls_attempted = attempted,
            urls_success   = success,
            urls_failed    = failed,
        ))
    except Exception:
        logger.exception("failed to write extraction log")
