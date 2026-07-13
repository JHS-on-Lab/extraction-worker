# extraction-worker — 설계 문서

> 이 문서는 구현 에이전트(Claude Code)가 읽고 개발에 착수하기 위한 설계 명세다.
> 명세에서 벗어나야 할 경우 이 문서를 먼저 갱신한다.

---

## 1. 개요

`t_crawl_url` 테이블에서 URL 을 꺼내 본문을 추출하고, 파일(JSONL) 또는 Solr 에 저장하는 서비스다.

- **입력**: MySQL `t_crawl_url` 테이블 (discovery-worker / rescrape-dispatcher 가 채움)
- **출력**: `SINK_TYPE` 설정에 따라 파일(JSONL) 또는 Solr 문서
- **이전 처리**: discovery-worker(포털 검색 스크래핑) 또는 rescrape-dispatcher(Solr 재조회)가
  `t_crawl_url` 에 URL 을 `discovered` 상태로 투입한다.

### 1.1 discovery-worker / rescrape-dispatcher 와의 관계

```
[discovery-worker]           [rescrape-dispatcher]
  포털 검색 스크래핑            Solr 신규 문서 조회
    → t_crawl_url (discovered)   → t_crawl_url (discovered)
                    │                      │
                    └──────────┬───────────┘
                                ▼
                       t_crawl_url (MySQL, 공유)
                                │
                                ▼
                       [extraction-worker]
                         claim_next() → fetch → extract → sink
                                │
                                ▼
                       stored / failed_transient / failed_permanent / dead
```

**이 프로젝트는 discovery-worker / rescrape-dispatcher 코드를 수정하거나 공유하지 않는다.**
세 프로젝트는 동일한 MySQL DB(`t_crawl_url`)를 통해서만 소통한다.

---

## 2. 아키텍처

```
                    ┌───────────────────────────────────────────────┐
                    │              extraction-worker                │
                    │                                                │
t_crawl_url ──────► │  CrawlUrlRepo.claim_next()                    │
 (MySQL)            │    낙관적 클레임 (status=extracting)            │
                    │          ↓                                    │
                    │  RateLimiter.wait(host)                       │
                    │          ↓                                    │
                    │  HttpFetcher / HeadlessFetcher                │
                    │    render_mode 에 따라 정적/JS 렌더링 선택       │
                    │          ↓                                    │
                    │  DefaultExtractor                              │
                    │    RuleEngine(도메인 규칙) → LibraryChain(폴백) │
                    │          ↓                                    │
                    │  Sink.write() (버퍼링) → Sink.flush() (확정)   │
                    │          ↓                                    │
   Solr ◄────────── │  CrawlUrlRepo.mark_stored/mark_failed/mark_dead│
   또는 JSONL 파일    │    (flush 성공 확인 후에만 stored 로 표시)      │
                    └───────────────────────┬───────────────────────┘
                                              │
                                              ▼ (daemon thread, 별도 루프)
                                     Reaper: claimed_at 초과 시
                                     attempt_count++ 후 회수/dead 처리
```

---

## 3. 동작 흐름

```
1. 설정 로드 + 검증 (config.validate())
2. HeadlessFetcher / HttpFetcher / DB 엔진을 루프 밖에서 1회 생성 (재사용)
3. Reaper 를 daemon 스레드로 기동
4. 메인 루프:
   a. heartbeat 주기 도달 시: healthcheck 파일 갱신 + pending flush + 통계 로그
   b. claim_next() 로 URL 1건 점유
      - 없으면 idle: pending flush + 통계 로그 + IDLE_SEC(10초) sleep
   c. RateLimiter.wait(host)
   d. render_mode 에 따라 fetch (static/headless/headless_iframe/headless_shadow)
      - fetch 실패(4xx/5xx/네트워크 예외) → _handle_failure() → 다음 루프
   e. DefaultExtractor.extract() — 도메인 규칙 우선, 실패 시 라이브러리 체인 폴백
      - 추출 실패(TITLE_EMPTY/BODY_TOO_SHORT/PARSE_ERROR 등) → _handle_failure() → 다음 루프
   f. domain_repo.upsert_health(success=True) 갱신
   g. sink.write() — 버퍼에 쌓기만 함(SolrSink) 또는 즉시 기록(FileSink)
      - pending 목록에 추가, len(pending) >= sink.batch_size 면 flush
   h. _flush_pending(): sink.flush() 성공 시에만 mark_stored, 실패 시 전체 failed_transient
5. 종료(SIGTERM/SIGINT/예외) → finally 블록에서 남은 pending 반드시 flush
```

