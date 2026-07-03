"""
extraction-worker 진입점.

실행 예:
  python -m app
  python -m app --source NAVER_NEWS   # 특정 소스만 처리
  python -m app --worker-id extr-1
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from app import logging_setup
from app import config

_SOURCES = ("NAVER_NEWS", "DAUM_NEWS", "GOOGLE_NEWS", "NAVER_STOCK", "DUCKDUCKGO_NEWS", "all")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="extraction-worker")
    p.add_argument("--source",    default="all", choices=_SOURCES, help="처리할 소스 필터 (기본: all)")
    p.add_argument("--worker-id", default=None,  help="워커 식별자 (기본: 환경변수 WORKER_ID)")
    return p.parse_args()


def _handle_signal(signum: int, frame: object) -> None:
    logger = logging_setup.setup("main")
    logger.info("shutdown", extra={"phase": "shutdown", "worker_id": config.WORKER_ID})
    sys.exit(0)


def main() -> None:
    args = _parse_args()
    config.validate()

    worker_id = args.worker_id or config.WORKER_ID
    config.WORKER_ID = worker_id

    logger = logging_setup.setup("extraction", worker_id=worker_id,
                                 log_name=f"extraction-{worker_id}")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    try:
        if config.MASKING_ENABLED:
            from app.sink.serialize import init_masker
            init_masker()

        from app.worker.extraction_worker import run_extraction_loop
        from app.worker.reaper import run_reaper

        reaper = threading.Thread(
            target=run_reaper,
            args=(worker_id,),
            daemon=True,
            name="reaper",
        )
        reaper.start()
        run_extraction_loop(source=args.source, worker_id=worker_id)
    except Exception:
        logger.exception(
            "unhandled exception — worker stopping",
            extra={"phase": "main", "worker_id": worker_id},
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
