"""
재시도 대기 시간 계산.

실패가 거듭될수록 더 오래 기다린다 (지수 백오프):
  attempt 0 → 약  30초
  attempt 1 → 약  60초
  attempt 2 → 약 120초
  attempt 3 → 약 240초
  ...최대 BACKOFF_MAX_SECONDS (기본 3600초 = 1시간)

jitter(무작위 편차)를 더하는 이유:
  여러 URL 이 같은 시각에 일제히 재시도하면 서버에 부담을 준다.
  jitter 로 재시도 시각을 분산시킨다.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app import config

KST = timezone(timedelta(hours=9))


def next_retry_at(attempt_count: int) -> datetime:
    """다음 재시도 시각을 계산해 반환한다."""
    delay = min(
        config.BACKOFF_BASE_SECONDS * (2 ** attempt_count),
        config.BACKOFF_MAX_SECONDS,
    )
    jitter = random.uniform(0, delay * 0.2)
    return datetime.now(KST) + timedelta(seconds=delay + jitter)
