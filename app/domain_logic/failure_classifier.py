"""
HTTP 오류 코드와 네트워크 예외를 두 가지로 분류한다:
  - 영구 오류 (is_permanent=True):  재시도해도 소용없다. 404, 403 같은 케이스.
  - 일시 오류 (is_permanent=False): 나중에 다시 시도하면 될 수 있다. 429, 5xx, 타임아웃.
"""

from __future__ import annotations

from app.types import ErrorCode


def classify_http(status_code: int) -> tuple[ErrorCode, bool]:
    """HTTP 상태코드 → (ErrorCode, is_permanent) 반환."""
    if status_code in (404, 410):
        return ErrorCode.FETCH_404, True       # 없는 페이지 — 영구
    if status_code in (400, 401, 403):
        return ErrorCode.FETCH_403, True       # 권한 없음 / 잘못된 요청 — 영구
    if status_code == 429:
        return ErrorCode.FETCH_429, False      # 요청 너무 많음 — 잠시 후 재시도
    if status_code >= 500:
        return ErrorCode.FETCH_5XX, False      # 서버 장애 — 잠시 후 재시도
    return ErrorCode.UNKNOWN, False


def classify_exception(exc: BaseException) -> tuple[ErrorCode, bool]:
    """네트워크 예외 → (ErrorCode, is_permanent) 반환. 예외 이름으로 판별한다."""
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return ErrorCode.FETCH_TIMEOUT, False    # 응답 없음 — 재시도 가능
    if "connect" in name:
        return ErrorCode.FETCH_CONNECTION, False  # 연결 실패 — 재시도 가능
    return ErrorCode.UNKNOWN, False
