# extraction-worker

크롤러 파이프라인(discovery-worker / rescrape-dispatcher / crawler-admin과 MySQL을
공유)에서, 이미 발견된 URL을 가져와 본문(제목/본문/작성자/게시일)을 추출하고
파일 또는 Solr에 저장하는 백그라운드 워커다.

```
discovery-worker / rescrape-dispatcher → t_crawl_url (status=discovered)
                                              │
                                              ▼
                  extraction-worker: claim_next() → fetch → extract → sink
                                              │
                                              ▼
                    stored / failed_transient / failed_permanent / dead
```

- `t_crawl_url`에서 조건부 UPDATE(`claim_next`)로 행을 선점하여 여러 인스턴스를
  동시에 띄워도 중복 처리되지 않는다.
- 도메인 설정(`t_domain.render_mode`)에 따라 정적(httpx) 또는 헤드리스(Playwright)로 페이지를 가져온다.
- 도메인별 규칙(`t_domain.rules_json`: CSS/XPath/JSON-API/AMP/Next.js) 우선 적용,
  없으면 `trafilatura` → `readability-lxml` 순으로 폴백.
- `masking_list.json` 기준으로 본문/작성자 PII 마스킹 후 파일(JSONL) 또는 Solr에 저장.
- 처리 결과에 따라 `t_crawl_url` 상태 갱신(재시도/백오프 포함), 클레임 타임아웃된
  URL은 백그라운드 Reaper 스레드가 회수.
- 범위 밖: URL 신규 발견, Solr 기반 재발견, DB 스키마/마이그레이션(discovery-worker 소관).

자세한 설계는 [docs/extraction-worker-design.md](docs/extraction-worker-design.md),
도메인 규칙 작성법은 [docs/domain-rule-guide.md](docs/domain-rule-guide.md),
운영 커맨드는 [docs/ops-commands.md](docs/ops-commands.md) 참고.

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium   # 헤드리스 렌더링용 브라우저 바이너리
```

## 실행 방법

### 로컬

```bash
APP_ENV=local python -m app --worker-id local-extr
APP_ENV=local python -m app --source NAVER_NEWS --worker-id local-extr
```

시작 시 `config.validate()`가 필수 환경변수 누락을 검사하고, 실패하면 목록을
출력하며 종료(exit 1)한다. 정상 기동 시 Reaper를 데몬 스레드로 시작하고
`SIGTERM`/`SIGINT`까지 메인 추출 루프를 실행한다.

### CLI 인자

| 인자 | 설명 | 값 범위 | 기본값 |
|---|---|---|---|
| `--source` | 처리할 소스 하나만 지정 (여러 인스턴스를 소스별로 분리 실행 가능) | `NAVER_NEWS` \| `DAUM_NEWS` \| `GOOGLE_NEWS` \| `BAIDU_NEWS` \| `NAVER_STOCK` \| `DUCKDUCKGO_NEWS`(운영상 비활성) \| `all` | `all` |
| `--worker-id` | DB 클레임 소유권/로그 파일명에 쓰이는 워커 식별자 (인스턴스마다 고유해야 함) | 문자열 | env `WORKER_ID` (기본 `worker-1`) |

> `DUCKDUCKGO_NEWS`는 어댑터/소스타입이 코드에 남아있지만 현재 실제 운영 대상 키워드는 없다(비활성). `BAIDU_NEWS`는 활성 대상이다.

### Docker

```bash
./deploy/build.sh            # extraction-worker:latest 빌드
./deploy/build.sh v1.0.0     # 버전 태그 지정 (선택)

