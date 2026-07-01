"""
공통 HTTP 클라이언트 유틸리티.

어댑터·Fetcher 모두 이 모듈에서 클라이언트를 생성한다.
"""

from __future__ import annotations

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


def make_client(
    *,
    referer: str | None = None,
    timeout: float = 15.0,
    follow_redirects: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Client:
    headers = dict(_BASE_HEADERS)
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    return httpx.Client(
        headers=headers,
        follow_redirects=follow_redirects,
        timeout=timeout,
        verify=config.HTTP_VERIFY_SSL,
    )
