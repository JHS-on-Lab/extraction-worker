"""
domain 테이블 접근 — 규칙·정책 조회 + 차단기 상태 갱신.

sparse 테이블: 오버라이드 필요한 도메인만 행이 존재.
행이 없으면 전역 기본값 사용 (호출자가 None 으로 판단).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, text


class DomainRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, host: str) -> dict | None:
        """도메인 행 반환. 행이 없으면 None."""
        with self._engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT host, rules_json, rules_enabled, rules_version,
                           crawl_delay_ms, render_mode, proxy_tier,
                           cooldown_until, recent_fail_count,
                           success_rate, avg_body_len, excluded
                    FROM t_domain
                    WHERE host = :host
                """),
                {"host": host},
            ).fetchone()
        return dict(row._mapping) if row else None

    def upsert_health(self, host: str, success: bool, body_len: int | None) -> None:
        """추출 성공/실패를 반영해 success_rate, avg_body_len, recent_fail_count 갱신."""
        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO t_domain (host, recent_fail_count, success_rate, avg_body_len, updated_at)
                    VALUES (:host, :fail, :rate, :body_len, NOW())
                    ON DUPLICATE KEY UPDATE
                        recent_fail_count = IF(:success, 0, recent_fail_count + 1),
                        success_rate = ROUND(
                            COALESCE(success_rate, 0.5) * 0.9 + :success * 0.1, 4
                        ),
                        avg_body_len = CASE
                            WHEN :body_len IS NOT NULL
                            THEN ROUND(COALESCE(avg_body_len, :body_len) * 0.9 + :body_len * 0.1)
                            ELSE avg_body_len
                        END,
                        updated_at = NOW()
                """),
                {
                    "host":     host,
                    "success":  1 if success else 0,
                    "fail":     0 if success else 1,
                    "rate":     1.0 if success else 0.0,
                    "body_len": body_len,
                },
            )

    def set_cooldown(self, host: str, until: datetime) -> None:
        """차단 감지 시 cooldown_until 세팅."""
        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO t_domain (host, cooldown_until, updated_at)
                    VALUES (:host, :until, NOW())
                    ON DUPLICATE KEY UPDATE
                        cooldown_until = :until,
                        updated_at = NOW()
                """),
                {"host": host, "until": until},
            )
