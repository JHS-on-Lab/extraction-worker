"""
Playwright 기반 headless 브라우저 Fetcher.

render_mode 에 따라 동작이 다르다:
  headless             — 기본. page.content() 반환.
  headless_with_iframe — 로드된 iframe 내용을 외부 HTML 에 주입해 반환.
                         iframe 안의 콘텐츠를 domain rules 로 추출해야 할 때 사용.
                         (예: finance.naver.com 종목토론)
  headless_with_shadow — open shadow root 내용을 외부 HTML 에 주입해 반환.
                         page.content() 는 shadow DOM 을 포함하지 않으므로
                         (selectolax 도 라이브 페이지가 아닌 문자열만 다뤄 마찬가지),
                         커스텀 엘리먼트 안에 본문이 있는 사이트에 사용.
                         (예: msn.com cp-article)

사용하려면:
  pip install playwright
  playwright install chromium
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.types import FetchResult, RenderMode

if TYPE_CHECKING:
    from app.fetch.http_client import HttpFetcher

_log = logging.getLogger(__name__)


class HeadlessFetcher:
    """Playwright Chromium 으로 페이지를 렌더링해 HTML 을 반환한다."""

    def __init__(self, timeout_ms: int = 15000) -> None:
        self._timeout_ms = timeout_ms
        self._playwright = None
        self._browser    = None

    def _ensure_browser(self) -> None:
        # is_connected() 로 살아있는지 매번 확인한다 — 기존엔 self._browser is not None
        # 만 보고 재사용해서, 브라우저 프로세스가 크래시(OOM 등)해도 죽은 참조를 계속
        # 들고 있었다. 그 결과 이후 모든 headless 요청이 죽은 연결로 실패하고,
        # 재시작 전까지 render_mode=headless(_iframe/_shadow) 인 모든 도메인이 계속
        # 실패했다.
        if self._browser is not None and self._browser.is_connected():
            return
        if self._browser is not None:
            _log.warning("headless 브라우저 연결이 끊김 — 재시작한다")
        self._teardown()

        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

    def _teardown(self) -> None:
        """죽었거나 재시작 전인 브라우저/playwright 참조를 정리한다."""
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def fetch(
        self,
        url: str,
        *,
        render: RenderMode = RenderMode.HEADLESS,
        wait_for_selector: str | None = None,
    ) -> FetchResult:
        self._ensure_browser()

        with_iframe = (render == RenderMode.HEADLESS_IFRAME)
        with_shadow = (render == RenderMode.HEADLESS_SHADOW)
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
            elif with_shadow:
                html = _inject_shadow_roots(page, html)
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
        self._teardown()

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
    allow_legacy_renegotiation: bool = False,
) -> FetchResult:
    """render_mode 문자열에 따라 적절한 fetcher 를 선택해 FetchResult 를 반환한다."""
    if render_mode == RenderMode.HEADLESS_IFRAME:
        return headless_fetcher.fetch(url, render=RenderMode.HEADLESS_IFRAME,
                                      wait_for_selector=wait_for_selector)
    if render_mode == RenderMode.HEADLESS_SHADOW:
        return headless_fetcher.fetch(url, render=RenderMode.HEADLESS_SHADOW,
                                      wait_for_selector=wait_for_selector)
    if render_mode == RenderMode.HEADLESS:
        return headless_fetcher.fetch(url, render=RenderMode.HEADLESS,
                                      wait_for_selector=wait_for_selector)
    return http_fetcher.fetch(url, allow_legacy_renegotiation=allow_legacy_renegotiation)


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


def _inject_shadow_roots(page, outer_html: str) -> str:
    """
    open shadow root 를 가진 커스텀 엘리먼트의 내용을 외부 HTML 에 주입한다.
    한 페이지에 광고·내비게이션 등 shadow root 를 쓰는 위젯이 여러 개 섞여
    있을 수 있어(예: msn.com 은 14개), 순회 순서(index)만으로는 domain rules
    셀렉터가 불안정하다. 각 wrapper 에 원본 태그명을 data-shadow-host 로
    남겨 `div[data-shadow-host="cp-article"]` 처럼 안정적으로 선택하게 한다.
    <div id="shadow_{n}" data-shadow-host="{tagName}"> 형태로 </body> 앞에 삽입된다.

    page.content() 는 shadow DOM 을 포함하지 않으므로, JS 로 직접 순회해
    shadowRoot.innerHTML 을 모아온다. closed 모드 shadow root 는 스크립트로도
    접근 불가하므로 대상에서 제외된다(사이트가 closed 를 쓰면 이 방법 자체가 무력화됨).
    """
    try:
        shadow_hosts: list[dict] = page.evaluate("""() => {
            const results = [];
            const walk = (root) => {
                for (const el of root.querySelectorAll('*')) {
                    if (el.shadowRoot) {
                        results.push({tag: el.tagName.toLowerCase(), html: el.shadowRoot.innerHTML});
                        walk(el.shadowRoot);  // 중첩 shadow root 대응
                    }
                }
            };
            walk(document);
            return results;
        }""")
    except Exception:
        shadow_hosts = []

    if not shadow_hosts:
        return outer_html

    injections = [
        f'<div id="shadow_{i}" data-shadow-host="{h["tag"]}">{h["html"]}</div>'
        for i, h in enumerate(shadow_hosts)
    ]
    return outer_html.replace("</body>", "\n".join(injections) + "\n</body>")
