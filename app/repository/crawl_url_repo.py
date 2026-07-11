"""
crawl_url 테이블 접근.

이 테이블은 수집할 URL 의 큐이자 처리 이력이다.
status 컬럼이 각 URL 의 현재 상태를 나타낸다:

  discovered      → 아직 처리 안 됨 (기본값)
  extracting      → 지금 어떤 워커가 처리 중
  stored          → 본문 추출 완료, JSONL 저장됨
  failed_transient→ 일시 오류로 실패. next_retry_at 이 지나면 자동 재시도
  failed_permanent→ 404 등 영구 오류. 재시도 안 함
  dead            → 재시도 횟수(MAX_ATTEMPTS) 초과. 포기

발견 단계: bulk_insert_discovered
추출 단계: claim_next → mark_stored / mark_failed / mark_dead
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import Engine, text

from app import config
from app.domain_logic.url_normalizer import normalize, url_hash
from app.types import ErrorCode

KST = timezone(timedelta(hours=9))


# ON DUPLICATE KEY UPDATE 는 url_hash 가 이미 있으면 아무것도 바꾸지 않는다.
# 중복 URL 을 조용히 무시하기 위한 관용구다.
_INSERT_SQL = text("""
    INSERT INTO t_crawl_url
        (url, url_hash, host, keyword_id, source_type, status,
         attempt_count, is_manual, priority,
         collected_date, created_at, updated_at)
    VALUES
        (:url, :hash, :host, :kid, :source, 'discovered',
         0, false, 0,
         :cdate, :created_at, :created_at)
    ON DUPLICATE KEY UPDATE
        updated_at = updated_at
