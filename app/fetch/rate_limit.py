"""
도메인별 요청 속도 제한 (Rate Limiter).

같은 도메인에 요청을 너무 빠르게 보내면 서버에서 429(Too Many Requests) 나
IP 차단이 발생할 수 있다. crawl_delay_ms 만큼 간격을 두고 요청한다.

딜레이 값 우선순위:
  1. domain 테이블의 crawl_delay_ms (도메인별 커스텀 설정)
  2. config.DEFAULT_CRAWL_DELAY_MS (전역 기본값)

마지막 요청 시각을 메모리(self._last)에 저장해 두고,
다음 요청 전에 '남은 대기 시간'만큼 sleep 한다.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from app import config
from app.repository.domain_repo import DomainRepo

_MAX_HOSTS = 10_000   # 이 수를 초과하면 가장 오래된 항목부터 제거한다


class RateLimiter:
    def __init__(self, domain_repo: DomainRepo) -> None:
        self._repo = domain_repo
        self._last: OrderedDict[str, float] = OrderedDict()

    def wait(self, host: str) -> None:
        """요청 전에 host 별 딜레이만큼 대기한다."""
        delay_ms = self._get_delay_ms(host)
        last = self._last.get(host)

        if last is None:
            self._record(host)
            return

        if delay_ms > 0:
            elapsed_ms = (time.monotonic() - last) * 1000
            remaining_ms = delay_ms - elapsed_ms
            if remaining_ms > 0:
                time.sleep(remaining_ms / 1000)

        self._record(host)

    def _record(self, host: str) -> None:
        """마지막 요청 시각을 기록하고, 한도 초과 시 가장 오래된 항목을 제거한다."""
        self._last[host] = time.monotonic()
        self._last.move_to_end(host)
        if len(self._last) > _MAX_HOSTS:
            self._last.popitem(last=False)

    def _get_delay_ms(self, host: str) -> int:
        domain = self._repo.get(host)
        if domain and domain.get("crawl_delay_ms") is not None:
            return int(domain["crawl_delay_ms"])
        return config.DEFAULT_CRAWL_DELAY_MS
