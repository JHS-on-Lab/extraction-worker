"""
점유 회수기 (Reaper).

문제 상황:
  추출 워커가 URL 을 꺼내 처리 중(status=extracting)에 갑자기 프로세스가 죽으면
  그 URL 은 extracting 상태로 영원히 남아 다른 워커가 건드리지 않는다.

해결:
  5분마다 "extracting 상태인데 CLAIM_TIMEOUT_SECONDS(기본 300초) 이상 지난 행"을 찾아
  attempt_count 를 늘리고 discovered 로 되돌린다(그러면 정상 워커가 다시 집어가
  처리한다). attempt_count 가 MAX_ATTEMPTS 에 도달하면 dead 로 처리해, 구조적으로
  항상 타임아웃나는 URL(예: 특정 페이지에서 headless 가 매번 멈추는 경우)이
  무한 재시도되는 것을 막는다.

실행 방식:
  __main__.py 에서 extraction 워커를 시작할 때 daemon 스레드로 함께 띄운다.
  daemon=True 이므로 메인 스레드(추출 워커)가 종료되면 Reaper 도 자동 종료된다.
"""

from __future__ import annotations

import logging
import time

from app import config
from app.repository.crawl_url_repo import CrawlUrlRepo
from app.repository.db import db_context

logger = logging.getLogger(__name__)

_INTERVAL_SEC = 300   # 5분마다 실행


def run_reaper(worker_id: str) -> None:
    """
    데몬 스레드로 실행. 주기적으로 타임아웃 초과 extracting 행을 회수한다.
    종료는 메인 스레드 종료 시 자동으로 이루어짐 (daemon=True).
    """
    logger.info(
        f"reaper started interval={_INTERVAL_SEC}s "
        f"timeout={config.CLAIM_TIMEOUT_SECONDS}s",
        extra={"phase": "startup", "worker_id": worker_id, "component": "reaper"},
    )

    with db_context() as engine:
        url_repo = CrawlUrlRepo(engine)

        while True:
            time.sleep(_INTERVAL_SEC)
            try:
                recovered = url_repo.recover_timed_out(
                    timeout_seconds=config.CLAIM_TIMEOUT_SECONDS
                )
                if recovered > 0:
                    logger.warning(
                        f"recovered {recovered} timed-out extracting rows",
                        extra={"phase": "reap", "worker_id": worker_id, "component": "reaper"},
                    )
                else:
                    logger.debug(
                        "reaper: no timed-out rows",
                        extra={"phase": "reap", "worker_id": worker_id, "component": "reaper"},
                    )
            except Exception:
                logger.exception(
                    "reaper error",
                    extra={"phase": "reap", "worker_id": worker_id, "component": "reaper"},
                )