./deploy/run.sh extr-1                 # 모든 소스 처리
./deploy/run.sh extr-naver NAVER_NEWS  # 특정 소스만 처리
```

`deploy/run.sh <worker_id> [source]` 형태이며 내부적으로
`docker run --detach --name <worker_id> --user "$(id -u):$(id -g)" --restart unless-stopped
--env-file .env.${APP_ENV:-dev} -e APP_ENV=... -e WORKER_ID=... extraction-worker:latest
python -m app --source "${SOURCE}"`를 실행하고 `~/apps/data/extraction-worker/{logs,output}`를
볼륨 마운트한다(동일 이름 컨테이너가 있으면 먼저 `docker rm -f`).

베이스 이미지는 `mcr.microsoft.com/playwright/python:v1.59.0-noble` — `requirements.txt`의
`playwright` 버전과 반드시 일치해야 헤드리스 페치가 정상 동작한다.

## 환경 변수

`.env`(공통 기본값) 로드 후 `.env.{APP_ENV}` 로 override (`APP_ENV` 기본값 `local`).

**필수 (미설정 시 기동 실패, `config._REQUIRED_ALWAYS` 기준)**

| 변수 | 설명 | 예시 |
|---|---|---|
| `RDS_HOST` | MySQL 호스트 | `your-rds-host.rds.amazonaws.com` |
| `RDS_USER` | MySQL 사용자 | - |
| `RDS_PASSWORD` | MySQL 비밀번호 | - |
| `RDS_DB` | 스키마 이름 | `crawlerdb` |

`RDS_PORT`는 기본값(`3306`)이 있어 미설정해도 기동에 실패하지 않는다(아래 표 참고) —
위 4개만 실제로 `config.validate()`가 검증하는 필수값이다.

**SSH 터널** (로컬 개발용)

| 변수 | 설명 | 값 범위 / 기본값 |
|---|---|---|
| `TUNNEL_ENABLED` | 터널 사용 여부 | bool, 기본 `false` |
| `RDS_PORT` | MySQL 포트 | 정수, 기본 `3306` |
| `TUNNEL_SSH_HOST` | bastion 호스트 (`TUNNEL_ENABLED=true`일 때 **필수**) | - |
| `TUNNEL_SSH_PORT` | SSH 포트 (기본값 있어 필수 아님) | 기본 `22` |
| `TUNNEL_SSH_USER` | SSH 사용자 (기본값 있어 필수 아님) | 기본 `ubuntu` |
| `TUNNEL_SSH_KEY_PATH` | 개인키 경로 (`TUNNEL_ENABLED=true`일 때 **필수**) | PEM 파일 경로 |
| `TUNNEL_LOCAL_PORT` | 터널 로컬 포트 (기본값 있어 필수 아님) | 기본 `13306` |

**워커**

| 변수 | 설명 | 값 범위 / 기본값 |
|---|---|---|
| `WORKER_ID` | 워커 식별자 (`--worker-id`로 override 가능, 인스턴스별 고유) | 문자열, 기본 `worker-1` |

**페처**

| 변수 | 설명 | 값 범위 / 기본값 |
|---|---|---|
| `DEFAULT_CRAWL_DELAY_MS` | `t_domain` 오버라이드 없을 때 기본 호스트별 크롤 딜레이 | 정수(ms), 기본 `1000` |
| `HTTP_VERIFY_SSL` | SSL 검증 여부 (사내 프록시 등에서 `false`) | bool, 기본 `true` |

**싱크**

| 변수 | 설명 | 값 범위 / 기본값 |
|---|---|---|
| `SINK_TYPE` | 저장소 종류 | `file` \| `solr`, 기본 `file` |
| `FILE_SINK_DIR` | JSONL 출력 디렉토리 (`{dir}/{date}/{crawler_type}-{worker_id}.jsonl`) | 경로, 기본 `./output` |
| `LOG_DIR` | 로그 디렉토리 | 경로, 기본 `./logs` |

**Solr** (`SINK_TYPE=solr`일 때만 사용)

| 변수 | 설명 | 값 범위 / 기본값 |
|---|---|---|
| `SOLR_DIRECT_ENABLED` | `true`: `SOLR_URL` 직접 사용 / `false`: `t_crawl_runtime` 조회 | bool, 기본 `false` |
| `SOLR_URL` | Solr 코어 URL (direct 모드) | `http://localhost:8983/solr/<core>` |
| `SOLR_RUNTIME_NAME` | `t_crawl_runtime.runtime_name` (DB 조회 모드, direct 아니면 필수) | - |
| `SOLR_CRAWLER_TYPE` | crawler_type (direct 모드에서 사용) | - |
| `SOLR_BATCH_SIZE` | 배치 플러시 크기 | 정수, 기본 `100` |
| `SOLR_COMMIT_WITHIN_MS` | 커밋 주기 | 정수(ms), 기본 `5000` |
| `SOLR_CONNECT_TIMEOUT_S` | 연결 타임아웃 | 정수(s), 기본 `5` |
| `SOLR_READ_TIMEOUT_S` | 읽기 타임아웃 | 정수(s), 기본 `30` |

