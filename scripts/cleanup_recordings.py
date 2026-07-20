"""录像留存清理 —— Sprint 6-5 (PIPL 留存策略的执行端)。

用法 (建议 cron 每天跑一次):
    python -m scripts.cleanup_recordings              # 默认 90 天
    python -m scripts.cleanup_recordings --days 30    # 自定义留存期
    python -m scripts.cleanup_recordings --dry-run    # 只列出, 不删

留存期约定三处同步:
- 这里的默认值 (src.media_store.DEFAULT_RETENTION_DAYS)
- 前端 consent 文案 (web session/media.tsx RETENTION_DAYS)
- 招聘方对候选人的告知口径
改任何一处都要同步其余两处。
"""
from __future__ import annotations

import argparse

try:  # .env 便利加载; 生产 cron 直接注入环境变量也行
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src import media_store


def main() -> None:
    ap = argparse.ArgumentParser(description="删除超过留存期的面试录像")
    ap.add_argument(
        "--days", type=int, default=media_store.DEFAULT_RETENTION_DAYS,
        help=f"留存天数 (默认 {media_store.DEFAULT_RETENTION_DAYS})",
    )
    ap.add_argument("--dry-run", action="store_true", help="只列出将删除的文件")
    args = ap.parse_args()

    if not media_store.is_configured():
        print("MEDIA_STORAGE_DIR 未配置, 无录像可清理")
        return

    if args.dry_run:
        import time
        from pathlib import Path
        import os
        cutoff = time.time() - args.days * 86400
        root = Path(os.environ["MEDIA_STORAGE_DIR"])
        stale = [
            p.name for p in root.glob("*.webm")
            if p.stat().st_mtime < cutoff
        ]
        print(f"[dry-run] {len(stale)} 个录像超过 {args.days} 天:")
        for name in stale:
            print(f"  - {name}")
        return

    removed = media_store.purge_older_than(args.days)
    print(f"已清理 {len(removed)} 个超过 {args.days} 天的录像")
    for name in removed:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