---

## 4. Deferred flush — Sink 데이터 정합성

### 4.1 문제

`SolrSink.write()`는 버퍼에만 쌓고, 실제 Solr 전송은 `flush()`가 호출됐을 때 일어난다
(`app/sink/solr_sink.py`). 과거엔 배치가 차면 `write()` 내부에서 자동으로 `flush()`를
호출했는데, 그 시점에 flush 가 실패하면 이미 그 배치에 포함된 앞쪽 항목들은 DB 에
`stored` 로 표시된 뒤라 — **DB 엔 저장 완료로 남았지만 Solr 엔 실제로 없는 문서**가
생겼다.

### 4.2 해결 — `_PendingStore` + `_flush_pending()`

`app/worker/extraction_worker.py`:

- `sink.write()` 성공 직후 곧바로 `mark_stored()` 를 부르지 않는다. 대신 `_PendingStore
  (item_id, extraction_method, attempt)` 를 `pending` 리스트에 쌓아 둔다.
- `pending` 이 `sink.batch_size` 개에 도달하거나, idle/heartbeat/루프 종료(finally) 시점에
  `_flush_pending()` 을 호출한다.
- `_flush_pending()`:
  - `sink.flush()` 성공 → 그제서야 `pending` 전체를 `mark_stored()` 로 확정.
  - `sink.flush()` 실패(circuit open 포함) → `pending` 전체를 `failed_transient` 로
    되돌려(백오프 후 재시도) 유실을 방지.
- 프로세스 종료 경로(정상/예외/`SIGTERM`→`sys.exit()`→`SystemExit`) 어디로 빠지든
  `try/finally` 로 감싼 메인 루프의 `finally` 에서 반드시 `_flush_pending()` 을 호출한다.

### 4.3 Sink 별 batch_size

| Sink | batch_size | 이유 |
|---|---|---|
| `FileSink` | `1` | 매 `write()` 가 파일에 즉시 append 되어 이미 durable — flush 는 no-op |
| `SolrSink` | `config.SOLR_BATCH_SIZE` (기본 100) | HTTP 요청 수를 줄이기 위해 배치로 upsert |

---

## 5. Claim 소유권 검증 — 동시성 안전

### 5.1 문제

`claim_next()` 로 URL 을 점유한 워커가 처리 도중 비정상적으로 느려지면, 그사이 Reaper
가 `CLAIM_TIMEOUT_SECONDS`(기본 300초) 초과로 판단해 그 행을 다른 워커에게 회수해줄 수
있다. 이때 원래(느린) 워커가 뒤늦게 처리를 끝내고 `mark_stored()` 를 호출하면, 이미
다른 워커가 처리 중이거나 완료한 결과를 조건 없이 덮어써버린다.

### 5.2 해결

`CrawlUrlRepo.mark_stored / mark_failed / mark_dead` 모두 `WHERE id=:id AND
status='extracting' AND claimed_by=:worker_id` 조건으로 UPDATE 하고, `rowcount > 0` 을
`bool` 로 반환한다. 호출부(`_flush_pending`, `_handle_failure`)는 반환값이 `False` 면
"소유권을 이미 잃었다"는 뜻으로 보고 `warning` 로그만 남기고 넘어간다(예외를 던지지
않는다 — Solr 에는 이미 반영됐을 수 있으므로 이중 처리 실패로 취급하지 않는다).

---

## 6. Reaper — 점유 회수 + attempt_count

