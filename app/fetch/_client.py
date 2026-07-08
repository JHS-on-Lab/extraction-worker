"""
공통 HTTP 클라이언트 유틸리티.

어댑터·Fetcher 모두 이 모듈에서 클라이언트를 생성한다.
"""

from __future__ import annotations

import ssl

import certifi
import httpx

from app import config

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_BASE_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_legacy_renegotiation_ctx: ssl.SSLContext | None = None


def _legacy_renegotiation_context() -> ssl.SSLContext:
    """구형 TLS 재협상(legacy renegotiation)을 요구하는 서버용 SSL 컨텍스트.

    OpenSSL 3.x는 이를 기본 거부(UNSAFE_LEGACY_RENEGOTIATION_DISABLED)하므로
    OP_LEGACY_SERVER_CONNECT로 명시 허용한다. (예: baotintuc.vn — ASP.NET/구형 IIS)
    """
    global _legacy_renegotiation_ctx
    if _legacy_renegotiation_ctx is None:
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        if not config.HTTP_VERIFY_SSL:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        _legacy_renegotiation_ctx = ctx
    return _legacy_renegotiation_ctx


def make_client(
    *,
    referer: str | None = None,
    timeout: float = 15.0,
    follow_redirects: bool = True,
    extra_headers: dict[str, str] | None = None,
    allow_legacy_renegotiation: bool = False,
) -> httpx.Client:
    headers = dict(_BASE_HEADERS)
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    verify = _legacy_renegotiation_context() if allow_legacy_renegotiation else config.HTTP_VERIFY_SSL
    return httpx.Client(
        headers=headers,
        follow_redirects=follow_redirects,
        timeout=timeout,
        verify=verify,
    )
