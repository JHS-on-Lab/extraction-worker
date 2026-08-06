"""
extraction-worker 진입점.

실행 예:
  python -m app
  python -m app --source NAVER_NEWS              # 특정 소스만 처리
  python -m app --source NAVER_NEWS,DAUM_NEWS    # 복수 소스만 처리
  python -m app --worker-id extr-1
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from app import logging_setup
from app import config


def _parse_source(value: str) -> str:
    """--source 값을 정규화한다.

    discovery-worker 의 어댑터(=source_type)는 이 저장소와 무관하게 계속 추가된다
    (예: SOLR_RESCRAPE 는 rescrape-dispatcher 가 붙이는 source_type). 여기서 값
    목록을 화이트리스트로 고정해두면 소스가 하나 늘 때마다 이 저장소도 따로
    수정해야 하는 불필요한 결합이 생긴다 — 그래서 형식(공백/빈 값 여부)만 검사하고,
    실제 값이 유효한 source_type 인지는 DB 조회(WHERE source_type IN (...))가
    0건으로 자연히 걸러내도록 맡긴다. 오탈자를 실행 즉시 막고 싶다면
    deploy/run.sh 의 형식 검증(정규식)을 통과시킨 뒤 실행해야 한다.
    """
    if value.upper() == "ALL":
        return "all"
    sources = [s.strip().upper() for s in value.split(",") if s.strip()]
    if not sources:
        raise argparse.ArgumentTypeError("empty --source value")
    return ",".join(sources)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="extraction-worker")
    p.add_argument("--source",    default="all", type=_parse_source,
                    help="처리할 소스 필터, 콤마로 복수 지정 가능 (예: NAVER_NEWS,DAUM_NEWS) (기본: all)")
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