`app/worker/reaper.py`. `run_extraction_loop()` 과 별개로 daemon 스레드에서 5분마다
`CrawlUrlRepo.recover_timed_out(CLAIM_TIMEOUT_SECONDS)` 를 호출한다.

```sql
UPDATE t_crawl_url
SET status = CASE WHEN attempt_count + 1 >= :max_attempts THEN 'dead' ELSE 'discovered' END,
    attempt_count   = attempt_count + 1,
    claimed_at      = NULL,
    claimed_by      = NULL,
    ...
WHERE status = 'extracting'
  AND claimed_at < NOW() - INTERVAL :sec SECOND
```

`attempt_count` 를 증가시키는 이유: 특정 페이지에서 headless 가 매번 멈추는 것처럼
**구조적으로 항상 타임아웃나는 URL**이 있으면, 증가 없이는 reaper 가 영원히
`discovered` 로 되돌리고 워커가 다시 집어가 다시 타임아웃나는 무한 루프가 된다.
`MAX_ATTEMPTS` 도달 시 `dead` 로 처리해 종료시킨다.

daemon 스레드(`daemon=True`)이므로 메인 스레드(추출 루프)가 종료되면 자동으로 함께
종료된다 — 별도 종료 처리 불필요.

---

## 7. Fetcher 재사용 및 복원력

### 7.1 HttpFetcher — 커넥션 재사용

`app/fetch/http_client.py`. 과거엔 `fetch()` 호출마다 `httpx.Client` 를 새로 만들고
버렸다(TCP+TLS 핸드셰이크 매번 반복). 이제 `HttpFetcher` 인스턴스를 루프 밖에서 한 번만
생성해 재사용하고, 내부적으로 일반 client 와 legacy TLS client(`allow_legacy_renegotiation`,
구형 재협상 요구 서버 대응, 예: baotintuc.vn) 를 지연 생성해 각각 캐싱한다. `verify`
(SSLContext) 는 client 생성 시점에 고정되므로 두 client 를 분리해야 한다.

### 7.2 HeadlessFetcher — 브라우저 크래시 자동 복구

`app/fetch/headless.py`. `_ensure_browser()` 가 `self._browser.is_connected()` 로 생존
여부를 매번 확인한다. 과거엔 `self._browser is not None` 만 봐서, 브라우저 프로세스가
크래시(OOM 등)해도 죽은 참조를 계속 재사용 — 이후 모든 headless 요청이 죽은 연결로
실패하고, **워커를 재시작하기 전까지 `render_mode=headless(_iframe/_shadow)` 인 모든
도메인이 계속 실패**했다. 연결이 끊긴 걸 감지하면 `_teardown()` 으로 정리 후 새
브라우저를 기동한다.

### 7.3 render_mode 3종

| render_mode | 동작 |
|---|---|
| `static` | `HttpFetcher` — 정적 HTML GET |
| `headless` | Playwright, `page.content()` 그대로 반환 |
| `headless_with_iframe` | 로드된 iframe 내용을 `<div id="frame_...">` 로 외부 HTML 에 주입 (예: finance.naver.com 종목토론) |
| `headless_with_shadow` | open shadow root 내용을 `<div data-shadow-host="...">` 로 주입 (예: msn.com cp-article). closed shadow root 는 접근 불가 |

---

## 8. 추출 전략

`app/extraction/extractor.py` (`DefaultExtractor`) 가 진입점이며 우선순위는:

1. **RuleEngine** (`app/extraction/rule_engine.py`) — `t_domain.rules_json` 이 있으면
   도메인 전용 CSS/XPath/JSON API 규칙으로 추출. 규칙 형식(css/xpath/json_api/amp_url/
   next_data)과 작성법은 [domain-rule-guide.md](domain-rule-guide.md) 참고.
2. **LibraryChain** (`app/extraction/library_chain.py`) — 규칙이 없거나(단, `json_api`
   규칙 실패는 폴백 대상에서 제외) 실패하면 `trafilatura` → 부족하면 `readability` 순으로
   시도, 더 긴 본문을 채택.

### 8.1 공통 헬퍼 — `app/extraction/_common.py`

