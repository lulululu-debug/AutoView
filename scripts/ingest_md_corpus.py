"""通用 md 知识库入库脚本 —— Sprint A; lib 抽到 src/knowledge_pipeline/parser.py
(Sprint upload 重构), 让 CLI 和 HTTP upload endpoint 复用同一组函数。

把任意 md 仓库切片入 knowledge_chunks 表, 只动 Postgres, 不入 Milvus,
不调 LLM (反向出题在 Sprint B / derive_questions.py)。

用法 (smoke test docs/java/basis):
    git clone --depth 1 --sparse https://github.com/Snailclimb/JavaGuide /tmp/jg
    cd /tmp/jg && git sparse-checkout set docs/java && cd -

    python -m scripts.ingest_md_corpus \\
        --source-name javaguide \\
        --commit 85a4170c100309ca34833d5f0bfe5b88b08d5f7a \\
        --root /tmp/jg/docs \\
        --include 'java/basis/**/*.md' \\
        --dataset-id javaguide-basis-smoke \\
        --topic "JAVA 基础" \\
        --category knowledge \\
        --truncate

设计取舍 (探查 docs/java/basis 后定):
- 切到 H3 叶子标题; H4 内容算进所属 H3 chunk
- 不主动 merge / split: 偏小 / 偏大都靠 quality_tag 打标 (low_value / oversize)
- 零新依赖: 自己 parse frontmatter 与 heading, 跳过 ``` 代码块
- 单文件一事务: 中断可恢复, 已入库的 chunk_id 重跑被 merge 覆盖
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path, PurePosixPath

from src.db.base import init_db
from src.db.repository import (
    count_knowledge_chunks,
    delete_knowledge_chunks_for_dataset,
    upsert_dataset,
    upsert_knowledge_chunks,
)
from src.knowledge_pipeline.parser import build_chunks
from src.schemas import Dataset

log = logging.getLogger("ingest_md_corpus")


_VALID_CATEGORIES = ("knowledge", "scenario")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-name", required=True, help="e.g. javaguide")
    parser.add_argument("--commit", required=True, help="git commit sha (固定可复现)")
    parser.add_argument("--root", required=True, type=Path,
                        help="md 根目录 (相对路径从这里算)")
    parser.add_argument("--include", default="**/*.md",
                        help="glob, 默认 **/*.md")
    parser.add_argument("--dataset-id", required=True,
                        help="数据集隔离标识, Planner 召回可按此过滤")
    parser.add_argument("--topic", required=True,
                        help='主题词 (中文 ok), 例如 "JAVA 基础" / "前端 React"')
    parser.add_argument("--category", default="knowledge", choices=_VALID_CATEGORIES,
                        help="dataset 级 category, 决定 derive 用 knowledge/scenario "
                             "prompt + SeedQuestion 落库 category (默认 knowledge)")
    parser.add_argument("--description", default="",
                        help="数据集详情/备注 (可选)")
    parser.add_argument("--truncate", action="store_true",
                        help="入库前先清空本 dataset_id 的所有 chunk")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    root = args.root.resolve()
    if not root.exists() or not root.is_dir():
        log.error("root not found or not a dir: %s", root)
        return 2

    init_db()

    upsert_dataset(Dataset(
        dataset_id=args.dataset_id,
        topic=args.topic,
        description=args.description,
        source_repo=args.source_name,
        source_commit=args.commit,
        category=args.category,
    ))
    log.info("upsert dataset: %s (topic=%s category=%s)",
             args.dataset_id, args.topic, args.category)

    if args.truncate:
        deleted = delete_knowledge_chunks_for_dataset(args.dataset_id)
        log.info("truncated %d rows for dataset_id=%s", deleted, args.dataset_id)

    md_files = sorted(
        p for p in root.glob(args.include)
        if p.is_file() and p.suffix.lower() == ".md"
    )
    log.info("discovered %d md files under %s", len(md_files), root)

    total_chunks = 0
    tag_counts: dict[str, int] = {}
    files_skipped = 0

    for md in md_files:
        rel = PurePosixPath(md.relative_to(root).as_posix())
        try:
            raw = md.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            log.warning("  skip (encoding): %s", rel)
            files_skipped += 1
            continue

        chunks = build_chunks(
            rel, raw,
            source_name=args.source_name,
            commit=args.commit,
            dataset_id=args.dataset_id,
        )
        n = upsert_knowledge_chunks(chunks)
        total_chunks += n
        for c in chunks:
            tag_counts[c.quality_tag] = tag_counts.get(c.quality_tag, 0) + 1
        log.info("  %s: %d chunks", rel, n)

    total_in_db = count_knowledge_chunks(dataset_id=args.dataset_id)
    log.info("")
    log.info("DONE  files=%d  skipped=%d  chunks_upserted=%d",
             len(md_files), files_skipped, total_chunks)
    log.info("by quality_tag: %s", tag_counts)
    log.info("rows in PG for dataset=%s: %d", args.dataset_id, total_in_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