**마스킹 / 재시도 / 캐시 / 로깅**

| 변수 | 설명 | 값 범위 / 기본값 |
|---|---|---|
| `MASKING_ENABLED` | `masking_list.json` 기준 PII 마스킹 여부 | bool, 기본 `true` |
| `MAX_ATTEMPTS` | `dead` 처리 전 최대 시도 횟수 | 정수, 기본 `5` |
| `BACKOFF_BASE_SECONDS` | 백오프 기준값 (`min(base*2^attempt, max)+jitter`) | 정수, 기본 `30` |
| `BACKOFF_MAX_SECONDS` | 백오프 최대값 | 정수, 기본 `3600` |
| `CLAIM_TIMEOUT_SECONDS` | Reaper가 클레임을 회수하는 기준 시간 | 정수, 기본 `300` |
| `RULES_CACHE_TTL_SECONDS` | `t_domain` 규칙 캐시 TTL | 정수, 기본 `60` |
| `LOG_LEVEL` | 로그 레벨 | `DEBUG`\|`INFO`\|`WARNING`\|`ERROR`\|`CRITICAL`, 기본 `INFO` |
| `LOG_ROTATION` | 로테이션 방식 | `daily`\|`size`, 기본 `daily` |
| `LOG_RETAIN_DAYS` | 보관 일수 (`daily` 모드) | 정수, 기본 `30` |
| `LOG_BACKUP_COUNT` | 보관 파일 개수 (`size` 모드) | 정수, 기본 `10` |
| `HEARTBEAT_INTERVAL_SECONDS` | 하트비트/헬스체크 파일 갱신 주기 | 정수(s), 기본 `60` |

## 유틸리티 스크립트 (`scripts/`)

저장소 루트에서(venv 활성화 후) 실행한다.

| 스크립트 | 용도 | 인자 예시 |
|---|---|---|
| `fetch_html.py` | URL의 원본 HTML을 가져와 iframe/본문 후보 요소를 출력 (규칙 작성 보조) | `--url "https://example.com" [--headless] [--save /path/out.html]` |
| `run_extraction.py` | 단일 URL 또는 DB에서 claim한 행 하나로 추출 파이프라인 수동 실행 | `--url "https://..." --dry-run` \| `--source NAVER_NEWS` \| 인자 없이 실행 시 DB에서 1건 claim |
| `healthcheck.py` | DB/Solr/`t_crawl_runtime` 연결 점검 | `[--db] [--solr] [--runtime [RUNTIME_NAME]]` (인자 없으면 전체 점검) |
| `seed_domain_rules.py` | `t_domain`에 하드코딩된 규칙 목록을 upsert | 인자 없음 |
| `test_solr_sink.py` | 더미 문서 3건을 Solr에 색인 후 검증 | 인자 없음(env 모드) \| `--rdb`(DB 조회 모드) |

## 주요 라이브러리

`SQLAlchemy`/`PyMySQL`(DB), `sshtunnel`/`paramiko`(SSH 터널), `httpx`/`playwright`(페치),
`trafilatura`/`readability-lxml`/`lxml`/`selectolax`(본문 추출), `python-dotenv`(설정).

## masking_list.json

전화번호/이메일 정규식 기반 마스킹 규칙 정의 파일(카드번호/주민번호 패턴은 없음). 각 항목은
`label`(이름), `mask_str`(정규식), `replace_str`(치환 문자열), `use_yn`(`Y`인 것만 적용)
로 구성된다. 기자명/특파원명 마스킹은 이 파일이 아니라 `app/domain_logic/masking.py`
내 Python 내장 패턴으로 처리된다.