`check_title()` / `check_body_length()` / `build_content()` 세 함수가 title/body
검증과 `CollectedContent` 조립을 담당하며, `library_chain.py` 와 `rule_engine.py` 의
`_extract_html`/`_extract_next_data`/`_extract_json_api` 가 공통으로 사용한다.

`min_body_len` 기본값은 전략마다 **의도적으로 다르며 통일하지 않는다**:

| 전략 | 기본값 | 이유 |
|---|---|---|
| library_chain (trafilatura/readability) | 200 | 임의 사이트 대상이라 노이즈 많음 |
| rule_html (CSS/XPath) | 200 | 셀렉터가 엉뚱한 짧은 영역을 잡을 위험 |
| rule_next_data | 100 | `__NEXT_DATA__` JSON 은 이미 구조화됨 |
| rule_json_api | 5 | API 응답 필드가 명확한 대상(예: 짧은 종목토론 글) |

값 자체를 통일하려던 리팩토링이 아니라, 왜 다른지 안 보이고 매직넘버로 흩어져 있던 걸
명명 상수로 정리한 것이다. 각 규칙의 `rules_json.min_body_len` 으로 도메인별 override 가능.

---

## 9. URL 정규화 및 crawl_id

`app/domain_logic/url_normalizer.py`:

- `normalize(url)` — http→https, 호스트 소문자, 추적 파라미터 제거, 끝 슬래시 제거,
  기본 포트 제거, 프래그먼트 제거. `t_crawl_url.url_hash`(DB 중복 키)에 사용.
- `crawl_id(url)` — `lookup3ycs64` 해시 기반 16자리 hex. Solr 문서 `id`(멱등 upsert 키)에 사용.
- `;jsessionid=...` 제거: `_JSESSIONID = re.compile(r";jsessionid=[^?#]*", re.IGNORECASE)`.
  과거엔 `r";jsessionid=.*?(?=\?)"` (뒤에 `?` 가 있어야만 매치되는 lookahead) 라서, URL
  끝에 세션ID만 있고 쿼리스트링이 없으면 전혀 매치되지 않아 세션ID가 그대로 남았고, 같은
  문서가 세션마다 다른 `crawl_id` 를 받아 **Solr 에 중복 문서가 생성**됐다. `normalize()`
  는 `urlparse` 가 `;params` 를 자동으로 분리해줘서 이 버그의 영향을 받지 않았고(DB 중복
  키는 정상), Solr 쪽 `crawl_id()` 만 영향을 받았다.

---

## 10. Sink

### 10.1 FileSink vs SolrSink

| | FileSink | SolrSink |
|---|---|---|
| 저장 위치 | `{FILE_SINK_DIR}/{날짜}/{crawler_type}-{worker_id}.jsonl` | Solr HTTP API (`/update`) |
| write() | 즉시 append (durable) | 버퍼에만 축적 |
| flush() | no-op | HTTP POST(commitWithin) — circuit breaker 포함 |
| batch_size | 1 | `SOLR_BATCH_SIZE` (기본 100) |
| 문서 id | 없음(파일이라 불필요) | `crawl_id(url)` — 동일 URL 재수집 시 upsert |

두 구현 모두 `app/sink/serialize.py` 의 `to_doc()` 으로 동일한 필드 스키마를 만든다
(Solr 필드명 기준: id/crawler_type/crawl_runtime_key/host/site/url/title/content/author/
tstamp/doc_version/keyword_id/etc_exact1/source_type). `MASKING_ENABLED=true` 면
`TextMasker` 로 본문/저자를 마스킹 후 저장한다.

### 10.2 Sink 선택 — `make_sink()`

`SINK_TYPE` 환경변수(`file` 기본 / `solr`)에 따라 `app/sink/__init__.py:make_sink()` 가
`crawler_type`/`crawl_runtime_key` 를 다음 규칙으로 resolve 한다:

- `SOLR_DIRECT_ENABLED=true` — `SOLR_URL`/`SOLR_CRAWLER_TYPE` 을 그대로 사용,
  `crawl_runtime_key = {WORKER_ID}_{SOLR_RUNTIME_NAME}` (`SOLR_RUNTIME_NAME` 없으면 `WORKER_ID`)
