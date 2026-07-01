# 도메인 규칙 진단 · 수정 가이드

추출 실패 URL이 쌓였을 때 원인을 찾고 도메인 규칙을 삽입/수정하는 절차를 정리한다.

---

## 1. 실패 URL 파악

### 에러 로그에서 확인

```
# error.log 에서 PARSE_ERROR 찾기
grep 'PARSE_ERROR\|BODY_TOO_SHORT\|TITLE_EMPTY' logs/error.log | tail -30

# 특정 도메인만 필터
grep 'host=news.example.com' logs/error.log
```

### DB에서 확인

```sql
-- 도메인별 실패 현황 (많은 순)
SELECT host, last_error_code, COUNT(*) AS cnt
FROM t_crawl_url
WHERE status IN ('failed_permanent', 'failed_transient', 'dead')
GROUP BY host, last_error_code
ORDER BY cnt DESC
LIMIT 30;

-- 특정 도메인 실패 URL 목록
SELECT id, url, status, attempt_count, last_error_code, last_error_msg
FROM t_crawl_url
WHERE host = 'www.example.com'
  AND status IN ('failed_permanent', 'failed_transient', 'dead')
ORDER BY updated_at DESC
LIMIT 20;
```

---

## 2. 진단 · 수정 절차

### Step 1 — HTML 구조 파악

실패한 URL 하나를 골라 HTML을 확인한다.

```bash
python scripts/fetch_html.py --url "https://www.example.com/article/123"
```

출력에서 **"본문 후보 요소"** 섹션의 클래스명을 보고 제목·본문·날짜 컨테이너를 특정한다.
JavaScript 렌더링이 필요한 사이트는 `--headless` 옵션을 사용한다.

```bash
python scripts/fetch_html.py --url "https://www.example.com/article/123" --headless
```

> **확인 포인트**
> - 본문이 HTML에 존재하는가? (SPA라면 `<div id="__NEXT_DATA__">` 또는 API 호출 여부 확인)
> - iframe 안에 본문이 있는가?
> - 기사 본문 컨테이너의 class/id가 명확한가?

### Step 2 — 현재 규칙 진단

```bash
python scripts/fix_domain_rule.py --url "https://www.example.com/article/123"
```

출력에서 두 가지를 확인한다.

| 출력 | 의미 |
|---|---|
| `domain rule : (없음)` | 도메인 행 자체가 없음 → 라이브러리 체인만 시도 |
| `rules_enabled=False` | 규칙이 등록됐지만 비활성 → 라이브러리 체인만 시도 |
| `[CSS MISS]` | 셀렉터가 HTML에 없음 |
| `[CSS OK] 총 29자` | 셀렉터는 맞지만 엉뚱한 노드를 잡음 |
| `body_len=X < min_body_len` | 셀렉터는 맞지만 본문이 너무 짧음 |

> **도메인 규칙이 없는 경우** — `fix_domain_rule.py`는 CSS/XPath 규칙 진단만 하고 라이브러리 체인은 시도하지 않는다.
> 규칙 없이 라이브러리(trafilatura → readability)가 본문을 잡을 수 있는지 먼저 확인한다.
>
> ```bash
> python scripts/run_extraction.py --url "https://www.example.com/article/123" --dry-run
> ```
>
> - 성공(`method: trafilatura` 또는 `readability`)이면 규칙 불필요. Step 3~4 건너뜀.
> - 실패(`PARSE_ERROR`)면 Step 3으로 넘어가 규칙을 작성한다.

### Step 3 — 새 규칙 작성 · 테스트

Step 1에서 찾은 셀렉터로 규칙을 작성하고 테스트한다.

```bash
python scripts/fix_domain_rule.py \
  --url "https://www.example.com/article/123" \
  --rule '{"title":{"css":"h1.article-title"},"body":{"css":"div.article-body"},"min_body_len":100}'
```

출력이 `[성공]`이면 저장 프롬프트가 뜬다. `--save` 플래그로 프롬프트 없이 바로 저장한다.

```bash
python scripts/fix_domain_rule.py \
  --url "https://www.example.com/article/123" \
  --rule '...' \
  --save
```

### Step 4 — 추출 재검증

규칙 저장 후 `run_extraction.py`로 실제 추출 결과를 확인한다.

```bash
python scripts/run_extraction.py \
  --url "https://www.example.com/article/123" \
  --dry-run
```

`method: rule:css` 또는 `rule:xpath`로 나오면 새 규칙이 적용된 것이다.

### Step 5 — 실패 URL 재투입

검증이 끝났으면 해당 도메인의 실패 URL을 재투입한다.

