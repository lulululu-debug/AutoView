"""questions / documents 的 upsert / search / count。

设计取舍:
- stub 向量(全零) 静默跳过 upsert + warning 日志, 不污染向量空间。
  调用方可以提前 is_stub_vector 自行决定, 但这层兜底让上游 ingestion
  代码不必每处都判。
- 返回 list[dict] 而非 pymilvus 原生 Hits 对象, 让上游对 SDK 解耦,
  日后换 store (Qdrant / Weaviate) 时调用方不动。
- search 的 filter 用关键字参数(role_family, competency, kind, source_id)
  组合, 避免上游手写 Milvus expr 字符串。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.embeddings import is_stub_vector
from src.vector_store.base import get_client
from src.vector_store.collections import COLLECTION_DOCUMENTS, COLLECTION_QUESTIONS

log = logging.getLogger(__name__)

_QUESTIONS_OUTPUT = ("question_id", "role_family", "competency", "category", "text")
_DOCUMENTS_OUTPUT = ("document_id", "kind", "source_id", "chunk_index", "text")


def _build_filter(**conditions: Optional[str | int]) -> str:
    """把关键字过滤拼成 Milvus expr; 空字典返回空串。
    类型: 字符串值自动加双引号, 数字直接写; None 跳过。"""
    parts = []
    for k, v in conditions.items():
        if v is None:
            continue
        if isinstance(v, str):
            # Milvus expr 字符串字面量用双引号; 值里的引号已经在 upsert 阶段
            # 受 schema 长度约束, 这里不做额外转义(Sprint 3-2 范围)。
            parts.append(f'{k} == "{v}"')
        else:
            parts.append(f"{k} == {v}")
    return " and ".join(parts)


# ---------- questions ----------

def upsert_question(
    *,
    question_id: str,
    role_family: str,
    competency: str,
    text: str,
    embedding: list[float],
    category: str = "knowledge",
) -> bool:
    """写入或更新一道题。返回 True 表示真的入库, False 表示因 stub 向量被跳过。
    Sprint 5.5: category 默认 knowledge 让老调用方零改动。"""
    if is_stub_vector(embedding):
        log.warning(
            "skip upsert_question(%s): stub vector (全零), 不污染向量空间",
            question_id,
        )
        return False
    client = get_client()
    client.upsert(
        collection_name=COLLECTION_QUESTIONS,
        data=[{
            "question_id": question_id,
            "role_family": role_family,
            "competency": competency,
            "category": category,
            "text": text,
            "embedding": embedding,
        }],
    )
    return True


def search_questions(
    *,
    embedding: list[float],
    top_k: int = 5,
    role_family: Optional[str] = None,
    competency: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict[str, Any]]:
    """按向量召回 top_k 道题, 可选 role_family / competency / category 过滤。
    返回 list[dict], 含字段 + distance (COSINE: 越小越相似)。
    Sprint 5.5: category 过滤让 Planner 按 stage 分别拉 knowledge / scenario 题。"""
    if is_stub_vector(embedding):
        # stub 向量召回结果无意义, 返回空免得调用方误用
        return []
    client = get_client()
    expr = _build_filter(
        role_family=role_family, competency=competency, category=category,
    )
    results = client.search(
        collection_name=COLLECTION_QUESTIONS,
        data=[embedding],
        limit=top_k,
        filter=expr,
        output_fields=list(_QUESTIONS_OUTPUT),
    )
    return _flatten_hits(results)


def count_questions() -> int:
    """题库行数(包含 deleted/未 compact 的; dev/test 中近似精确)。"""
    client = get_client()
    stats = client.get_collection_stats(COLLECTION_QUESTIONS)
    return int(stats.get("row_count", 0))


# ---------- documents ----------

def upsert_document(
    *,
    document_id: str,
    kind: str,
    source_id: str,
    chunk_index: int,
    text: str,
    embedding: list[float],
) -> bool:
    if is_stub_vector(embedding):
        log.warning(
            "skip upsert_document(%s): stub vector (全零)", document_id,
        )
        return False
    client = get_client()
    client.upsert(
        collection_name=COLLECTION_DOCUMENTS,
        data=[{
            "document_id": document_id,
            "kind": kind,
            "source_id": source_id,
            "chunk_index": chunk_index,
            "text": text,
            "embedding": embedding,
        }],
    )
    return True


def search_documents(
    *,
    embedding: list[float],
    top_k: int = 5,
    kind: Optional[str] = None,
    source_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    if is_stub_vector(embedding):
        return []
    client = get_client()
    expr = _build_filter(kind=kind, source_id=source_id)
    results = client.search(
        collection_name=COLLECTION_DOCUMENTS,
        data=[embedding],
        limit=top_k,
        filter=expr,
        output_fields=list(_DOCUMENTS_OUTPUT),
    )
    return _flatten_hits(results)


def count_documents() -> int:
    client = get_client()
    stats = client.get_collection_stats(COLLECTION_DOCUMENTS)
    return int(stats.get("row_count", 0))


# ---------- 内部: 把 pymilvus 的 Hits 拍成 list[dict] ----------

def _flatten_hits(results: Any) -> list[dict[str, Any]]:
    """results 形如 [[{id, distance, entity: {...}}, ...]]; 我们只支持单 query。
    把 entity 摊平到顶层 + 保留 distance。"""
    if not results:
        return []
    out = []
    for hit in results[0]:
        flat: dict[str, Any] = {}
        if isinstance(hit, dict):
            entity = hit.get("entity") or {}
            flat.update(entity)
            flat["distance"] = hit.get("distance")
        else:
            # 老版 pymilvus Hit 对象走属性访问
            entity = getattr(hit, "entity", None)
            if entity is not None:
                for k in (*_QUESTIONS_OUTPUT, *_DOCUMENTS_OUTPUT):
                    if hasattr(entity, k):
                        flat[k] = getattr(entity, k)
                    elif hasattr(entity, "get"):
                        v = entity.get(k)
                        if v is not None:
                            flat[k] = v
            flat["distance"] = getattr(hit, "distance", None)
        out.append(flat)
    return out