- `SOLR_DIRECT_ENABLED=false` — `t_crawl_runtime.runtime_name=SOLR_RUNTIME_NAME` 조회해
  `solr_url`/`crawler_type` 획득. 없거나 `use_yn='N'` 이면 `SolrSink` 생성 시 `RuntimeError`.

---

## 11. Rate Limiting / 도메인 정책

`app/fetch/rate_limit.py` (`RateLimiter`), `app/repository/domain_repo.py` (`DomainRepo`).

- 도메인별 `t_domain.crawl_delay_ms` (없으면 `config.DEFAULT_CRAWL_DELAY_MS`) 만큼
  마지막 요청 이후 간격을 두고 요청.
- `t_domain` 은 sparse 테이블 — 오버라이드 필요한 도메인만 행이 존재, 없으면 전역 기본값.
- 매 처리 후 `upsert_health()` 로 `success_rate`(지수이동평균), `avg_body_len`,
  `recent_fail_count` 갱신 — 향후 도메인별 이상 감지/알림에 활용 가능(현재는 조회만 함).
- `RateLimiter._last` 는 메모리 내 `OrderedDict`, 최대 10,000 호스트까지 보관하고
  초과 시 가장 오래된 항목부터 제거(LRU 유사).

---

## 12. 실패 분류 및 재시도

`app/domain_logic/failure_classifier.py`, `app/domain_logic/backoff.py`.

| 판정 | 예 | 처리 |
|---|---|---|
| 영구(`is_permanent=True`) | 404, 410, 400/401/403, TITLE_EMPTY | `failed_permanent` (재시도 없음, `MAX_ATTEMPTS` 도달 시 `dead`) |
| 일시(`is_permanent=False`) | 429, 5xx, timeout, connection error, BODY_TOO_SHORT | `failed_transient` + `next_retry_at` (지수 백오프 + jitter) |

백오프: `min(BACKOFF_BASE_SECONDS * 2^attempt, BACKOFF_MAX_SECONDS)` + `[0, delay*0.2)`
jitter(동시 재시도로 서버에 부담 주는 것 방지). 기본값: base 30초, max 3600초(1시간).

`attempt_count + 1 >= MAX_ATTEMPTS` 면 `is_permanent` 여부와 무관하게 무조건 `dead`.

---

## 13. 모듈 구조

```
app/
  __main__.py                    # 진입점 (argparse, signal, config.validate, reaper 스레드 기동)
  config.py                      # 환경변수 로딩 + validate()
  logging_setup.py                # 로그 파일/콘솔 핸들러 설정
  ports.py                        # Sink Protocol (write/flush/batch_size)
  types.py                        # SourceType, CrawlUrlStatus, RenderMode, FetchResult,
                                   # CollectedContent, ErrorCode, ExtractionFailure 등

  domain_logic/
    url_normalizer.py            # normalize(), url_hash(), crawl_id()
    failure_classifier.py        # classify_http(), classify_exception()
    backoff.py                   # next_retry_at()
    masking.py                   # TextMasker, mask_author()

  fetch/
    _client.py                   # httpx.Client 팩토리 (verify, legacy_renegotiation)
    http_client.py                # HttpFetcher — client 재사용
    headless.py                   # HeadlessFetcher, fetch_by_render_mode()
    rate_limit.py                 # RateLimiter
    proxy.py                      # 프록시 설정 (proxy_tier)

  extraction/
    extractor.py                  # DefaultExtractor — RuleEngine → LibraryChain
    rule_engine.py                # 도메인 규칙 기반 추출 (css/xpath/json_api/amp_url/next_data)
    library_chain.py              # trafilatura → readability 폴백
    _common.py                    # check_title/check_body_length/build_content 공통 헬퍼

  sink/
    __init__.py                   # make_sink() 팩토리
    base.py                       # Sink 재익스포트
    serialize.py                  # CollectedContent → dict (Solr 스키마 필드명)
    file_sink.py                  # FileSink (JSONL)
    solr_sink.py                  # SolrSink (버퍼링 + circuit breaker)

  repository/
    db.py                         # SSH 터널(옵션) + SQLAlchemy 엔진 context manager
    crawl_url_repo.py             # claim_next / mark_stored / mark_failed / mark_dead / recover_timed_out
    domain_repo.py                # t_domain 조회 + upsert_health + set_cooldown
    crawl_runtime_repo.py         # t_crawl_runtime 조회 (Solr 접속 정보)
    collection_log_repo.py        # 배치 통계 로그 적재

  worker/
    extraction_worker.py          # run_extraction_loop() — 메인 루프, _flush_pending, _process_one
    reaper.py                     # run_reaper() — daemon 스레드, 점유 회수
    _healthcheck.py               # /tmp/healthcheck 파일 갱신 (Docker HEALTHCHECK 용)
```

