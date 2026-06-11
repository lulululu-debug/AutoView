"""向量存储统一入口 —— Sprint 3。

调用约定:
- 业务层(planner / ingestion / evaluator) 只 import 本包的函数,
  不直接接触 pymilvus, 也不直接读 collection schema。
- 切 store (Milvus -> Qdrant / Weaviate / pgvector) 时只动 base + operations,
  上游不动。

与 src.db / src.cache 一致的"惰性 + 缺 env 抛"风格:
- import 本包不连接
- 缺 MILVUS_URI 时抛 MilvusNotConfigured
"""
from __future__ import annotations

from src.vector_store.base import (
    MilvusNotConfigured,
    get_client,
    reset_client_for_testing,
)
from src.vector_store.collections import (
    COLLECTION_DOCUMENTS,
    COLLECTION_QUESTIONS,
    DOC_KIND_COMPANY_MATERIAL,
    DOC_KIND_JD,
    DOC_KIND_RESUME,
    drop_collections,
    init_collections,
)
from src.vector_store.operations import (
    count_documents,
    count_questions,
    search_documents,
    search_questions,
    upsert_document,
    upsert_question,
)

__all__ = [
    "MilvusNotConfigured",
    "get_client",
    "reset_client_for_testing",
    "COLLECTION_DOCUMENTS",
    "COLLECTION_QUESTIONS",
    "DOC_KIND_COMPANY_MATERIAL",
    "DOC_KIND_JD",
    "DOC_KIND_RESUME",
    "init_collections",
    "drop_collections",
    "count_documents",
    "count_questions",
    "search_documents",
    "search_questions",
    "upsert_document",
    "upsert_question",
]
