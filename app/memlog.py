"""
메모리 사용량 스냅샷 로깅.

worker/extraction_worker.py 의 heartbeat 블록(HEARTBEAT_INTERVAL_SECONDS 주기)에서
호출한다. 정적(httpx)으로 처리하는 동안은 자식 프로세스가 없어 rss_children_mb 가
0 근처지만, render_mode=headless 인 URL 을 처리할 때 Playwright 가 띄운 Chromium
자식 프로세스가 있으면 그만큼 잡힌다 — render_mode 구분 없이 항상 로깅해서
"headless 처리 비중이 높을 때 실제로 rss_children_mb 가 같이 늘어나는지"를
대조군 없이도 시계열로 확인할 수 있게 한다.

출력은 logging_setup.py 가 구성한 "memlog" 로거(→ {log_name}-mem.log)로 간다.
"""

from __future__ import annotations

import logging

import psutil

_mem_logger = logging.getLogger("memlog")
_self = psutil.Process()

_MB = 1024 * 1024


def log_memory_usage(worker_id: str) -> None:
    """현재 프로세스(self) + 자식 프로세스(Chromium 등) 전체의 RSS 를 한 줄 로깅한다."""
    try:
        rss_self = _self.memory_info().rss
        children = _self.children(recursive=True)
    except psutil.NoSuchProcess:
        return

    rss_children = 0
    for child in children:
        try:
            rss_children += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    _mem_logger.info(
        f"worker={worker_id} rss_self_mb={rss_self / _MB:.1f} "
        f"rss_children_mb={rss_children / _MB:.1f} children={len(children)}"
    )
