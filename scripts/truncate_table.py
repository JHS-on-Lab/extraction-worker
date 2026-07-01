"""
테이블 데이터 삭제 스크립트.

실행:
  python scripts/truncate_table.py --table t_crawl_url
  python scripts/truncate_table.py --table t_collection_log
  python scripts/truncate_table.py --table t_domain
  python scripts/truncate_table.py --table t_keyword
  python scripts/truncate_table.py --all
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.repository.db import db_context

# keyword 는 crawl_url 의 FK 참조 대상이므로 crawl_url 먼저 비워야 한다.
_ALLOWED_TABLES = {"t_keyword", "t_crawl_url", "t_domain", "t_collection_log"}
_ALL_ORDER = ["t_collection_log", "t_crawl_url", "t_keyword", "t_domain"]


def _truncate(engine, tables: list[str]) -> None:
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for t in tables:
            conn.execute(text(f"TRUNCATE TABLE `{t}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))


def main() -> None:
    p = argparse.ArgumentParser()
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--table", choices=sorted(_ALLOWED_TABLES),
                       help="비울 테이블 이름")
    group.add_argument("--all", action="store_true",
                       help="전체 테이블 한번에 비우기")
    args = p.parse_args()

    tables = _ALL_ORDER if args.all else [args.table]

    with db_context() as engine:
        with engine.connect() as conn:
            for t in tables:
                count = conn.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()
                print(f"  {t}: {count:,} 행")

    print()
    confirm = input("정말 삭제하시겠습니까? (yes 입력): ").strip()
    if confirm != "yes":
        print("취소됨.")
        return

    with db_context() as engine:
        _truncate(engine, tables)

    for t in tables:
        print(f"  [완료] {t}")


if __name__ == "__main__":
    main()
