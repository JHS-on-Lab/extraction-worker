# Commit Log

이 저장소에 커밋될 때마다 커밋ID·날짜·메시지·수정된 파일 목록을 기록한다.
최신 항목이 맨 위로 오도록 앞에 추가한다.

> 주의: 커밋 자신의 해시는 그 커밋 내용(트리)을 해시한 결과라서, 같은 커밋 안에
> 자기 자신의 해시를 담을 수 없다(자기 참조 불가). 그래서 이 파일은 매 커밋을
> "즉시" 기록하는 게 아니라, **다음 커밋을 만들 때 직전 커밋의 항목을 함께
> 기록**하는 방식으로 갱신한다 — 커밋 수를 늘리지 않으면서 정확한 해시를 남기기
> 위한 절충이다. 따라서 가장 최근 커밋 하나는 그다음 커밋이 생기기 전까지 이
> 목록에 아직 나타나지 않을 수 있다.

---

## 69393ff — 2026-08-06
--source 화이트리스트 검증을 제거하고 형식 검사만 남김

- app/__main__.py
- deploy/run.sh

## ef07217 — 2026-08-06
fix: build.sh 의 APP_UID/APP_GID 를 배포 계정 실값(1001)으로 재고정

- Dockerfile
- deploy/build.sh
- deploy/run.sh
- docs/commit-log.md

## 73febad — 2026-08-06
fix: build.sh/run.sh UID 처리를 동적 방식으로 되돌리고 run.sh --user 제거

- Dockerfile
- deploy/build.sh
- deploy/run.sh
- docs/commit-log.md
- docs/extraction-worker-design.md

## 508c109 — 2026-08-06
fix: build.sh 의 APP_UID/APP_GID 를 1001로 고정 (run.sh --user 값과 통일)

- Dockerfile
- deploy/build.sh
- docs/commit-log.md

## 035aee5 — 2026-08-06
run.sh 데이터 경로를 HOME 대신 고정 경로(DATA_ROOT)로 변경

- deploy/run.sh

## 99f4e10 — 2026-08-06
run.sh에 source 인자 띄어쓰기 방어 로직 추가

- deploy/run.sh

## f1f3915 — 2026-08-06
run.sh에서 컨테이너 메모리 제한 제거

- deploy/run.sh

## 0e12466 — 2026-08-06
Docker 이미지 타임존을 서울(KST)로 설정하고 build-arg로 호스트 UID/GID 전달

- Dockerfile
- deploy/build.sh

## b6b9056 — 2026-08-06
fix: rule_engine amp_url/json_api 페치 실패 분류 및 스크립트 중복 정리

- app/extraction/rule_engine.py
- app/repository/crawl_url_repo.py
- docs/commit-log.md
- scripts/run_extraction.py

## 6b3748e — 2026-08-06
docs: 주석/설계문서에서 날짜·이전 구현 비교 서술 제거

- app/fetch/http_client.py
- docs/commit-log.md
- docs/extraction-worker-design.md
- scripts/seed_domain_rules.py

## e4d6f7c — 2026-08-04
chore: 안 쓰는 sink/base.py, fetch/proxy.py 제거 + 문서 최신화

- README.md
- app/fetch/proxy.py
- app/sink/base.py
- docs/commit-log.md
- docs/extraction-worker-design.md

## 7a760a0 — 2026-08-04
feat: TINHTE_FORUM 소스 추가 반영

- README.md
- app/__main__.py
- app/types.py
- deploy/run.sh
- docs/commit-log.md

## 6450237 — 2026-08-04
chore: docker run --user 를 1001:1001 로 고정

- deploy/run.sh
- docs/commit-log.md

## badf634 — 2026-08-03
fix: --source 필터에 BAOMOI_NEWS 누락되어 있던 문제 수정

- app/__main__.py
- app/types.py
- deploy/run.sh
- docs/commit-log.md

## d3dd915 — 2026-07-27
fix: 저자명 마스킹 해제, 기자명/특파원명 마스킹을 JSON use_yn 으로 실제 제어

- app/domain_logic/masking.py
- app/sink/serialize.py
- masking_list.json

## 85737c9 — 2026-07-17
docs: 커밋 로그 트래킹 파일 추가 (docs/commit-log.md)

- docs/commit-log.md

## 8100cde — 2026-07-17
feat: --source 콤마 구분 복수 소스 지정 지원

- README.md
- app/__main__.py
- app/repository/crawl_url_repo.py
- app/worker/extraction_worker.py
