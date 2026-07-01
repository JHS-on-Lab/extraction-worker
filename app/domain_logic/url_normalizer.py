"""URL 정규화 + url_hash 생성 — 설계 문서 6절."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse

# utm_* / fbclid 등 추적 파라미터 제거 화이트리스트
_STRIP_PARAMS = re.compile(
    r"^(utm_source|utm_medium|utm_campaign|utm_term|utm_content"
    r"|fbclid|gclid|msclkid|ref|source)$",
    re.IGNORECASE,
)


def normalize(url: str) -> str:
    """
    URL 정규화:
    - 스킴: http → https
    - 호스트 소문자, www. 제거 (정책: 유지)
    - 추적 파라미터 제거
    - 끝 슬래시·기본 포트·프래그먼트 제거
    """
    parsed = urlparse(url.strip())

    scheme = "https"
    netloc = parsed.netloc.lower().rstrip(":")
    # 기본 포트 제거
    if netloc.endswith(":443") or netloc.endswith(":80"):
        netloc = netloc.rsplit(":", 1)[0]

    path = parsed.path.rstrip("/") or "/"

    # 추적 파라미터 제거
    qs = [(k, v) for k, v in parse_qsl(parsed.query) if not _STRIP_PARAMS.match(k)]
    query = urlencode(sorted(qs))

    return urlunparse((scheme, netloc, path, "", query, ""))


def url_hash(normalized_url: str) -> str:
    """sha256 hex (64자). t_crawl_url 중복 방지 키로 사용."""
    return hashlib.sha256(normalized_url.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Solr 문서 ID 생성 — Java HashCreator.lookup3ycs64 포트
# ---------------------------------------------------------------------------

def _u32(x: int) -> int:
    return x & 0xFFFFFFFF


def _lookup3ycs64(s: str, start: int, end: int, initval: int) -> int:
    if initval < 0:
        initval += (1 << 64)
    initval &= 0xFFFFFFFFFFFFFFFF

    a = b = c = _u32(0xdeadbeef + (initval & 0xFFFFFFFF))
    c = _u32(c + (initval >> 32))

    i = start
    mixed = True

    while True:
        if i >= end:
            break
        mixed = False

        ch = ord(s[i]); i += 1
        if 0xD800 <= ch <= 0xDBFF and i < end:
            low = ord(s[i])
            if 0xDC00 <= low <= 0xDFFF:
                ch = ((ch - 0xD800) << 10) + (low - 0xDC00) + 0x10000
                i += 1
        a = _u32(a + ch)

        if i >= end:
            break

        ch = ord(s[i]); i += 1
        if 0xD800 <= ch <= 0xDBFF and i < end:
            low = ord(s[i])
            if 0xDC00 <= low <= 0xDFFF:
                ch = ((ch - 0xD800) << 10) + (low - 0xDC00) + 0x10000
                i += 1
        b = _u32(b + ch)

        if i >= end:
            break

        ch = ord(s[i]); i += 1
        if 0xD800 <= ch <= 0xDBFF and i < end:
            low = ord(s[i])
            if 0xDC00 <= low <= 0xDFFF:
                ch = ((ch - 0xD800) << 10) + (low - 0xDC00) + 0x10000
                i += 1
        c = _u32(c + ch)

        if i >= end:
            break

        a = _u32(a - c); a = _u32(a ^ _u32((c << 4)  | (c >> 28))); c = _u32(c + b)
        b = _u32(b - a); b = _u32(b ^ _u32((a << 6)  | (a >> 26))); a = _u32(a + c)
        c = _u32(c - b); c = _u32(c ^ _u32((b << 8)  | (b >> 24))); b = _u32(b + a)
        a = _u32(a - c); a = _u32(a ^ _u32((c << 16) | (c >> 16))); c = _u32(c + b)
        b = _u32(b - a); b = _u32(b ^ _u32((a << 19) | (a >> 13))); a = _u32(a + c)
        c = _u32(c - b); c = _u32(c ^ _u32((b << 4)  | (b >> 28))); b = _u32(b + a)
        mixed = True

    if not mixed:
        c = _u32(c ^ b); c = _u32(c - _u32((b << 14) | (b >> 18)))
        a = _u32(a ^ c); a = _u32(a - _u32((c << 11) | (c >> 21)))
        b = _u32(b ^ a); b = _u32(b - _u32((a << 25) | (a >> 7)))
        c = _u32(c ^ b); c = _u32(c - _u32((b << 16) | (b >> 16)))
        a = _u32(a ^ c); a = _u32(a - _u32((c << 4)  | (c >> 28)))
        b = _u32(b ^ a); b = _u32(b - _u32((a << 14) | (a >> 18)))
        c = _u32(c ^ b); c = _u32(c - _u32((b << 24) | (b >> 8)))

    result = (b << 32) | c
    if result >= (1 << 63):
        result -= (1 << 64)
    return result


_JSESSIONID = re.compile(r";jsessionid=.*?(?=\?)")
_HEX = "0123456789abcdef"


def crawl_id(url: str) -> str:
    """Solr 문서 id 생성 — lookup3ycs64 기반 16자 hex."""
    url = _JSESSIONID.sub("", url)
    hash_val = _lookup3ycs64(url, 0, len(url), 0)
    if hash_val < 0:
        hash_val += (1 << 64)
    return "".join(
        _HEX[(hash_val >> (56 - 8 * i)) >> 4 & 0xF] + _HEX[(hash_val >> (56 - 8 * i)) & 0xF]
        for i in range(8)
    )
