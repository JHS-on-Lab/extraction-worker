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
    """요청마다 httpx.Client 를 새로 만들지 않고 재사용한다.

    이전엔 fetch() 호출마다 `with make_client(...) as client:` 로 매번 새
    Client(+커넥션)를 만들고 버렸다 — 매 요청이 TCP+TLS 핸드셰이크를 새로
    해야 해서 비효율적이었다(연결 재사용/커넥션 풀링 이점이 없었음).

    allow_legacy_renegotiation 은 도메인별로 달라질 수 있고 httpx.Client 는
    생성 시점의 verify(SSLContext)를 나중에 못 바꾸므로, 일반 클라이언트와
    legacy 클라이언트를 각각 하나씩만 지연 생성해 캐싱한다.
    """

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout
        self._client: httpx.Client | None = None
        self._legacy_client: httpx.Client | None = None

    def _get_client(self, allow_legacy_renegotiation: bool) -> httpx.Client:
        if allow_legacy_renegotiation:
            if self._legacy_client is None:
                self._legacy_client = make_client(
                    timeout=self._timeout, allow_legacy_renegotiation=True,
                )
            return self._legacy_client
        if self._client is None:
            self._client = make_client(timeout=self._timeout, allow_legacy_renegotiation=False)
        return self._client

    def fetch(self, url: str, *, allow_legacy_renegotiation: bool = False) -> FetchResult:
        """
        URL 을 GET 으로 가져와 FetchResult 반환.
        - 리다이렉트 자동 추적 (구글 RSS 중간 URL 처리)
        - HTTP 오류(4xx/5xx)는 예외 없이 FetchResult(status_code=N, html="") 반환
        - 네트워크 오류(timeout, connect)는 재raise — 호출자가 classify_exception 으로 처리
        - allow_legacy_renegotiation: 구형 TLS 재협상 서버(예: baotintuc.vn) 대응
        """
        start = time.monotonic()
        client = self._get_client(allow_legacy_renegotiation)
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

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._legacy_client is not None:
            self._legacy_client.close()
            self._legacy_client = None

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, *_) -> None:
        self.close()
