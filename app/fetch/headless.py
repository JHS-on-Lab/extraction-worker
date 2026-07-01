"""
Playwright 기반 headless 브라우저 Fetcher.

render_mode 에 따라 동작이 다르다:
  headless             — 기본. page.content() 반환.
  headless_with_iframe — 로드된 iframe 내용을 외부 HTML 에 주입해 반환.
                         iframe 안의 콘텐츠를 domain rules 로 추출해야 할 때 사용.
                         (예: finance.naver.com 종목토론)

사용하려면:
  pip install playwright
  playwright install chromium
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.types import FetchResult, RenderMode

if TYPE_CHECKING:
    from app.fetch.http_client import HttpFetcher


class HeadlessFetcher:
    """Playwright Chromium 으로 페이지를 렌더링해 HTML 을 반환한다."""

    def __init__(self, timeout_ms: int = 15000) -> None:
        self._timeout_ms = timeout_ms
        self._playwright = None
        self._browser    = None

    def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

    def fetch(
        self,
        url: str,
        *,
        render: RenderMode = RenderMode.HEADLESS,
        wait_for_selector: str | None = None,
    ) -> FetchResult:
        self._ensure_browser()

        with_iframe = (render == RenderMode.HEADLESS_IFRAME)
        # networkidle 은 광고/추적 스크립트가 많은 페이지에서 타임아웃 위험.
        # load 이벤트는 메인 문서와 iframe 리소스가 모두 로드된 시점을 보장.
        wait_until  = "load" if with_iframe else "domcontentloaded"

        start = time.monotonic()
        page  = self._browser.new_page()
        try:
            response = page.goto(url, timeout=self._timeout_ms, wait_until=wait_until)

            # Next.js 등 CSR 사이트는 domcontentloaded 이후에도 React 하이드레이션이 진행된다.
            # headless_wait_for 셀렉터가 지정된 경우 해당 요소가 나타날 때까지 대기한다.
            if wait_for_selector:
                try:
                    page.wait_for_selector(wait_for_selector, timeout=self._timeout_ms)
                except Exception:
                    pass  # 타임아웃 시 그대로 진행

            html     = page.content()
            status   = response.status if response else 200

            if with_iframe:
                _wait_for_frames(page, timeout_ms=self._timeout_ms)
                html = _inject_frames(page, html)
        finally:
            page.close()

        elapsed_ms = (time.monotonic() - start) * 1000
        return FetchResult(
            url=url,
            html=html,
            status_code=status,
            render_mode=render,
            elapsed_ms=elapsed_ms,
        )

    def close(self) -> None:
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self) -> "HeadlessFetcher":
        return self

    def __exit__(self, *_) -> None:
        self.close()


def fetch_by_render_mode(
    url: str,
    render_mode: str,
    http_fetcher: "HttpFetcher",
    headless_fetcher: "HeadlessFetcher",
    wait_for_selector: str | None = None,
) -> FetchResult:
    """render_mode 문자열에 따라 적절한 fetcher 를 선택해 FetchResult 를 반환한다."""
    if render_mode == RenderMode.HEADLESS_IFRAME:
        return headless_fetcher.fetch(url, render=RenderMode.HEADLESS_IFRAME)
    if render_mode == RenderMode.HEADLESS:
        return headless_fetcher.fetch(url, render=RenderMode.HEADLESS,
                                      wait_for_selector=wait_for_selector)
    return http_fetcher.fetch(url)


def _wait_for_frames(page, timeout_ms: int) -> None:
    """http URL 을 가진 프레임이 하나라도 로드될 때까지 대기한다.
    load 이벤트 후에도 iframe src 가 JS 로 채워지는 경우를 처리한다."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if any(f.url.startswith("http") for f in page.frames[1:]):
            return
        time.sleep(0.2)


def _inject_frames(page, outer_html: str) -> str:
    """
    로드된 iframe 의 HTML 을 외부 HTML 에 주입한다.
    각 iframe 은 <div id="frame_{name}"> 으로 감싸져 </body> 앞에 삽입된다.
    domain rules 의 CSS 셀렉터로 접근 가능해진다.
    """
    injections: list[str] = []
    for frame in page.frames[1:]:           # index 0 은 메인 프레임
        if not frame.url or not frame.url.startswith("http"):
            continue
        try:
            frame_html = frame.content()
            frame_id   = f"frame_{frame.name}" if frame.name else f"frame_{len(injections)}"
            injections.append(f'<div id="{frame_id}">{frame_html}</div>')
        except Exception:
            pass

    if injections:
        outer_html = outer_html.replace("</body>", "\n".join(injections) + "\n</body>")
    return outer_html
