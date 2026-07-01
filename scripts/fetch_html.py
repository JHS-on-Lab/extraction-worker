"""
URL 의 HTML 을 가져와서 출력하는 진단 스크립트.
iframe src 탐지 및 iframe 내부 HTML 도 함께 출력한다.

실행:
  python scripts/fetch_html.py --url "https://finance.naver.com/item/board_read.naver?code=000660&nid=421731371"
  python scripts/fetch_html.py --url "..." --headless
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from selectolax.parser import HTMLParser


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="URL HTML 진단")
    p.add_argument("--url",      required=True, help="가져올 URL")
    p.add_argument("--headless", action="store_true", help="headless 브라우저 사용")
    p.add_argument("--save",     default=None, help="HTML 저장 파일 경로 (예: /tmp/out.html)")
    return p.parse_args()


def _fetch_static(url: str) -> str:
    from app.fetch._client import make_client
    with make_client(referer="https://finance.naver.com/") as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def _fetch_headless(url: str) -> str:
    from app.fetch.headless import HeadlessFetcher
    fetcher = HeadlessFetcher()
    try:
        result = fetcher.fetch(url)
        return result.html
    finally:
        fetcher.close()


def _print_iframes(html: str, base_url: str) -> None:
    tree = HTMLParser(html)
    iframes = tree.css("iframe")
    if not iframes:
        print("  (iframe 없음)")
        return
    for i, node in enumerate(iframes):
        src = node.attributes.get("src", "")
        print(f"  iframe[{i}] src={src}")


def _print_candidates(html: str) -> None:
    """제목/본문/날짜/작성자 후보 요소를 출력한다."""
    tree = HTMLParser(html)

    print("\n--- 텍스트 50자 이상인 요소 (본문 후보) ---")
    for tag in ("p", "div", "td", "span", "article", "section"):
        for node in tree.css(tag):
            text = (node.text(deep=True) or "").strip()
            if len(text) >= 50:
                cls = node.attributes.get("class", "")
                id_ = node.attributes.get("id", "")
                print(f"  <{tag} class='{cls}' id='{id_}'>  {text[:120]!r}")

    print("\n--- 날짜 패턴 포함 요소 ---")
    import re
    date_pat = re.compile(r"\d{4}[.\-/]\d{2}[.\-/]\d{2}|\d{2}:\d{2}")
    for tag in ("td", "span", "div", "p", "em", "small"):
        for node in tree.css(tag):
            text = (node.text(deep=True) or "").strip()
            if date_pat.search(text) and len(text) < 40:
                cls = node.attributes.get("class", "")
                print(f"  <{tag} class='{cls}'>  {text!r}")


def main() -> None:
    args = _parse_args()
    url = args.url

    print(f"URL: {url}")
    print(f"mode: {'headless' if args.headless else 'static'}\n")

    html = _fetch_headless(url) if args.headless else _fetch_static(url)

    if args.save:
        Path(args.save).write_text(html, encoding="utf-8")
        print(f"HTML 저장됨: {args.save}  ({len(html):,} bytes)\n")

    print(f"HTML 크기: {len(html):,} bytes\n")

    print("=== iframe 탐지 ===")
    _print_iframes(html, url)

    print("\n=== 본문·날짜 후보 요소 ===")
    _print_candidates(html)


if __name__ == "__main__":
    main()
