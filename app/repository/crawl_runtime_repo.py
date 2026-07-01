"""
t_crawl_runtime 테이블 조회.

runtime_name 으로 Solr 접속 정보를 가져온다.
use_yn = 'Y' 인 행만 유효한 것으로 간주한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text


@dataclass
class RuntimeInfo:
    solr_url:     str
    crawler_type: str


class CrawlRuntimeRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_runtime(self, runtime_name: str) -> RuntimeInfo | None:
        """
        runtime_name 에 해당하는 Solr 접속 정보를 반환한다.
        행이 없거나 use_yn='N' 이면 None.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT solr_url, crawler_type
                    FROM t_crawl_runtime
                    WHERE runtime_name = :name
                      AND use_yn = 'Y'
                """),
                {"name": runtime_name},
            ).fetchone()
        if not row:
            return None
        return RuntimeInfo(solr_url=row[0], crawler_type=row[1] or "")
