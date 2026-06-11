"""Embedding 调用统一入口 —— Sprint 3 起。

与 src/llm 对称:
- embed() 是 agent / ingestion / planner 接触 embedding 的唯一通道
- 缺 OPENAI_API_KEY 或 SDK 不可用时, 返回前缀可识别的 stub 向量(全零),
  让骨架 / eval / 离线开发可以继续, 调用方用 is_stub_vector() 决定是否
  跳过 Milvus 写入(全零向量进库会拉低召回质量)
- 透明 Redis 缓存: 命中直接返回, 未命中调 API 并写回; stub 不入缓存

为什么选 OpenAI text-embedding-3-small:
- 1536 维, 模型大小适中, 中文表现可用
- 成本 $0.02/1M tokens, 跟 LLM 调用比可忽略
- anthropic 没有 embedding API, 这是 dev 期最务实的选择;
  日后切换到 voyage / 本地 sentence-transformers 时, 改本文件即可,
  agent / ingestion 不动

OPENAI_BASE_URL 可覆盖, 兼容代理 / 私有部署。
"""
from __future__ import annotations

import os

from src.cache import embedding_cache

EMBEDDING_DIM = 1536  # text-embedding-3-small 默认维度
DEFAULT_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
_STUB_SENTINEL = 0.0  # stub 向量元素值


def embed(text: str, *, model: str | None = None) -> list[float]:
    """单条文本 -> 向量。

    缺 OPENAI_API_KEY / openai 包不可用时返回 stub 向量(全零),
    调用方用 is_stub_vector() 检测并决定是否跳过 Milvus 入库。

    返回值: 长度固定为 EMBEDDING_DIM 的 list[float]。
    """
    if not text:
        # 空字符串特殊处理: 不调 API, 直接 stub。比让 API 报错或返奇怪结果好。
        return _stub_vector()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _stub_vector()

    resolved_model = model or DEFAULT_MODEL
    cache_key = embedding_cache.make_key(text, resolved_model)

    cached = embedding_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from openai import OpenAI
    except ImportError:
        return _stub_vector()

    base_url = os.environ.get("OPENAI_BASE_URL") or None
    client = OpenAI(api_key=api_key, base_url=base_url)
    resp = client.embeddings.create(model=resolved_model, input=text)
    vector = list(resp.data[0].embedding)

    if vector and not is_stub_vector(vector):
        embedding_cache.set(cache_key, vector)

    return vector


def is_stub_vector(vector: list[float]) -> bool:
    """判断是否为 stub 向量。
    stub 是固定模式(全零), 真实 embedding 全零的概率为 0,
    所以这是一个安全且无歧义的检测。"""
    if not vector or len(vector) != EMBEDDING_DIM:
        return True
    return all(v == _STUB_SENTINEL for v in vector)


def _stub_vector() -> list[float]:
    """占位向量: 与真实输出维度一致, 元素全零。
    入 Milvus 前由调用方判断是否跳过 (避免污染向量空间)。"""
    return [_STUB_SENTINEL] * EMBEDDING_DIM
