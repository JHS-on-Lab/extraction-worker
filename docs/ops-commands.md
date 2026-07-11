# extraction-worker 운영 명령어

## 빌드 & 실행

```bash
# 이미지 빌드
./deploy/build.sh

# 워커 시작 (전체 소스)
./deploy/run.sh extr-1

# 특정 소스만 처리
./deploy/run.sh extr-naver NAVER_NEWS
```

## 모니터링

```bash
# 실시간 로그
docker logs -f extr-1

# 상태 확인
docker ps | grep extr

# 컨테이너 중지
docker stop extr-1
```

## 개발 환경 실행

```bash
# 로컬에서 직접 실행
APP_ENV=local python -m app --worker-id local-extr

# 특정 소스만
APP_ENV=local python -m app --source NAVER_NEWS --worker-id local-extr
```

## 유틸리티 스크립트

```bash
# 연결 상태 확인 (DB + Solr + t_crawl_runtime 전체)
python scripts/healthcheck.py
python scripts/healthcheck.py --db        # DB만
python scripts/healthcheck.py --solr      # Solr만
python scripts/healthcheck.py --runtime   # t_crawl_runtime 전체 목록

# 특정 URL HTML 직접 fetch 테스트 (iframe 내부까지 출력)
python scripts/fetch_html.py --url "https://example.com"
python scripts/fetch_html.py --url "https://example.com" --headless --save /tmp/out.html

# 추출(Extraction) 단계 수동 실행
python scripts/run_extraction.py --url "https://..." --dry-run   # 저장 없이 결과만
python scripts/run_extraction.py --url "https://..." --source NAVER_STOCK --keyword 000660
python scripts/run_extraction.py                                  # DB 에서 discovered URL 하나 꺼내 추출
python scripts/run_extraction.py --source NAVER_NEWS              # 특정 소스만

# domain 규칙 시드 (테이블 초기화 후 재시딩)
python scripts/seed_domain_rules.py

# Solr sink 연결 테스트 (더미 데이터 투입 후 인덱싱 확인)
python scripts/test_solr_sink.py
python scripts/test_solr_sink.py --rdb    # t_crawl_runtime 에서 설정 조회
```
