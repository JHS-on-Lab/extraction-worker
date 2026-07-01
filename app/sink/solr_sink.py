"""
Solr 싱크 — CollectedContent 을 Solr 에 upsert 한다.

url_hash 를 Solr 문서 id 로 사용한다 (같은 URL 을 다시 넣어도 안전하게 덮어써짐).

설정 (.env):
  SINK_TYPE=solr
  SOLR_URL=http://localhost:8983/solr/news
  SOLR_BATCH_SIZE=100            (선택, 기본 100)
  SOLR_COMMIT_WITHIN_MS=5000     (선택, 기본 5000)
  SOLR_CONNECT_TIMEOUT_S=5       (선택, 기본 5) — TCP 연결 타임아웃
  SOLR_READ_TIMEOUT_S=30         (선택, 기본 30) — 응답 수신 타임아웃

장애 처리 (circuit breaker):
  연속 3회 flush 실패 시 circuit 을 열어 즉시 SinkUnavailableError 를 반환한다.
  backoff 는 60s 로 시작해 실패가 누적될수록 2배씩 증가(최대 3600s).
  복구 후 첫 번째 성공 시 circuit 이 닫힌다.

SOLR_COMMIT_WITHIN_MS:
  flush 마다 commit=true 를 보내면 다수 컨테이너가 동시에 flush 할 때 하드 커밋이
  직렬화되어 병목이 생긴다. commitWithin 으로 커밋 타이밍을 Solr 에 위임한다.
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from app import config
from app.sink.serialize import to_solr_doc
from app.types import CollectedContent, SinkUnavailableError

_log = logging.getLogger(__name__)

_CIRCUIT_THRESHOLD = 3     # 연속 실패 N회 시 circuit open
_CIRCUIT_BASE_S    = 60    # 첫 backoff 시간 (초), 이후 2배씩 증가
_CIRCUIT_MAX_S     = 3600  # 최대 backoff 시간 (초)


class SolrSink:
    """CollectedContent 을 Solr 코어에 JSON 으로 upsert 한다."""

    def __init__(self, solr_url: str, crawler_type: str, crawl_runtime_key: str) -> None:
        self._url               = solr_url.rstrip("/")
        self._crawler_type      = crawler_type
        self._crawl_runtime_key = crawl_runtime_key
        self._batch_size        = config.SOLR_BATCH_SIZE
        self._buffer: list[dict] = []
        self._timeout           = httpx.Timeout(
            connect=config.SOLR_CONNECT_TIMEOUT_S,
            read=config.SOLR_READ_TIMEOUT_S,
            write=config.SOLR_READ_TIMEOUT_S,
            pool=config.SOLR_CONNECT_TIMEOUT_S,
        )
        self._consecutive_failures = 0
        self._circuit_open_until   = 0.0  # monotonic timestamp

    def write(self, content: CollectedContent) -> None:
        self._buffer.append(
            to_solr_doc(content, self._crawler_type, self._crawl_runtime_key)
        )
        if len(self._buffer) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return

        # circuit open: timeout 대기 없이 즉시 실패
        if time.monotonic() < self._circuit_open_until:
            remaining = int(self._circuit_open_until - time.monotonic())
            self._buffer.clear()
            raise SinkUnavailableError(
                f"Solr circuit open — {remaining}s 후 재시도 "
                f"(연속 {self._consecutive_failures}회 실패)"
            )

        # flush 실패 시 docs 재누적을 막기 위해 전송 전에 buffer 를 비운다.
        # URL 은 extraction_worker 에서 failed_transient 로 표시되어 나중에 재처리된다.
        docs = list(self._buffer)
        self._buffer.clear()

        try:
            resp = httpx.post(
                f"{self._url}/update",
                params={"commitWithin": str(config.SOLR_COMMIT_WITHIN_MS)},
                content=json.dumps(docs, ensure_ascii=False, default=str),
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            self._consecutive_failures = 0

        except Exception:
            self._consecutive_failures += 1
            if self._consecutive_failures >= _CIRCUIT_THRESHOLD:
                backoff = min(
                    _CIRCUIT_BASE_S * (2 ** (self._consecutive_failures - _CIRCUIT_THRESHOLD)),
                    _CIRCUIT_MAX_S,
                )
                self._circuit_open_until = time.monotonic() + backoff
                _log.warning(
                    "Solr circuit opened: %d consecutive failures — pausing %ds",
                    self._consecutive_failures, int(backoff),
                )
            raise

    def __enter__(self) -> "SolrSink":
        return self

    def __exit__(self, *_) -> None:
        try:
            self.flush()
        except Exception:
            _log.warning("SolrSink flush on exit failed — %d docs may be lost", len(self._buffer))
