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

### Step 2 — 규칙 없이도 되는지 먼저 확인

```bash
python scripts/run_extraction.py --url "https://www.example.com/article/123" --dry-run
```

이 명령은 fetch 직전에 현재 `t_domain` 행의 규칙 상태도 같이 출력한다(`domain rule : 없음` /
`있으나 rules_enabled=False` / 규칙 타입). 이미 규칙이 등록돼 있는데도 실패하고 있다면
여기서 바로 드러난다.

- 성공(`method: trafilatura` 또는 `readability`)이면 규칙 불필요. Step 3~4 건너뛰고 바로
  Step 5(재투입)로.
- `PARSE_ERROR`/`BODY_TOO_SHORT` 등 실패면 Step 3으로.

### Step 3 — 후보 규칙을 DB에 저장하고 반복 검증

`run_extraction.py`는 규칙 후보를 파일/인자로 넘기는 옵션이 없고 `t_domain.rules_json`을
그대로 읽는다. 그래서 Step 1에서 찾은 셀렉터를 먼저 DB에 써넣고(`t_domain`은 sparse
테이블이라 해당 host 행이 아직 없을 수 있음 — upsert), `--dry-run`으로 결과를 보는 식으로
반복한다(파일 저장은 안 하니 실제 수집에는 영향 없음).

```sql
INSERT INTO t_domain (host, rules_json, rules_enabled, rules_version, updated_by)
VALUES (
    'www.example.com',
    '{"title":{"css":"h1.article-title"},"body":{"css":"div.article-body"},"min_body_len":100}',
    1, 1, 'manual-debug'
)
ON DUPLICATE KEY UPDATE
    rules_json    = VALUES(rules_json),
    rules_enabled = 1,
    rules_version = rules_version + 1,
    updated_by    = VALUES(updated_by);
```

```bash
python scripts/run_extraction.py --url "https://www.example.com/article/123" --dry-run
```

`method: rule:css` 또는 `rule:xpath`로 나오고 `body_len`이 충분하면 성공. 부족하면
셀렉터를 고쳐 위 `UPDATE`를 다시 실행하고 재확인 — 이 사이클을 반복한다.

> crawler-admin 이 떠 있다면 SQL 대신 `/domains` 화면의 "규칙 편집" 모달로 같은 작업을
> UI에서 할 수 있다(저장 시 `rules_version` 자동 증가는 동일하게 적용됨).

### Step 4 — seed_domain_rules.py 동기화

검증이 끝난 규칙을 `seed_domain_rules.py`의 `_RULES` 리스트에도 반영해둔다(5절 참고) —
그래야 테이블 재초기화 시 방금 검증한 규칙이 사라지지 않는다.

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

> **도메인 규칙이 없는 상태에서 BODY_TOO_SHORT가 나는 경우** — `library_chain.py`에
> 자체 안전망이 있다. trafilatura(`favor_precision=True`)가 본문 대신 바이라인/날짜
> 같은 엉뚱한 영역만 짧게(200자 미만) 잡으면, readability도 자동으로 시도해서 더 긴
> 쪽을 채택한다 (예: `www.fomos.kr` — trafilatura는 24자만, readability는 849자
> 정상 추출). 둘 다 시도해도 짧으면 그대로 실패하니, 이 경우엔 Step 1~3대로 CSS
> 규칙을 작성한다. 이 폴백은 최후의 안전망일 뿐 — 도메인 규칙이 있으면 그게 항상
> library_chain보다 먼저 시도되고 더 정확하므로(`app/extraction/extractor.py`),
> 반복 실패하는 도메인은 폴백에 기대지 말고 규칙을 등록하는 걸 권장한다.

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

### SSL/TLS 접속 실패

`ConnectError: [SSL: WRONG_VERSION_NUMBER]` / `CERTIFICATE_VERIFY_FAILED`(hostname
mismatch, 자체서명, 만료) / `DH_KEY_TOO_SMALL` 등 — HTTPS 접속 자체가 서버 쪽에서
구조적으로 깨진 경우가 많다. 규칙(CSS/XPath) 문제가 아니라 fetch 단계에서 이미
실패하는 것이라, 먼저 HTTP(평문)로 같은 URL이 응답하는지 확인한다.

