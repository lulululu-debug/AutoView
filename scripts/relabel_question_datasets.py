"""按 dataset 改标 seed_questions.role_family + Milvus 重建 —— Sprint 6.5 F6。

    python -m scripts.relabel_question_datasets --datasets a,b --role-family ai --dry-run
    python -m scripts.relabel_question_datasets --datasets agent-skill-basics,ai-agent-basics,cs-ai-rag,mcp-basics --role-family ai

背景 (judge 审计 F6): 知识管线派生题审批时 role_family 默认 backend,
AI/技能类 dataset 的题全部混进 backend JD 召回池 (9 道去重召回题中 5 道弱相关)。
修法: PG (真理之源) 改标 → Milvus questions 全量重建 (与 admin
reseed-milvus-questions 同款核心逻辑)。

安全护栏: 重建前先跑 **embedding 缓存覆盖率探针** —— 全部题文本必须命中
Redis embedding 缓存 (embed 不打 API) 才允许 drop 旧集合。任何 miss 直接
中止, 防止在无 API 配额时把题库炸成半残。
"""
from __future__ import annotations

import argparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import text as sql_text

from src import db, embeddings, vector_store
from src.cache import embedding_cache
from src.db.base import get_engine


def _relabel(datasets: list[str], role_family: str, dry_run: bool) -> int:
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(sql_text(
            "SELECT dataset_id, count(*) FROM seed_questions "
            "WHERE dataset_id = ANY(:ds) GROUP BY 1 ORDER BY 1"
        ), {"ds": datasets}).all()
        total = sum(r[1] for r in rows)
        for ds, n in rows:
            print(f"  {ds:<28} {n} 道 -> role_family={role_family!r}")
        missing = set(datasets) - {r[0] for r in rows}
        if missing:
            print(f"  警告: 以下 dataset 无题: {sorted(missing)}")
        if dry_run or total == 0:
            return total
        conn.execute(sql_text(
            "UPDATE seed_questions SET role_family = :rf "
            "WHERE dataset_id = ANY(:ds)"
        ), {"rf": role_family, "ds": datasets})
    return total


def _probe_embedding_cache(seeds) -> list[str]:
    """返回未命中 embedding 缓存的题目 id 列表 (命中 = 重建不打 API)。"""
    misses = []
    for seed in seeds:
        key = embedding_cache.make_key(seed.text, embeddings.DEFAULT_MODEL)
        if embedding_cache.get(key) is None:
            misses.append(seed.question_id)
    return misses


def _reseed() -> None:
    """与 admin POST /admin/diag/reseed-milvus-questions 同款核心。"""
    client = vector_store.get_client()
    before = vector_store.count_questions()
    if client.has_collection(vector_store.COLLECTION_QUESTIONS):
        client.drop_collection(vector_store.COLLECTION_QUESTIONS)
    vector_store.init_collections()

    topic_by_dataset = {d.dataset_id: d.topic for d in db.list_datasets()}
    topic_by_draft = db.map_draft_chunk_topics()
    seeds = db.list_seed_questions()
    upserted = errored = 0
    for seed in seeds:
        vec = embeddings.embed(seed.text)
        if embeddings.is_stub_vector(vec):
            errored += 1
            continue
        topic = db.compose_question_topic(
            topic_by_draft.get(seed.source_draft_id or "", ""),
            topic_by_dataset.get(seed.dataset_id, ""),
        )
        try:
            vector_store.upsert_question(
                question_id=seed.question_id,
                role_family=seed.role_family,
                competency=seed.competency,
                text=seed.text,
                embedding=vec,
                category=seed.category.value,
                dataset_id=seed.dataset_id,
                topic=topic,
                difficulty=seed.difficulty,
            )
            upserted += 1
        except Exception as e:
            print(f"  upsert 失败 {seed.question_id}: {e}")
            errored += 1
    after = vector_store.count_questions()
    print(f"[reseed] before={before} after={after} upserted={upserted} errored={errored}")


def main() -> None:
    ap = argparse.ArgumentParser(description="按 dataset 改标 role_family + Milvus 重建")
    ap.add_argument("--datasets", required=True, help="逗号分隔 dataset_id")
    ap.add_argument("--role-family", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--allow-api", action="store_true",
        help="跳过缓存覆盖率门禁, 允许重建时打 embedding API "
             "(API 配额充足时用; 3626 道 ≈ $0.01 量级)",
    )
    args = ap.parse_args()
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]

    print(f"[relabel] {'DRY-RUN ' if args.dry_run else ''}目标:")
    total = _relabel(datasets, args.role_family, args.dry_run)
    if args.dry_run:
        print(f"[relabel] dry-run 结束 (共 {total} 道)")
        return
    print(f"[relabel] PG 已改标 {total} 道")

    seeds = db.list_seed_questions()
    print(f"[probe] 检查 {len(seeds)} 道题的 embedding 缓存覆盖率...")
    misses = _probe_embedding_cache(seeds)
    if misses and args.allow_api:
        print(f"[probe] {len(misses)} 道未命中缓存, --allow-api 已授权打 API 重建")
    elif misses:
        print(f"[probe] ❌ {len(misses)} 道未命中缓存 (重建会打 API), 中止重建。")
        print("        PG 已改标 (真理之源正确); 等 API 配额恢复后重跑本脚本"
              "或调 admin reseed 端点即可让 Milvus 跟上。")
        raise SystemExit(1)
    print("[probe] ✅ 全部命中缓存, 重建零 API 调用")
    _reseed()


if __name__ == "__main__":
    main()
