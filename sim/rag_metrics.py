"""RAG 检索确定性指标 —— Sprint 6.5 task 5。**只花 embedding (~分厘级)。**

    python -m sim.rag_metrics

两组指标 (零 LLM judge, 全部确定性可复跑):
- 题库召回 (questions collection, role_family=backend 过滤):
  hit@5   — 金标 query 的 top-5 内任一题命中期望关键词
  污染@5  — top-5 内出现 AI/技能类派生题特征词 (F6 的量化指标)
  标签完整性 — 返回行的 role_family 必须等于过滤值 (过滤器回归护栏)
- documents 召回 (resume 切片路径): 固定 fixture 简历自包含验证,
  query -> 期望切片关键词。

红线: 题库 hit@5 ≥ 80%; 污染@5 = 0 (F6 修复验收线); 标签完整性 100%;
documents 3/3。任一红线破 → exit 1。

注意: 本工具读 **dev** PG/Milvus (题库真理之源在 dev, 不走 sim 的 TEST 库
切换), 只读题库 + 幂等写一个 fixture 简历切片, 不碰业务数据。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 题库在 dev 库; 必须在 bootstrap 之前设定 (bootstrap 读该 env 决定是否切 TEST)
os.environ["SIM_USE_DEV_DB"] = "1"

from sim._env import bootstrap  # noqa: E402

_TOP_K = 5


def main() -> None:
    bootstrap()

    from src import embeddings, ingestion, vector_store

    gold = json.loads(
        Path("sim/data/rag_gold.json").read_text(encoding="utf-8"),
    )
    forbid_common: list[str] = gold["forbid_common"]
    failures: list[str] = []

    # ---- 1. 题库召回 ----
    n = len(gold["question_bank"])
    hits = clean = labels_ok = 0
    print(f"[rag] 题库召回: {n} 条金标 query (top_k={_TOP_K}, role_family=backend)")
    for g in gold["question_bank"]:
        vec = embeddings.embed(g["query"])
        if embeddings.is_stub_vector(vec):
            raise SystemExit("embedding 走了 stub, 检索指标无意义 (需真 key)")
        rows = vector_store.search_questions(
            embedding=vec, top_k=_TOP_K,
            role_family="backend", category=g["category"],
        )
        texts = [r.get("text", "") for r in rows]
        hit = any(kw in t for t in texts for kw in g["expect"])
        dirty = [
            (kw, t[:40]) for t in texts for kw in forbid_common if kw in t
        ]
        label_ok = all(r.get("role_family") == "backend" for r in rows)
        hits += hit
        clean += not dirty
        labels_ok += label_ok
        mark = "✅" if (hit and not dirty and label_ok) else "❌"
        extra = ""
        if not hit:
            extra += " [未命中期望]"
        if dirty:
            extra += f" [污染: {dirty[0][0]!r} in {dirty[0][1]!r}...]"
        if not label_ok:
            extra += " [标签漏过滤]"
        print(f"  {g['id']}  {mark}{extra}")

    hit_rate = hits / n
    contamination = n - clean
    print(f"[rag] 题库: hit@{_TOP_K}={hits}/{n} ({hit_rate:.0%}), "
          f"污染 query 数={contamination}, 标签完整={labels_ok}/{n}")
    if hit_rate < 0.8:
        failures.append(f"题库 hit@{_TOP_K} {hit_rate:.0%} < 80%")
    if contamination > 0:
        failures.append(f"题库污染 query {contamination} 个 > 0 (F6)")
    if labels_ok != n:
        failures.append("标签完整性破损 (过滤器回归!)")

    # ---- 2. documents 召回 (fixture 自包含) ----
    src_id = gold["documents_fixture_source_id"]
    ingestion.ingest_resume(src_id, gold["documents_fixture_resume"])
    doc_hits = 0
    n_doc = len(gold["documents"])
    print(f"[rag] documents 召回: fixture={src_id}, {n_doc} 条 query")
    for g in gold["documents"]:
        vec = embeddings.embed(g["query"])
        rows = vector_store.search_documents(
            embedding=vec, top_k=3, kind="resume", source_id=src_id,
        )
        texts = [r.get("text", "") for r in rows]
        hit = any(kw in t for t in texts for kw in g["expect"])
        doc_hits += hit
        print(f"  {g['id']}  {'✅' if hit else '❌ 未命中期望切片'}")
    print(f"[rag] documents: hit={doc_hits}/{n_doc}")
    if doc_hits != n_doc:
        failures.append(f"documents 召回 {doc_hits}/{n_doc} != 全中")

    # ---- 结论 ----
    if failures:
        print(f"\n[rag] ❌ {len(failures)} 项红线未过:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[rag] ✅ 全部红线通过")


if __name__ == "__main__":
    main()
