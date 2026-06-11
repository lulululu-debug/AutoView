"""资料切片 + 向量化 + 入 Milvus —— Sprint 3-4。

调用方:
- api/routes/jobs.py        POST /jobs            后台 ingest JD + company_materials
- api/routes/candidates.py  POST .../candidates   后台 ingest resume (与 Planner 并行)

设计:
- chunker.chunk_text 中文友好的固定长度切片(~500 + 50 overlap), 不引 langchain。
- pipeline 里三种类型(jd / company_material / resume) 分别一个 ingest_*, 因为
  document_id 与 source 类型耦合, 拆函数比共用一个 +kind 参数易读。
- embed stub (无 OPENAI_API_KEY) -> upsert 自动跳过 Milvus, written=0 但不报错。
  Resume 这种 PG 已有真理之源 (candidates.resume 字段), 没入 Milvus 不致命,
  Planner 拿不到 RAG 时退到现场 LLM 生成 (Sprint 3-6 处理)。

为什么不持久化切片到 PG:
- 切片是 derived data, 原文已在 PG (jobs.jd / candidates.resume / jobs.company_materials)
- Milvus 是用来"按语义召回"的副本, 不是真理之源
- 真要重建 Milvus, 重跑 ingestion 即可 (输入是 PG 里的原文)
"""
from __future__ import annotations

from src.ingestion.chunker import chunk_text
from src.ingestion.pipeline import (
    ingest_company_material,
    ingest_jd,
    ingest_resume,
)

__all__ = [
    "chunk_text",
    "ingest_company_material",
    "ingest_jd",
    "ingest_resume",
]