---

## 14. 설정 키 전체 목록

| 키 | 기본값 | 설명 |
|---|---|---|
| `RDS_HOST` / `RDS_PORT` / `RDS_USER` / `RDS_PASSWORD` / `RDS_DB` | (필수, 포트 3306) | MySQL 접속 정보 |
| `TUNNEL_ENABLED` | `false` | SSH 터널 사용 여부 |
| `TUNNEL_SSH_HOST` / `TUNNEL_SSH_PORT` / `TUNNEL_SSH_USER` / `TUNNEL_SSH_KEY_PATH` / `TUNNEL_LOCAL_PORT` | (터널 활성 시 필수) | SSH 터널 설정 |
| `WORKER_ID` | `worker-1` | 워커 식별자 (CLI `--worker-id` 로 override 가능) |
| `DEFAULT_CRAWL_DELAY_MS` | `1000` | 도메인별 설정 없을 때 기본 크롤 딜레이 |
| `HTTP_VERIFY_SSL` | `true` | SSL 검증 여부 (사내 자체서명 인증서 환경은 false) |
| `SINK_TYPE` | `file` | `file` \| `solr` |
| `FILE_SINK_DIR` | `./output` | FileSink 저장 디렉토리 |
| `LOG_DIR` | `./logs` | 로그 디렉토리 |
| `SOLR_DIRECT_ENABLED` | `false` | true면 `SOLR_URL` 직접 사용, false면 `t_crawl_runtime` 조회 |
| `SOLR_URL` | — | 직접 모드일 때 Solr 코어 URL |
| `SOLR_RUNTIME_NAME` | — | `t_crawl_runtime.runtime_name` (DB 조회 모드 필수) |
| `SOLR_CRAWLER_TYPE` | — | 직접 모드일 때 crawler_type |
| `SOLR_BATCH_SIZE` | `100` | flush 트리거 배치 크기 |
| `SOLR_COMMIT_WITHIN_MS` | `5000` | Solr commitWithin |
| `SOLR_CONNECT_TIMEOUT_S` | `5` | TCP 연결 타임아웃 |
| `SOLR_READ_TIMEOUT_S` | `30` | 응답 수신 타임아웃 |
| `MASKING_ENABLED` | `true` | 본문/저자 마스킹 여부 |
| `MAX_ATTEMPTS` | `5` | 초과 시 `dead` 처리 |
| `BACKOFF_BASE_SECONDS` | `30` | 백오프 기준값 |
| `BACKOFF_MAX_SECONDS` | `3600` | 백오프 상한 |
| `CLAIM_TIMEOUT_SECONDS` | `300` | Reaper 가 회수 판단하는 점유 시간 |
| `RULES_CACHE_TTL_SECONDS` | `60` | RuleEngine 도메인 규칙 캐시 TTL |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |
| `LOG_ROTATION` | `daily` | `daily` \| `size` |
| `LOG_RETAIN_DAYS` | `30` | daily 모드 보관 일수 |
| `LOG_BACKUP_COUNT` | `10` | size 모드 보관 파일 수 |
| `HEARTBEAT_INTERVAL_SECONDS` | `60` | 하트비트 + pending flush 주기 |