```bash
curl -sSI --max-time 8 "http://www.example.com/"
```

- **200 정상 응답 + 리다이렉트 없음** → `rules_json`에 `"force_http": true`만 추가하면
  된다(별도 CSS 규칙 불필요 — fetch 직전 스킴만 http로 다운그레이드하고 이후는
  평소처럼 library_chain/규칙 엔진이 처리). `www.celuvmedia.com`, `www.thekorea.kr`,
  `knpp.co.kr`, `www.worktoday.co.kr`, `www.sisacast.kr`, `www.seouleconews.com`,
  `www.financialreview.co.kr`, `autotimes.co.kr` 가 이 패턴.
- **301/302로 다시 `https://`로 리다이렉트됨** → `force_http`가 무의미하다(리다이렉트
  따라가면 결국 같은 SSL 에러로 돌아옴). `www.tjb.co.kr`(`DH_KEY_TOO_SMALL`)이 이
  케이스 — OpenSSL 3.x가 약한 DH 파라미터를 거부하는 게 근본 원인인데, 이건
  `legacy_renegotiation`과 별개로 SECLEVEL을 낮추는 새 SSL 컨텍스트가 필요해서
  `app/fetch/_client.py`에 옵션을 추가하지 않는 한 규칙만으로는 해결 안 됨.
- **구형 TLS 재협상을 요구하는 서버**(OpenSSL 3.x가 `UNSAFE_LEGACY_RENEGOTIATION_DISABLED`로
  기본 거부) → `"legacy_renegotiation": true`. `baotintuc.vn` 참고.

**혼동하지 말 것 — 봇 차단과의 구분**: `x-amzn-waf-action: challenge` 헤더나 Cloudflare
"Just a moment..." 페이지, headless로 열어도 "JavaScript is disabled" 벽만 나오는
경우는 SSL/TLS 문제가 아니라 WAF/봇 챌린지다. 이건 `force_http`는 물론 어떤
`rules_json` 설정으로도 해결 안 된다 — HTML 자체가 안 오기 때문. (`www.imdb.com`,
`kr.investing.com` 확인 사례.)

### SPA / JavaScript 렌더링 필요

`fetch_html.py` 정적 모드에서 본문이 비어 있으면 headless가 필요하다.

```bash
# headless 로 렌더링된 HTML 확인 (본문 후보 요소가 이제 보이는지)
python scripts/fetch_html.py --url "https://www.example.com/article/123" --headless
```

`render_mode`는 `rules_json`과 별개의 `t_domain` 컬럼이라, Step 3의 `UPDATE`에 같이
넣어야 한다:

```sql
INSERT INTO t_domain (host, render_mode, rules_json, rules_enabled, rules_version, updated_by)
VALUES (
    'www.example.com', 'headless',
    '{"title":{"css":"..."},"body":{"css":"..."},"min_body_len":100}',
    1, 1, 'manual-debug'
)
ON DUPLICATE KEY UPDATE
    render_mode   = VALUES(render_mode),
    rules_json    = VALUES(rules_json),
    rules_enabled = 1,
    rules_version = rules_version + 1,
    updated_by    = VALUES(updated_by);
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
| `force_http` | HTTPS가 구조적으로 깨진 도메인 → fetch 직전 http로 다운그레이드 ("SSL/TLS 접속 실패" 절 참고) |
| `legacy_renegotiation` | 구형 TLS 재협상을 요구하는 서버 대응 (`baotintuc.vn` 등) |

---

## 5. seed_domain_rules.py 동기화

Step 3의 디버그용 `UPDATE`로 DB에 저장한 규칙은 `seed_domain_rules.py`에도 반영해두어야
테이블 재초기화 시 사라지지 않는다.

> **주의**: Step 3의 SQL 예시는 기본적으로 `rules_json / rules_enabled / rules_version` 만
> 다룬다. `render_mode` 와 `crawl_delay_ms` 는 별개 컬럼이라(SPA 케이스처럼 의도적으로
> 같이 넣지 않는 한) 디버그 루프에 안 딸려온다 — headless 사이트나 딜레이가 필요한
> 사이트는 반드시 `seed_domain_rules.py` 에도 해당 값을 추가해
> `python scripts/seed_domain_rules.py` 로 적용해야 한다.

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
