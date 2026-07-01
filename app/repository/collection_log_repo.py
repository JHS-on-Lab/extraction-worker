"""
collection_log 테이블 접근 — 발견·추출 실행 이력 기록.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, text

KST = timezone(timedelta(hours=9))


@dataclass
class DiscoveryLog:
    keyword_id:    int
    source_type:   str
    worker_id:     str
    started_at:    datetime
    duration_ms:   int
    urls_found:    int
    urls_inserted: int
    urls_skipped:  int
    error_msg:     str | None = None


@dataclass
class ExtractionLog:
    source_type:    str
    worker_id:      str
    started_at:     datetime
    duration_ms:    int
    urls_attempted: int
    urls_success:   int
    urls_failed:    int


class CollectionLogRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_discovery(self, log: DiscoveryLog) -> None:
        run_date = log.started_at.astimezone(KST).date()
        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO t_collection_log
                        (run_type, run_date, keyword_id, source_type, worker_id,
                         started_at, duration_ms,
                         urls_found, urls_inserted, urls_skipped,
                         error_msg)
                    VALUES
                        ('discovery', :run_date, :kid, :source, :worker,
                         :started_at, :duration_ms,
                         :urls_found, :urls_inserted, :urls_skipped,
                         :error_msg)
                """),
                {
                    "run_date":      run_date,
                    "kid":           log.keyword_id,
                    "source":        log.source_type,
                    "worker":        log.worker_id,
                    "started_at":    log.started_at,
                    "duration_ms":   log.duration_ms,
                    "urls_found":    log.urls_found,
                    "urls_inserted": log.urls_inserted,
                    "urls_skipped":  log.urls_skipped,
                    "error_msg":     log.error_msg,
                },
            )

    def insert_extraction(self, log: ExtractionLog) -> None:
        run_date = log.started_at.astimezone(KST).date()
        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO t_collection_log
                        (run_type, run_date, source_type, worker_id,
                         started_at, duration_ms,
                         urls_attempted, urls_success, urls_failed)
                    VALUES
                        ('extraction', :run_date, :source, :worker,
                         :started_at, :duration_ms,
                         :urls_attempted, :urls_success, :urls_failed)
                """),
                {
                    "run_date":      run_date,
                    "source":        log.source_type,
                    "worker":        log.worker_id,
                    "started_at":    log.started_at,
                    "duration_ms":   log.duration_ms,
                    "urls_attempted": log.urls_attempted,
                    "urls_success":   log.urls_success,
                    "urls_failed":    log.urls_failed,
                },
            )

    def count_today_403(self, keyword_id: int) -> int:
        """오늘(KST) 해당 키워드의 403 실패 횟수를 반환한다."""
        with self._engine.connect() as conn:
            return conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM t_collection_log
                    WHERE keyword_id = :kid
                      AND error_msg LIKE '%403%'
                      AND run_date = CURDATE()
                """),
                {"kid": keyword_id},
            ).scalar() or 0

    def count_today_bot_blocked(self, keyword_id: int) -> int:
        """오늘(KST) 해당 키워드의 봇 차단(BotBlockedError) 횟수를 반환한다."""
        with self._engine.connect() as conn:
            return conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM t_collection_log
                    WHERE keyword_id = :kid
                      AND error_msg  LIKE 'BotBlockedError%'
                      AND run_date   = CURDATE()
                """),
                {"kid": keyword_id},
            ).scalar() or 0

    def daily_summary(self, run_date: str | None = None) -> list[dict]:
        """일자별 요약. run_date=None이면 오늘(KST)."""
        date = run_date or datetime.now(KST).date().isoformat()
        with self._engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT
                        cl.run_type,
                        cl.source_type,
                        k.keyword,
                        cl.started_at,
                        cl.duration_ms,
                        cl.urls_found,
                        cl.urls_inserted,
                        cl.urls_skipped,
                        cl.urls_attempted,
                        cl.urls_success,
                        cl.urls_failed
                    FROM t_collection_log cl
                    LEFT JOIN t_keyword k ON k.id = cl.keyword_id
                    WHERE cl.run_date = :date
                    ORDER BY cl.started_at
                """),
                {"date": date},
            ).fetchall()
        return [dict(r._mapping) for r in rows]