```sql
UPDATE t_crawl_url
SET status = 'discovered',
    next_retry_at = NOW(),
    attempt_count = 0
WHERE host = 'www.example.com'
  AND status IN ('failed_permanent', 'failed_transient', 'dead');
```

---

## 3. 실패 유형별 대응

### PARSE_ERROR — "trafilatura and readability both returned nothing"

라이브러리가 본문을 인식하지 못한 경우. 가장 흔한 케이스.

→ Step 1~3 절차대로 CSS 규칙 작성.

### BODY_TOO_SHORT

셀렉터는 맞지만 너무 작은 영역을 잡은 경우.

```bash
# fetch_html.py 에서 잡힌 노드 미리보기 확인
python scripts/fetch_html.py --url "..."
```

→ 더 큰 컨테이너 셀렉터로 변경하거나 `min_body_len` 값을 낮춘다.

### TITLE_EMPTY

제목 셀렉터 미스. `og:title` 메타 태그를 fallback으로 자주 사용한다.

```json
{"title": {"xpath": "//meta[@property='og:title']/@content"}}
```

### FETCH_BLOCKED / 429

도메인이 봇 차단 중인 경우. 규칙 문제가 아니다.

```sql
-- 차단 해제 (cooldown 수동 제거)
UPDATE t_domain
SET cooldown_until = NULL, recent_fail_count = 0
WHERE host = 'www.example.com';
```

### SPA / JavaScript 렌더링 필요

`fetch_html.py` 정적 모드에서 본문이 비어 있으면 headless가 필요하다.

```bash
python scripts/fix_domain_rule.py --host www.example.com
# render_mode 확인

python scripts/fix_domain_rule.py \
  --url "..." \
  --rule '{"title":{"css":"..."},"body":{"css":"..."},"min_body_len":100}' \
  --save
# 규칙 저장 후 seed_domain_rules.py 의 render_mode 도 headless 로 업데이트
```

`seed_domain_rules.py`의 해당 도메인 항목도 `"render_mode": "headless"`로 맞춰두면
테이블 재초기화 시 일관성이 유지된다.

---

## 4. 규칙 문법 빠른 참조

```json
{
  "title":        {"css": "h1.article-title"},
  "body":         {"css": "div.article-body"},
  "author":       {"css": "span.byline"},
  "published_at": {"css": "span.date", "date_format": "%Y.%m.%d %H:%M"},
  "min_body_len": 100
}
```

| 키 | 설명 |
|---|---|
| `css` | selectolax CSS 셀렉터. 매칭 노드가 여러 개면 줄바꿈으로 이어 붙임 |
| `xpath` | lxml XPath. 속성값(`//@attr`)·텍스트 노드(`text()`)·스칼라 함수 모두 지원 |
| `date_format` | `strptime` 포맷. 미지정 시 날짜 파싱 안 함 |
| `min_body_len` | 본문 최소 길이(기본 200). 미만이면 `BODY_TOO_SHORT` 실패 |

**특수 규칙 모드** — 상세 문법은 `app/extraction/rule_engine.py` 상단 docstring 참조.

| 최상위 키 | 설명 |
|---|---|
| `json_api` | JSON API 직접 호출 (네이버 증권 종목토론 등) |
| `amp_url` | AMP 페이지로 변환 후 추출 (SBS Biz 등 CSR 사이트) |
| `next_data` | `<script id="__NEXT_DATA__">` 임베드 JSON 추출 (뉴스1 등 Next.js) |
| `headless_wait_for` | headless 모드에서 특정 셀렉터 출현 대기 (JTBC 등) |

---

## 5. seed_domain_rules.py 동기화

`fix_domain_rule.py --save`로 DB에 저장한 규칙은 `seed_domain_rules.py`에도 반영해두어야
테이블 재초기화 시 사라지지 않는다.

> **주의**: `fix_domain_rule.py --save` 는 `rules_json / rules_enabled / rules_version` 만 저장한다.
> `render_mode` 와 `crawl_delay_ms` 는 저장하지 않으므로, headless 사이트나 딜레이가 필요한 사이트는
> 반드시 `seed_domain_rules.py` 에도 해당 값을 추가해 `python scripts/seed_domain_rules.py` 로 적용해야 한다.

```python
# seed_domain_rules.py 의 _RULES 리스트에 추가
{
    "host": "www.example.com",
    "render_mode": "static",
    "crawl_delay_ms": 1000,
    "rules_enabled": True,
    "updated_by": "domain-analysis",
    "rules_json": {
        "title":    {"css": "h1.article-title"},
        "body":     {"css": "div.article-body"},
        "min_body_len": 100,
    },
},
```