(환경변수 이름은 `app/config.py` 실제 정의 기준.)

---

## 15. 배포

### 15.1 Docker 이미지

Playwright(headless 렌더링)와 trafilatura/lxml 등 무거운 파싱 라이브러리를 쓰므로
discovery-worker 와 동일하게 `playwright/python` 계열 이미지를 사용한다(경량 이미지인
rescrape-dispatcher와 다름). `./deploy/build.sh` 로 빌드.

### 15.2 실행 예

```bash
# 워커 시작 (전체 소스)
./deploy/run.sh extr-1

# 특정 소스만 처리 (다중 인스턴스로 소스별 분산 가능)
./deploy/run.sh extr-naver NAVER_NEWS
```

Reaper 는 워커 프로세스 내부 daemon 스레드로 자동 기동되므로 별도 컨테이너/커맨드가
필요 없다.

### 15.3 멀티 인스턴스

`claim_next()` 의 낙관적 클레임(조건부 UPDATE + rowcount 확인) 덕분에 동일 소스를 처리하는
여러 인스턴스를 동시에 띄워도 같은 URL 을 중복 처리하지 않는다. `--source` 로 소스별
전담 인스턴스를 분리하면 특정 소스가 밀려도 다른 소스 처리에 영향 없음.

---

## 16. 관측성 / 로깅

### 16.1 로그 파일

`{LOG_DIR}/extraction-{worker_id}.log` (INFO 이상), `-error.log` (WARNING 이상).

### 16.2 주요 로그 phase

| phase | 의미 |
|---|---|
| `startup` | 워커/리퍼 기동 |
| `heartbeat` | 주기적 통계 + pending flush + healthcheck 파일 갱신 |
| `claim` | `claim_next()` 실패 (DB 오류) |
| `extract` | fetch/extract/sink 처리 중 |
| `idle` | 처리할 URL 없음 |
| `sink_flush_error` | `sink.flush()` 실패 → 해당 배치 전체 `failed_transient` |
| `claim_lost` | flush는 성공했지만 소유권을 잃어 `mark_stored` 스킵 (Solr 엔 반영됨, DB 갱신만 무시) |
| `reap` (reaper) | 타임아웃 회수 실행 |
| `shutdown` | SIGTERM/SIGINT 수신 |

### 16.3 Docker HEALTHCHECK

`/tmp/healthcheck` 파일의 mtime 이 `HEARTBEAT_INTERVAL_SECONDS` 의 2배(약 120초) 이내인지
확인. heartbeat 주기마다 `_healthcheck.write()` 로 갱신.

---

## 17. discovery-worker / rescrape-dispatcher 와의 차이점

| 항목 | discovery-worker | extraction-worker | rescrape-dispatcher |
|---|---|---|---|
| 역할 | URL 발견 | 본문 추출 | 신규 URL 투입만 |
| 입력 | 포털 검색 결과 (스크래핑) | t_crawl_url | Solr (HTTP JSON API) |
| 출력 | t_crawl_url | t_crawl_url + (Solr \| 파일) | t_crawl_url 만 |
| 베이스 이미지 | playwright/python | playwright/python | python:3.12-slim |
| 주요 의존성 | httpx, undetected-chromedriver | httpx, Playwright, trafilatura, lxml, selectolax | SQLAlchemy, httpx |
| 동시성 안전장치 | WORKER_ID별 flock 프로필 락 | claim_next 낙관적 클레임 + claim 소유권 검증 | INSERT IGNORE 멱등성 |
| 백그라운드 스레드 | healthcheck 스레드 | reaper 스레드 + healthcheck | healthcheck (heartbeat) |

---

## 18. 범위 밖

- URL 발견(검색/스크래핑) — discovery-worker 가 처리
- Solr 재조회 기반 신규 URL 투입 — rescrape-dispatcher 가 처리
- `t_crawl_url`/`t_domain` 스키마 변경 — discovery-worker alembic 마이그레이션으로 관리
- 도메인 규칙(`rules_json`) 작성 방법 — [domain-rule-guide.md](domain-rule-guide.md) 참고
