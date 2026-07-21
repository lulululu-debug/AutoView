"""Sprint B: LLM 反向出题 CLI。

读 knowledge_chunks 表里指定 dataset 的 chunk, 跳过 low_value / navigation,
对每个 chunk 调 derive_chunk() 拿到 1-5 道 DerivedQuestion, 拼装成
QuestionDraft 落 question_drafts 表 (review_status='pending')。

用法:
    python -m scripts.derive_questions \\
        --dataset-id javaguide-basis-smoke \\
        [--skip-tags low_value navigation oversize] \\
        [--limit 10] \\
        [--truncate-pending]

设计取舍:
- 单 chunk 一事务: LLM 失败 / 解析失败 / 字段非法 → 跳过该 chunk, 不阻塞下一个。
- --truncate-pending 只清当前 prompt_version 的 pending draft; approved/rejected
  历史不动 (复杂度 0, 后续 sprint 不用回头清)。
- 默认 skip_tags = (low_value, navigation): oversize 暂不跳过, 让 LLM 看能否
  消化; 真正爆 token 时再加进去。
- 不并发: 一份脚本一次 LLM 调用, 排错简单; Sprint D 上 HTTP 上传时改异步队列。
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from src.db.base import init_db
from src.db.repository import (
    count_question_drafts,
    delete_question_drafts_for_dataset,
    get_dataset,
    list_knowledge_chunks,
    upsert_question_drafts,
)
from src.derivation import (
    derive_chunk,
    llm_model_name,
    make_draft_id,
    prompt_version,
)
from src.schemas import KnowledgeChunk, QuestionDraft

log = logging.getLogger("derive_questions")


_DEFAULT_SKIP_TAGS = ["low_value", "navigation"]


def _drafts_from_chunk(
    chunk: KnowledgeChunk, *, model: str, pv: str, category: str,
) -> list[QuestionDraft]:
    """单 chunk → list[QuestionDraft]; LLM 失败时 derive_chunk 返空 list,
    这里跟着返空, CLI 计数为'未出题 chunk'."""
    derived = derive_chunk(chunk, category=category)
    out: list[QuestionDraft] = []
    for dq in derived:
        out.append(QuestionDraft(
            draft_id=make_draft_id(
                chunk_id=chunk.chunk_id,
                question_text=dq.question_text,
                category=category,
            ),
            chunk_id=chunk.chunk_id,
            dataset_id=chunk.dataset_id,
            question_text=dq.question_text,
            qtype=dq.qtype,
            difficulty=dq.difficulty,
            key_points=dq.key_points,
            prompt_version=pv,
            llm_model=model,
            category=category,
        ))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument(
        "--skip-tags", nargs="*", default=_DEFAULT_SKIP_TAGS,
        help="quality_tag 黑名单, 默认 low_value navigation",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="只跑前 N 个 chunk (按 chunk_id 排序), 调试用",
    )
    parser.add_argument(
        "--truncate-pending", action="store_true",
        help="跑前先删该 dataset + 当前 prompt_version 的 pending draft "
             "(approved/rejected 不删)",
    )
    parser.add_argument(
        "--category", default=None, choices=["knowledge", "scenario"],
        help="覆盖 datasets 表 category. None=从 PG 读 (默认 knowledge)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()

    # Sprint upload: category 来源优先级 CLI override > datasets 表 > "knowledge"
    if args.category:
        category = args.category
    else:
        ds = get_dataset(args.dataset_id)
        category = ds.category if ds else "knowledge"

    pv = prompt_version(category)
    model = llm_model_name()
    log.info("prompt_version=%s  model=%s  dataset=%s  category=%s",
             pv, model, args.dataset_id, category)

    if args.truncate_pending:
        deleted = delete_question_drafts_for_dataset(
            args.dataset_id, prompt_version=pv,
        )
        log.info("truncated %d pending drafts (same prompt_version)", deleted)

    chunks = list_knowledge_chunks(
        dataset_id=args.dataset_id,
        exclude_quality_tags=args.skip_tags,
        limit=args.limit,
    )
    log.info("targeting %d chunks (skip_tags=%s, limit=%s)",
             len(chunks), args.skip_tags, args.limit)

    if not chunks:
        log.warning("no chunks matched; nothing to do")
        return 0

    total_drafts = 0
    chunks_with_zero = 0
    qtype_counter: Counter[str] = Counter()
    diff_counter: Counter[str] = Counter()

    for i, chunk in enumerate(chunks, 1):
        drafts = _drafts_from_chunk(chunk, model=model, pv=pv, category=category)
        if not drafts:
            chunks_with_zero += 1
            log.warning("  [%d/%d] %s ... 0 drafts (LLM fail / no parsable JSON)",
                        i, len(chunks), chunk.chunk_id)
            continue
        upsert_question_drafts(drafts)
        total_drafts += len(drafts)
        for d in drafts:
            qtype_counter[d.qtype] += 1
            diff_counter[d.difficulty] += 1
        leaf = chunk.heading_path[-1] if chunk.heading_path else "(no heading)"
        log.info("  [%d/%d] %s  →  %d drafts  | %s",
                 i, len(chunks), chunk.chunk_id, len(drafts), leaf)

    pending_in_db = count_question_drafts(
        dataset_id=args.dataset_id, review_status="pending",
        prompt_version=pv,
    )
    log.info("")
    log.info("DONE  chunks=%d  zero_chunks=%d  drafts_upserted=%d",
             len(chunks), chunks_with_zero, total_drafts)
    log.info("by qtype:      %s", dict(qtype_counter))
    log.info("by difficulty: %s", dict(diff_counter))
    log.info("pending drafts in PG (dataset=%s, prompt_version=%s): %d",
             args.dataset_id, pv, pending_in_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
