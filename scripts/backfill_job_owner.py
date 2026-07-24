"""存量 job 归属迁移 —— Sprint 6.8 task 2。

    python -m scripts.backfill_job_owner --username <HR用户名> [--dry-run]

把 owner_user_id IS NULL 的存量岗位归到指定 HR 账号。
隔离规则下 NULL 归属仅 admin 可见, 跑完本脚本消灭模糊态。
"""
from __future__ import annotations

import argparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import text

from src.db import load_user_by_username
from src.db.base import get_engine


def main() -> None:
    ap = argparse.ArgumentParser(description="存量 job 归属迁移")
    ap.add_argument("--username", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    found = load_user_by_username(args.username)
    if found is None:
        raise SystemExit(f"用户 {args.username!r} 不存在")
    uid = found[0].user_id

    eng = get_engine()
    with eng.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM jobs WHERE owner_user_id IS NULL"
        )).scalar()
        print(f"无主岗位: {n} 个 -> 归属 {args.username} ({uid[:8]}...)")
        if args.dry_run or not n:
            print("dry-run / 无需迁移, 结束")
            return
        c.execute(text(
            "UPDATE jobs SET owner_user_id = :uid WHERE owner_user_id IS NULL"
        ), {"uid": uid})
    print("迁移完成")


if __name__ == "__main__":
    main()
