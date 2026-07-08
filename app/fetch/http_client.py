"""
정적 HTTP Fetcher.

FetchResult 를 반환하며 예외를 던지지 않는다.
네트워크 오류는 status_code=-1 로 표현해 호출자가 failure_classifier 로 분류한다.
"""

from __future__ import annotations

import re
import time

import httpx

from app.fetch._client import make_client
from app.types import FetchResult, RenderMode


def _decode_response(resp: httpx.Response) -> str:
    """Content-Type 또는 HTML <meta charset> 에서 인코딩을 감지해 디코딩한다.
    EUC-KR/CP949 등 비UTF-8 사이트가 charset 헤더를 누락할 때 대응."""
    # httpx 가 이미 charset 을 헤더에서 확인한 경우
    if resp.charset_encoding:
        return resp.text

    raw = resp.content
    # HTML 앞부분 <meta charset> / <meta http-equiv="Content-Type"> 에서 charset 추출
    snippet = raw[:2048].decode("ascii", errors="replace")
    m = re.search(r'charset=["\']?([\w\-]+)', snippet, re.IGNORECASE)
    if m:
        charset = m.group(1)
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass

    return raw.decode("utf-8", errors="replace")


class HttpFetcher:
    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    def fetch(self, url: str, *, allow_legacy_renegotiation: bool = False) -> FetchResult:
        """
        URL 을 GET 으로 가져와 FetchResult 반환.
        - 리다이렉트 자동 추적 (구글 RSS 중간 URL 처리)
        - HTTP 오류(4xx/5xx)는 예외 없이 FetchResult(status_code=N, html="") 반환
        - 네트워크 오류(timeout, connect)는 재raise — 호출자가 classify_exception 으로 처리
        - allow_legacy_renegotiation: 구형 TLS 재협상 서버(예: baotintuc.vn) 대응
        """
        start = time.monotonic()
        with make_client(
            timeout=self._timeout,
            allow_legacy_renegotiation=allow_legacy_renegotiation,
        ) as client:
            resp = client.get(url)

        elapsed_ms = (time.monotonic() - start) * 1000
        html = "" if resp.status_code >= 400 else _decode_response(resp)

        return FetchResult(
            url=str(resp.url),          # 리다이렉트 최종 URL
            html=html,
            status_code=resp.status_code,
            render_mode=RenderMode.STATIC,
            elapsed_ms=elapsed_ms,
        )
