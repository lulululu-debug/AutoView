"""集合 schema 定义 + init_collections()。

两个 collection:

questions —— 题库, 按维度召回的目标
  - question_id  VARCHAR(64)  PK
  - role_family  VARCHAR(32)  按职位族过滤(后端 / 前端 / 数据科学 / ...)
  - competency   VARCHAR(64)  按考察维度过滤(技术深度 / 沟通协作 / ...)
  - category     VARCHAR(32)  Sprint 5.5: knowledge / scenario, 让 Planner 按 stage 分流
  - dataset_id   VARCHAR(64)  Sprint D-lite: 数据集隔离, 召回可按 dataset 过滤
  - topic        VARCHAR(128) Sprint D-lite: 主题词 (e.g. "JAVA 基础"), 召回可按 topic 过滤
  - difficulty   VARCHAR(16)  Sprint D-lite: easy / medium / hard, 召回可按难度过滤
  - text         VARCHAR(2048) 题目原文
  - embedding    FLOAT_VECTOR(1536)  text-embedding-3-small

documents —— 资料切片, RAG 召回的目标
  - document_id  VARCHAR(96)  PK, 通常 "{source_id}:{chunk_index}"
  - kind         VARCHAR(32)  "jd" | "resume" | "company_material"
  - source_id    VARCHAR(64)  job_id 或 candidate_id
  - chunk_index  INT64        在源中的顺序
  - text         VARCHAR(8192) 切片原文(中等长度足够, 不放整篇)
  - embedding    FLOAT_VECTOR(1536)

索引: AUTOINDEX + COSINE。Milvus Lite 数据量小, AUTOINDEX 内部就是
简单的 BF / IVF; 真上量再切 HNSW。

COSINE 距离语义: distance = 1 - cosine_similarity。
                  identical -> 0.0, opposite -> 2.0; 越小越相似。
                  这是 pymilvus 的约定, 我们的 wrapper 保持原值不做翻转,
                  调用方拿到的 'distance' 都是越小越好。
"""
from __future__ import annotations

from pymilvus import DataType, MilvusClient

from src.embeddings import EMBEDDING_DIM
from src.vector_store.base import get_client

COLLECTION_QUESTIONS = "questions"
COLLECTION_DOCUMENTS = "documents"

# kind 的合法取值
DOC_KIND_JD = "jd"
DOC_KIND_RESUME = "resume"
DOC_KIND_COMPANY_MATERIAL = "company_material"


def _build_questions_schema():
    s = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    s.add_field("question_id", DataType.VARCHAR, max_length=64, is_primary=True)
    s.add_field("role_family", DataType.VARCHAR, max_length=32)
    s.add_field("competency", DataType.VARCHAR, max_length=64)
    s.add_field("category", DataType.VARCHAR, max_length=32)
    # Sprint D-lite: 新增 3 个可过滤字段, 召回 expr 可按 dataset/topic/difficulty 缩池
    s.add_field("dataset_id", DataType.VARCHAR, max_length=64)
    s.add_field("topic", DataType.VARCHAR, max_length=128)
    s.add_field("difficulty", DataType.VARCHAR, max_length=16)
    s.add_field("text", DataType.VARCHAR, max_length=2048)
    s.add_field("embedding", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM)
    return s


def _build_documents_schema():
    s = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    s.add_field("document_id", DataType.VARCHAR, max_length=96, is_primary=True)
    s.add_field("kind", DataType.VARCHAR, max_length=32)
    s.add_field("source_id", DataType.VARCHAR, max_length=64)
    s.add_field("chunk_index", DataType.INT64)
    s.add_field("text", DataType.VARCHAR, max_length=8192)
    s.add_field("embedding", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM)
    return s


def _vector_index_params():
    p = MilvusClient.prepare_index_params()
    p.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
    return p


def init_collections() -> None:
    """按 schema 建立两个 collection。幂等 —— 已存在则跳过。
    Sprint 3-2 不引迁移, schema 改动需要 drop_collections + init_collections,
    本地 dev 数据丢失可接受。生产期再补迁移工具。

    Sprint 5.5: 加 load_collection 调用。milvus-lite 每个 python 进程开自己的
    server, 进程切换后 collection 状态可能是 'released', search/query 会报错。
    init_collections 显式 load 一次让后续 search 能直接命中。
    幂等: 已 load 的 collection 再 load 是 noop。"""
    client = get_client()
    if not client.has_collection(COLLECTION_QUESTIONS):
        client.create_collection(
            collection_name=COLLECTION_QUESTIONS,
            schema=_build_questions_schema(),
            index_params=_vector_index_params(),
        )
    if not client.has_collection(COLLECTION_DOCUMENTS):
        client.create_collection(
            collection_name=COLLECTION_DOCUMENTS,
            schema=_build_documents_schema(),
            index_params=_vector_index_params(),
        )
    # Sprint 5.5: 防"Collection in state released" 报错
    for name in (COLLECTION_QUESTIONS, COLLECTION_DOCUMENTS):
        try:
            client.load_collection(name)
        except Exception:
            # 已 load / 空 collection 偶发报警, 不影响 search; 静默吞
            pass


def drop_collections() -> None:
    """删两个 collection (本地 dev / 测试用)。"""
    client = get_client()
    for name in (COLLECTION_QUESTIONS, COLLECTION_DOCUMENTS):
        if client.has_collection(name):
            client.drop_collection(name)