""")


class CrawlUrlRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # 발견 단계
    # ------------------------------------------------------------------

    def bulk_insert_discovered(
        self,
        raw_urls: list[str],
        keyword_id: int,
        source_type: str,
    ) -> tuple[int, int]:
        """
        URL 목록을 discovered 상태로 bulk insert.
        중복(url_hash)은 ON DUPLICATE KEY UPDATE로 조용히 무시.
        반환: (inserted, skipped)
        """
        if not raw_urls:
            return 0, 0

        now = datetime.now(KST)
        rows = []
        for raw in raw_urls:
            norm = normalize(raw)
            rows.append({
                "url":        norm,
                "hash":       url_hash(norm),
                "host":       urlparse(norm).netloc,
                "kid":        keyword_id,
                "source":     source_type,
                "cdate":      now.date(),
                "created_at": now,
            })

        with self._engine.begin() as conn:
            result = conn.execute(_INSERT_SQL, rows)

        inserted = result.rowcount
        return inserted, len(rows) - inserted

    # ------------------------------------------------------------------
    # 추출 단계
    # ------------------------------------------------------------------

    def claim_next(self, worker_id: str, source: str | None = None) -> dict | None:
        """처리할 URL 하나를 원자적으로 점유한다.

        낙관적 클레임 패턴 (MariaDB 10.5 호환):
          1. 후보 N개를 조회 (잠금 없음)
          2. 각 후보에 대해 UPDATE WHERE status 조건으로 선점 시도
          3. rowcount=1 → 내가 가져간 것 / rowcount=0 → 다른 워커가 먼저 가져간 것 → 다음 후보 시도

        source: 지정하면 해당 source_type 만 처리. None 이면 전체.

        반환:
          - 처리할 URL 이 있으면 → dict (id, url, host, source_type, attempt_count, keyword)
          - 없으면 → None
        """
        source_filter = "AND a.source_type = :source" if source else ""
        params: dict = {"source": source} if source else {}

        with self._engine.begin() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT a.id, a.url, a.host, a.source_type, a.keyword_id,
                           a.attempt_count, a.status, a.next_retry_at,
                           COALESCE(k.keyword, '') AS keyword
                    FROM t_crawl_url a
                    LEFT JOIN t_keyword k ON k.id = a.keyword_id
                    LEFT JOIN t_domain d ON d.host = a.host
                    WHERE (
                        a.status = 'discovered'
                        OR (a.status = 'failed_transient' AND a.next_retry_at <= NOW())
                    )
                    AND (d.cooldown_until IS NULL OR d.cooldown_until <= NOW())
                    AND (d.excluded IS NULL OR d.excluded = 0)
                    {source_filter}
                    ORDER BY a.priority DESC, a.id ASC
                    LIMIT 20
                """),
                params,
            ).fetchall()

            for row in rows:
                item = dict(row._mapping)
                result = conn.execute(
                    text("""
                        UPDATE t_crawl_url
                        SET status = 'extracting',
                            claimed_at = NOW(),
                            claimed_by = :worker,
                            updated_at = NOW()
                        WHERE id = :id
                          AND (
                              status = 'discovered'
                              OR (status = 'failed_transient' AND next_retry_at <= NOW())
                          )
                    """),
                    {"worker": worker_id, "id": item["id"]},
                )
                if result.rowcount == 1:
                    return item

        return None

    def mark_stored(self, item_id: int, extraction_method: str, worker_id: str) -> bool:
        """추출 성공: status=stored, extraction_method 기록.

        WHERE 절에 status='extracting' AND claimed_by=:worker_id 를 명시해, reaper가
        타임아웃으로 이미 회수(discovered로 되돌림)했거나 다른 워커가 다시 집어간
        행을 뒤늦게 덮어쓰지 않는다 — 없으면 느린 워커의 지연 완료가 이미 다른
        워커가 처리 중이거나 완료한 결과를 조용히 덮어쓸 수 있다.
        반환: 실제로 갱신됐으면 True, 소유권을 이미 잃었으면 False.
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE t_crawl_url
                    SET status = 'stored',
                        extraction_method = :method,
                        claimed_at = NULL,
                        claimed_by = NULL,
                        updated_at = NOW()
                    WHERE id = :id AND status = 'extracting' AND claimed_by = :worker_id
                """),
                {"method": extraction_method, "id": item_id, "worker_id": worker_id},
            )
        return result.rowcount > 0

    def mark_failed(
        self,
        item_id: int,
        error_code: ErrorCode,
        error_msg: str,
        is_permanent: bool,
        next_retry_at: datetime | None,
        worker_id: str,
    ) -> bool:
        """
        추출 실패 처리.
        is_permanent=True  → failed_permanent (재시도 없음)
        is_permanent=False → failed_transient + next_retry_at 세팅

        mark_stored 와 동일한 이유로 claim 소유권(status='extracting' AND
        claimed_by=:worker_id)을 확인한다. 반환: 실제로 갱신됐으면 True.
        """
        status = "failed_permanent" if is_permanent else "failed_transient"
        with self._engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE t_crawl_url
                    SET status          = :status,
                        attempt_count   = attempt_count + 1,
                        last_error_code = :code,
                        last_error_msg  = :msg,
                        next_retry_at   = :retry_at,
                        claimed_at      = NULL,
                        claimed_by      = NULL,
                        updated_at      = NOW()
                    WHERE id = :id AND status = 'extracting' AND claimed_by = :worker_id
                """),
                {
                    "status":   status,
                    "code":     error_code.value,
                    "msg":      error_msg[:500],
                    "retry_at": next_retry_at,
                    "id":       item_id,
                    "worker_id": worker_id,
                },
            )
        return result.rowcount > 0

    def mark_dead(self, item_id: int, error_code: ErrorCode, error_msg: str, worker_id: str) -> bool:
        """최대 시도 횟수 초과: status=dead.

        mark_stored 와 동일한 이유로 claim 소유권을 확인한다. 반환: 실제로 갱신됐으면 True.
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE t_crawl_url
                    SET status          = 'dead',
                        attempt_count   = attempt_count + 1,
                        last_error_code = :code,
                        last_error_msg  = :msg,
                        claimed_at      = NULL,
                        claimed_by      = NULL,
                        updated_at      = NOW()
                    WHERE id = :id AND status = 'extracting' AND claimed_by = :worker_id
                """),
                {"code": error_code.value, "msg": error_msg[:500], "id": item_id, "worker_id": worker_id},
            )
        return result.rowcount > 0

    def recover_timed_out(self, timeout_seconds: int) -> int:
        """
        status=extracting 이고 claimed_at 이 timeout_seconds 초 이상 지난 행을 회수한다.
        (reaper 전용)

        attempt_count 를 증가시키고, 그 결과 MAX_ATTEMPTS 에 도달하면 dead 로,
        아니면 discovered 로 되돌린다. attempt_count 를 안 늘리면 구조적으로 항상
        타임아웃나는 URL(예: 특정 페이지에서 headless 가 매번 멈추는 경우)이
        MAX_ATTEMPTS/dead 도달 없이 reaper 에 의해 영원히 재시도될 수 있었다.
        반환: 회수된 행 수
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE t_crawl_url
                    SET status = CASE WHEN attempt_count + 1 >= :max_attempts THEN 'dead' ELSE 'discovered' END,
                        attempt_count   = attempt_count + 1,
                        last_error_code = :code,
                        last_error_msg  = :msg,
                        claimed_at      = NULL,
                        claimed_by      = NULL,
                        updated_at      = NOW()
                    WHERE status = 'extracting'
                      AND claimed_at < NOW() - INTERVAL :sec SECOND
                """),
                {
                    "sec": timeout_seconds,
                    "max_attempts": config.MAX_ATTEMPTS,
                    "code": ErrorCode.UNKNOWN.value,
                    "msg": "claim timeout (reaper 회수)",
                },
            )
        return result.rowcount

    def requeue(
        self,
        *,
        statuses: list[str],
        host: str | None = None,
        error_code: str | None = None,
    ) -> int:
        """
        조건에 맞는 실패 URL 을 discovered 로 재투입한다.
        반환: 재투입된 행 수
        """
        filters = ["status IN :statuses"]
        params: dict = {"statuses": tuple(statuses)}

        if host:
            filters.append("host = :host")
            params["host"] = host
        if error_code:
            filters.append("last_error_code = :code")
            params["code"] = error_code

        where = " AND ".join(filters)
        with self._engine.begin() as conn:
            result = conn.execute(
                text(f"""
                    UPDATE t_crawl_url
                    SET status        = 'discovered',
                        next_retry_at = NULL,
                        updated_at    = NOW()
                    WHERE {where}
                """),
                params,
            )
        return result.rowcount

    def status_summary(self) -> list[dict]:
        """전체 status별 건수 (운영 확인용)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text("SELECT status, COUNT(*) as cnt FROM t_crawl_url GROUP BY status ORDER BY cnt DESC")
            ).fetchall()
        return [dict(r._mapping) for r in rows]
