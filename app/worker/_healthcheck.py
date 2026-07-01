"""워커 헬스체크 파일 갱신 유틸리티.

각 워커는 heartbeat 주기마다 이 함수를 호출한다.
Docker HEALTHCHECK 가 파일 갱신 시각을 확인해 컨테이너 상태를 판단한다.

  HEALTHCHECK CMD: time.time() - mtime(/tmp/healthcheck) < 120
"""

from __future__ import annotations

import pathlib
import time

_PATH = pathlib.Path("/tmp/healthcheck")


def write() -> None:
    """현재 monotonic 타임스탬프를 /tmp/healthcheck 에 기록한다."""
    try:
        _PATH.write_text(str(time.monotonic()))
    except OSError:
        pass
