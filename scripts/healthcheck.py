"""
연결 상태 확인 스크립트.

실행:
  python scripts/healthcheck.py           # DB + Solr + crawl_runtime 전체 확인
  python scripts/healthcheck.py --db      # DB만
  python scripts/healthcheck.py --solr    # Solr만
  python scripts/healthcheck.py --runtime              # t_crawl_runtime 전체 목록
  python scripts/healthcheck.py --runtime my_runtime   # 특정 runtime_name 조회
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import httpx
from sqlalchemy import text

from app import config
from app.repository.db import db_context


def check_db() -> bool:
    print("[ DB ]")
    try:
        with db_context() as engine:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT VERSION(), DATABASE()")).fetchone()
        print(f"  MySQL 버전 : {row[0]}")
        print(f"  현재 DB   : {row[1]}")
        print("  → OK\n")
        return True
    except Exception as e:
        print(f"  → 실패: {e}\n")
        return False


def check_solr() -> bool:
    print("[ Solr ]")
    try:
        solr_url = _resolve_solr_url().rstrip("/")
        print(f"  URL : {solr_url}")

        resp = httpx.get(f"{solr_url}/admin/ping", params={"wt": "json"}, timeout=5)
        resp.raise_for_status()

        resp = httpx.get(f"{solr_url}/select", params={"q": "*:*", "rows": "0", "wt": "json"}, timeout=5)
        resp.raise_for_status()
        num_found = resp.json().get("response", {}).get("numFound", 0)
        print(f"  문서 수 : {num_found:,}건")
        print("  → OK\n")
        return True
    except Exception as e:
        print(f"  → 실패: {e}\n")
        return False


def check_runtime(runtime_name: str | None) -> bool:
    print("[ t_crawl_runtime ]")
    try:
        with db_context() as engine:
            if runtime_name:
                from app.repository.crawl_runtime_repo import CrawlRuntimeRepo
                info = CrawlRuntimeRepo(engine).get_runtime(runtime_name)
                if info:
                    print(f"  runtime_name : {runtime_name}")
                    print(f"  solr_url     : {info.solr_url}")
                    print("  → OK\n")
                    return True
                else:
                    print(f"  → 없음: runtime_name='{runtime_name}' 이 없거나 use_yn='N'\n")
                    return False
            else:
                with engine.connect() as conn:
                    rows = conn.execute(
                        text("SELECT runtime_name, crawler_type, solr_url, use_yn FROM t_crawl_runtime ORDER BY runtime_name")
                    ).fetchall()
                if not rows:
                    print("  (비어 있음)\n")
                    return True
                print(f"  {'runtime_name':<30} {'crawler_type':<20} {'use_yn':<6} solr_url")
                print("  " + "-" * 96)
                for row in rows:
                    print(f"  {row[0]:<30} {(row[1] or ''):<20} {row[3]:<6} {row[2] or ''}")
                print()
                return True
    except Exception as e:
        print(f"  → 실패: {e}\n")
        return False


def _resolve_solr_url() -> str:
    if config.SOLR_DIRECT_ENABLED:
        if not config.SOLR_URL:
            raise RuntimeError("SOLR_DIRECT_ENABLED=true 이지만 SOLR_URL 이 설정되지 않았습니다.")
        return config.SOLR_URL

    if not config.SOLR_RUNTIME_NAME:
        raise RuntimeError("SOLR_RUNTIME_NAME 을 .env 에 설정하세요.")

    from app.repository.crawl_runtime_repo import CrawlRuntimeRepo
    with db_context() as engine:
        info = CrawlRuntimeRepo(engine).get_runtime(config.SOLR_RUNTIME_NAME)
    if not info:
        raise RuntimeError(f"t_crawl_runtime 에서 '{config.SOLR_RUNTIME_NAME}' 을 찾을 수 없거나 use_yn='N'")
    return info.solr_url


def main() -> None:
    p = argparse.ArgumentParser(description="연결 상태 확인")
    p.add_argument("--db",      action="store_true", help="DB 연결 확인")
    p.add_argument("--solr",    action="store_true", help="Solr 연결 확인")
    p.add_argument("--runtime", nargs="?", const="",  metavar="RUNTIME_NAME",
                   help="t_crawl_runtime 조회 (인수 없으면 전체 목록)")
    args = p.parse_args()

    # 플래그 없으면 전체 실행
    run_all = not (args.db or args.solr or args.runtime is not None)

    results = []
    if run_all or args.db:
        results.append(check_db())
    if run_all or args.solr:
        results.append(check_solr())
    if run_all or args.runtime is not None:
        results.append(check_runtime(args.runtime or None))

    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
