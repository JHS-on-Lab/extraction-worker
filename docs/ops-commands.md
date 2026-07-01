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
# 특정 URL HTML 직접 fetch 테스트
python scripts/fetch_html.py https://example.com

# Solr sink 연결 테스트
python scripts/test_solr_sink.py

# 테이블 truncate (주의)
python scripts/truncate_table.py t_crawl_url
```
