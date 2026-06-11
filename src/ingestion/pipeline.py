"""高层 ingest_* 函数 —— 切片 + embed + 入 Milvus。

三个独立函数 (jd / company_material / resume) 而非一个 +kind 参数,
因为各类型的 document_id 命名前缀不同, 拆函数让上游路由清楚自己在做什么。

document_id 命名: "{source_id}:{kind_short}:{idx}"
    JD:               "{job_id}:jd:{idx}"
    company_material: "{job_id}:cm:{idx}"
    resume:           "{candidate_id}:resume:{idx}"
重复入库的天然幂等: 切片确定 -> idx 确定 -> document_id 确定 -> upsert 不重复。
"""
from __future__ import annotations

import logging

from src import embeddings, vector_store
from src.ingestion.chunker import chunk_text

log = logging.getLogger(__name__)


def _ingest(
    *,
    source_id: str,
    kind: str,
    id_prefix: str,
    text: str,
) -> int:
    """通用 ingest: 切 -> embed -> upsert_document; 返回真的入 Milvus 的切片数。
    embed 为 stub 时 upsert 自动跳过 (vector_store.upsert_document 内已防护)。"""
    if not text or not text.strip():
        return 0

    chunks = chunk_text(text)
    written = 0
    for idx, chunk in enumerate(chunks):
        vec = embeddings.embed(chunk)
        ok = vector_store.upsert_document(
            document_id=f"{source_id}:{id_prefix}:{idx}",
            kind=kind,
            source_id=source_id,
            chunk_index=idx,
            text=chunk,
            embedding=vec,
        )
        if ok:
            written += 1
    return written


def ingest_jd(job_id: str, jd_text: str) -> int:
    """切 JD + embed + 入 Milvus。返回入库切片数。"""
    return _ingest(
        source_id=job_id,
        kind=vector_store.DOC_KIND_JD,
        id_prefix="jd",
        text=jd_text,
    )


def ingest_company_material(job_id: str, material_text: str) -> int:
    """切公司资料 + embed + 入 Milvus。"""
    return _ingest(
        source_id=job_id,
        kind=vector_store.DOC_KIND_COMPANY_MATERIAL,
        id_prefix="cm",
        text=material_text,
    )


def ingest_resume(candidate_id: str, resume_text: str) -> int:
    """切候选人 Resume + embed + 入 Milvus。"""
    return _ingest(
        source_id=candidate_id,
        kind=vector_store.DOC_KIND_RESUME,
        id_prefix="resume",
        text=resume_text,
    )
