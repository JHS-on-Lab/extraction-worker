"""
도메인 전용 추출 규칙 엔진.

domain.rules_json 에 저장된 CSS/XPath 셀렉터로 제목·본문·저자·언론사·날짜를 추출한다.
규칙이 있으면 trafilatura/readability 보다 먼저 시도되고,
규칙이 없거나 실패하면 LibraryChain 으로 폴백한다.

rules_json 형식:
  {
    "title":        {"css": "h1.article-title"},
    "body":         {"css": "div.article-body p"},
    "author":       {"css": "span.byline"},
    "published_at": {"css": "span.date", "date_format": "%Y.%m.%d %H:%M"},
    "min_body_len": 10
  }

지원 셀렉터 타입:
  "css"      — selectolax 로 처리. 여러 노드가 매칭되면 텍스트를 이어 붙인다.
  "xpath"    — lxml 로 처리. 속성값(//@attr)과 텍스트(//tag) 모두 지원.
  "json_api" — JSON API 를 직접 호출해 필드를 추출한다.
               URL 파라미터에서 값을 뽑아 API URL 을 구성한 뒤 GET 호출.
               응답 JSON 의 점(.) 경로로 필드를 지정한다.
  "amp_url"  — 원본 URL 의 경로를 변환해 AMP 페이지를 정적 fetch 한 뒤
               일반 CSS/XPath 규칙으로 추출한다.
               순수 CSR 사이트에 AMP 버전이 있을 때 headless 대신 사용.
  "next_data" — 정적 HTML 의 <script id="__NEXT_DATA__"> 에 임베드된 JSON 에서 추출한다.
               Next.js Pages Router 사이트에서 headless 없이 사용 가능.

amp_url 규칙 형식 (최상위에 "amp_url" 키를 두면 이 모드로 동작):
  {
    "amp_url":  {"pattern": "/article/", "replacement": "/amp/article/"},
    "title":    {"css": "h2.titleline_title_end"},
    "body":     {"css": "div.acem_text"},
    "published_at": {"css": "span.aeti_num", "date_format": "%Y.%m.%d %H:%M"},
    "min_body_len": 100
  }

next_data 규칙 형식 (최상위에 "next_data" 키를 두면 이 모드로 동작):
  {
    "next_data": {
      "root":             "props.pageProps.articleView",  // __NEXT_DATA__ 내 콘텐츠 객체 경로
      "title":            "title",
      "author":           "author",
      "published_at":     "published_time",               // ISO 8601 자동 파싱
      "body_array":       "contentArrange",               // 배열 필드
      "body_type_key":    "type",                         // 배열 항목의 타입 키
      "body_type_value":  "text",                         // 본문으로 사용할 타입 값
      "body_content_key": "content"                       // 실제 텍스트가 담긴 키
    },
    "min_body_len": 100
  }

json_api 규칙 형식 (최상위에 "json_api" 키를 두면 이 모드로 동작):
  {
    "json_api": {
      "url_template": "https://api.example.com/article?id={nid}",
      "url_param": "nid",          // 원래 URL 에서 추출할 쿼리 파라미터명
      "title":        "result.title",
      "body_html":    "result.contentHtml",  // HTML 이면 body_html + body_css
      "body_css":     ".se-module-text",     // body_html 을 파싱할 CSS 셀렉터
      "body_json_fallback": "result.contentJsonSwReplaced",  // body_html 이 없을 때만 사용.
                             // 이 경로의 값을 문자열로 보고 한 번 더 JSON 파싱해
                             // {"title": ..., "content": "<p>...</p>"} 형태에서 꺼낸다.
                             // title 도 이 값으로 덮어씀(예: 네이버 종목토론 리서치
                             // 게시글은 result.title 이 "새로운 리서치가 있어요" 같은
                             // 공통 안내문이고, 실제 제목/본문은 여기 있음).
      "published_at": "result.writtenAt",    // ISO 8601 자동 파싱
      "author":       "result.writer.nickname",
    },
    "min_body_len": 5
  }

published_at 규칙:
  "date_format" — strptime 포맷 문자열. 미지정 시 날짜 파싱을 시도하지 않는다.
  json_api 모드에서 published_at 가 ISO 8601 이면 date_format 없이도 자동 파싱.
  파싱 실패 시 None 으로 폴백한다 (추출 전체를 실패시키지 않는다).
  date_format 에 %z 등 오프셋이 없으면 원본 문자열이 이미 KST 현지시각이라고
  가정해 KST 로 라벨링한다. %z 로 오프셋이 파싱되면(예: 베트남 UTC+7) 그 값을
  기준으로 KST 로 변환한다 (예: baotintuc.vn).

도메인 규칙은 TTL 캐시에 보관한다 (RULES_CACHE_TTL_SECONDS, 기본 60초).
재배포 없이 DB 에서 rules_json 을 수정하면 캐시 만료 후 자동 반영된다.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs

from selectolax.parser import HTMLParser

from app import config
from app.extraction._common import build_content, check_body_length, check_title
from app.types import CollectedContent, ErrorCode, ExtractionFailure

_KST = timezone(timedelta(hours=9))

# 전략마다 min_body_len 기본값이 다른 이유:
#   HTML 규칙  — 임의 CSS/XPath 라 셀렉터가 엉뚱한 짧은 영역을 잡을 위험이 커서 높게(200)
#   next_data  — __NEXT_DATA__ JSON 은 이미 구조화돼 있어 중간 정도(100)
#   json_api   — API 응답 필드는 대상이 명확해(예: 종목토론 짧은 글) 낮게(5)
_DEFAULT_MIN_BODY_LEN_HTML      = 200
_DEFAULT_MIN_BODY_LEN_NEXT_DATA = 100
_DEFAULT_MIN_BODY_LEN_JSON_API  = 5


class RuleEngine:
    """도메인별 CSS/XPath 규칙으로 본문을 추출한다."""

    def __init__(self) -> None:
        # host → (rules_dict, cached_at) 형태로 메모리 캐시
        self._cache: dict[str, tuple[dict, float]] = {}

    def get_rules(self, host: str, domain_row: dict | None) -> dict | None:
        """domain 행에서 rules_json 을 읽어 캐시에 보관한다. 규칙 없으면 None."""
        now = time.monotonic()
        cached = self._cache.get(host)
        if cached:
            rules, cached_at = cached
            if now - cached_at < config.RULES_CACHE_TTL_SECONDS:
                return rules or None  # 빈 dict {} 는 None 으로 취급

        # 캐시 미스 또는 만료 — DB 값으로 갱신
        rules: dict = {}
        if domain_row and domain_row.get("rules_enabled") and domain_row.get("rules_json"):
            raw = domain_row["rules_json"]
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}
            rules = raw or {}

        self._cache[host] = (rules, now)
        return rules or None

    def extract(
        self,
        url: str,
        html: str,
        host: str,
        rules: dict,
        source_type: str = "",
        keyword: str = "",
        keyword_id: int | None = None,
    ) -> CollectedContent | ExtractionFailure:
        """rules_json 으로 HTML(또는 JSON API)에서 필드를 추출한다."""
        if "json_api" in rules:
            return self._extract_json_api(url, rules, source_type, keyword, keyword_id)
        if "amp_url" in rules:
            return self._extract_amp(url, rules, source_type, keyword, keyword_id)
        if "next_data" in rules:
            return self._extract_next_data(url, html, rules, source_type, keyword, keyword_id)
        return self._extract_html(url, html, rules, source_type, keyword, keyword_id)

    def _extract_html(
        self,
        url: str,
        html: str,
        rules: dict,
        source_type: str,
        keyword: str,
        keyword_id: int | None = None,
    ) -> CollectedContent | ExtractionFailure:
        """CSS/XPath 규칙으로 HTML 에서 필드를 추출한다."""
        title  = _apply_rule(html, rules.get("title"))
        body   = _apply_rule(html, rules.get("body"))
        author = _apply_rule(html, rules.get("author")) or None

        published_at_rule = rules.get("published_at")
        published_at = _parse_date(
            _apply_rule(html, published_at_rule),
            (published_at_rule or {}).get("date_format"),
        )

        if failure := check_title(url, title, "rule_html"):
            return failure

        min_body = int(rules.get("min_body_len", _DEFAULT_MIN_BODY_LEN_HTML))
        if failure := check_body_length(url, body, min_body, "rule_html"):
            return failure

        has_css = any(
            isinstance(rules.get(f), dict) and "css" in rules[f]
            for f in ("title", "body")
        )
        return build_content(
            url=url, title=title, body=body,
            source_type=source_type, keyword=keyword, keyword_id=keyword_id,
            extraction_method="rule:css" if has_css else "rule:xpath",
            published_at=published_at, author=author,
        )

    def _extract_amp(
        self,
        url: str,
        rules: dict,
        source_type: str,
        keyword: str,
        keyword_id: int | None = None,
    ) -> CollectedContent | ExtractionFailure:
        """AMP URL 로 변환해 정적 fetch 후 CSS/XPath 규칙으로 추출한다."""
        spec = rules["amp_url"]
        amp_url = url.replace(spec["pattern"], spec["replacement"])

        try:
            from app.fetch._client import make_client
            with make_client() as client:
                resp = client.get(amp_url)
                if resp.status_code == 404:
                    return ExtractionFailure(
                        url=url,
                        error_code=ErrorCode.FETCH_404,
                        error_msg="amp_url: 404 not found",
                        is_permanent=True,
                    )
                resp.raise_for_status()
                amp_html = resp.text
        except Exception as exc:
            return ExtractionFailure(
                url=url,
                error_code=ErrorCode.FETCH_CONNECTION,
                error_msg=f"amp_url fetch failed: {exc}",
                is_permanent=False,
            )

        return self._extract_html(url, amp_html, rules, source_type, keyword, keyword_id)

    def _extract_next_data(
        self,
        url: str,
        html: str,
        rules: dict,
        source_type: str,
        keyword: str,
        keyword_id: int | None = None,
    ) -> "CollectedContent | ExtractionFailure":
        """<script id="__NEXT_DATA__"> 임베드 JSON 에서 필드를 추출한다."""
        spec = rules["next_data"]

        # __NEXT_DATA__ 파싱
        try:
            script = HTMLParser(html).css_first("script#__NEXT_DATA__")
            if not script:
                return ExtractionFailure(
                    url=url,
                    error_code=ErrorCode.PARSE_ERROR,
                    error_msg="next_data: __NEXT_DATA__ script not found",
                    is_permanent=False,
                )
            data = json.loads(script.text())
        except Exception as exc:
            return ExtractionFailure(
                url=url,
                error_code=ErrorCode.PARSE_ERROR,
                error_msg=f"next_data: JSON parse failed: {exc}",
                is_permanent=False,
            )

        # root 경로로 콘텐츠 객체 이동
        root_path = spec.get("root", "")
        obj = data
        if root_path:
            for key in root_path.split("."):
                obj = obj.get(key, {}) if isinstance(obj, dict) else {}

        title        = _json_path(obj, spec.get("title", ""))
        author       = _json_path(obj, spec.get("author", "")) or None
        published_at = _parse_iso(_json_path(obj, spec.get("published_at", "")))

        # 본문: 배열 필드에서 특정 type 의 content 를 이어 붙인다
        body = ""
        body_array_path = spec.get("body_array", "")
        if body_array_path:
            items = obj
            for key in body_array_path.split("."):
                items = items.get(key, []) if isinstance(items, dict) else []
            type_key    = spec.get("body_type_key", "type")
            type_value  = spec.get("body_type_value", "text")
            content_key = spec.get("body_content_key", "content")
            parts = [
                item[content_key]
                for item in (items if isinstance(items, list) else [])
                if isinstance(item, dict)
                and item.get(type_key) == type_value
                and item.get(content_key)
            ]
            body = "\n".join(parts)
        else:
            body = _json_path(obj, spec.get("body", ""))

        if failure := check_title(url, title, "next_data"):
            return failure

        min_body = int(rules.get("min_body_len", _DEFAULT_MIN_BODY_LEN_NEXT_DATA))
        if failure := check_body_length(url, body, min_body, "next_data"):
            return failure

        return build_content(
            url=url, title=title, body=body,
            source_type=source_type, keyword=keyword, keyword_id=keyword_id,
            extraction_method="rule:next_data",
            published_at=published_at, author=author,
        )

    def _extract_json_api(
        self,
        url: str,
        rules: dict,
        source_type: str,
        keyword: str,
        keyword_id: int | None = None,
    ) -> CollectedContent | ExtractionFailure:
        """JSON API 를 직접 호출해 CollectedContent 을 추출한다."""
        spec = rules["json_api"]

        # 원래 URL 의 쿼리 파라미터에서 API 키 값 추출
        param_name = spec.get("url_param", "")
        param_value = ""
        if param_name:
            qs = parse_qs(urlparse(url).query)
            values = qs.get(param_name, [])
            param_value = values[0] if values else ""

        api_url = spec["url_template"].replace(f"{{{param_name}}}", param_value)

        # JSON API 호출
        try:
            from app.fetch._client import make_client
            with make_client() as client:
                resp = client.get(api_url)
                if resp.status_code == 404:
                    return ExtractionFailure(
                        url=url,
                        error_code=ErrorCode.FETCH_404,
                        error_msg="json_api: 404 not found (삭제된 글)",
                        is_permanent=True,
                    )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return ExtractionFailure(
                url=url,
                error_code=ErrorCode.FETCH_CONNECTION,
                error_msg=f"json_api fetch failed: {exc}",
                is_permanent=False,
            )

        # API 응답 자체가 실패를 나타내는 경우 (예: 삭제된 글)
        if not data.get("isSuccess", True):
            return ExtractionFailure(
                url=url,
                error_code=ErrorCode.FETCH_404,
                error_msg=f"json_api: isSuccess=false — {data.get('message', '삭제된 글')}",
                is_permanent=True,
            )

        # 점(.) 경로로 JSON 필드 추출
        title        = _json_path(data, spec.get("title", ""))
        author       = _json_path(data, spec.get("author", "")) or None
        published_at = _parse_iso(_json_path(data, spec.get("published_at", "")))

        # body: body_html → body_css 로 파싱, 없으면 body 직접
        body_html = _json_path(data, spec.get("body_html", ""))
        body_css  = spec.get("body_css", "")

        # body_json_fallback: body_html 이 없을 때(예: 네이버 종목토론 리서치 게시글은
        # contentHtml 이 null 이고 실제 본문이 contentJsonSwReplaced 라는 "JSON 문자열을
        # 담은 문자열 필드" 안에 있음) 그 필드를 한 번 더 JSON 파싱해 title/content 를 쓴다.
        # 이 fallback 콘텐츠는 body_css 대상이 아닌 별도 포맷(<p> 등)이라 태그만 벗겨낸다.
        used_fallback = False
        fallback_path = spec.get("body_json_fallback")
        if not body_html and fallback_path:
            raw = _json_path(data, fallback_path)
            if raw:
                try:
                    inner = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    inner = {}
                if inner.get("content"):
                    body_html = inner["content"]
                    used_fallback = True
                    title = inner.get("title") or title

        if body_html and body_css and not used_fallback:
            body = _extract_css(body_html, body_css)
        elif body_html:
            body = HTMLParser(body_html).text(strip=True)
        else:
            body = _json_path(data, spec.get("body", ""))

        if failure := check_title(url, title, "json_api"):
            return failure

        min_body = int(rules.get("min_body_len", _DEFAULT_MIN_BODY_LEN_JSON_API))
        if failure := check_body_length(url, body, min_body, "json_api"):
            return failure

        return build_content(
            url=url, title=title, body=body,
            source_type=source_type, keyword=keyword, keyword_id=keyword_id,
            extraction_method="rule:json_api",
            published_at=published_at, author=author,
        )


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _apply_rule(html: str, rule: dict | None) -> str:
    """단일 필드 규칙을 HTML 에 적용해 텍스트를 반환한다. 실패 시 빈 문자열."""
    if not rule:
        return ""

    try:
        if "css" in rule:
            return _extract_css(html, rule["css"])
        if "xpath" in rule:
            return _extract_xpath(html, rule["xpath"])
    except Exception:
        pass

    return ""


def _parse_date(text: str, date_format: str | None) -> datetime | None:
    """텍스트를 KST datetime 으로 파싱한다. 실패하면 None.

    date_format 에 %z 로 오프셋이 파싱되면 그 시각 기준으로 KST 변환하고,
    오프셋이 없으면(naive) 원본이 이미 KST 현지시각이라고 가정해 그대로 라벨링한다.
    """
    if not text or not date_format:
        return None
    try:
        parsed = datetime.strptime(text.strip(), date_format)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(_KST)
    return parsed.replace(tzinfo=_KST)


def _extract_css(html: str, selector: str) -> str:
    tree = HTMLParser(html)
    nodes = tree.css(selector)
    if not nodes:
        return ""
    # 여러 노드가 매칭되면 줄바꿈으로 이어 붙인다 (body 에서 <p> 여러 개 처리).
    return "\n".join(n.text(strip=True) for n in nodes if n.text(strip=True))


def _json_path(data: dict, path: str) -> str:
    """점(.) 구분 경로로 JSON 값을 추출한다. 예: 'result.writer.nickname'"""
    if not path:
        return ""
    try:
        node = data
        for key in path.split("."):
            node = node[key]
        return str(node) if node is not None else ""
    except (KeyError, TypeError):
        return ""


def _parse_iso(text: str) -> "datetime | None":
    """ISO 8601 문자열을 KST datetime 으로 파싱한다."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.rstrip("Z"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_KST)
        return dt
    except ValueError:
        return None


def _extract_xpath(html: str, expression: str) -> str:
    from lxml import etree
    tree = etree.HTML(html)
    if tree is None:
        return ""
    results = tree.xpath(expression)
    if not results:
        return ""
    # substring-after 등 XPath 스칼라 함수는 문자열을 직접 반환한다.
    # 이 경우 for 루프로 순회하면 문자 단위로 쪼개지므로 먼저 체크한다.
    if isinstance(results, str):
        return results.strip()
    # 속성값은 문자열, 노드는 텍스트로 변환
    texts = []
    for r in results:
        if isinstance(r, str):
            texts.append(r.strip())
        elif hasattr(r, "text_content"):
            texts.append(r.text_content().strip())
    return "\n".join(t for t in texts if t)
