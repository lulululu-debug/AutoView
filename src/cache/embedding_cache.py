"""Embedding 向量的 Redis 缓存。

与 llm_cache 的区别:
- 缓存值是 list[float], 比文本大很多, 走 JSON 序列化。
- TTL 默认更长 (30 天): 同一模型对同文本输出应当严格相等, 改 prompt 模板
  不影响 embedding; 真要换模型才需要 invalidate, 那时 key 自带 model 串能区分。
- 与 llm_cache 完全平行的接口 (make_key / get / set), 行为一致:
  Redis 不可用时 get -> None, set -> no-op, 不阻塞 embedding 主链路。

强刷:
    redis-cli --scan --pattern 'emb:*' | xargs redis-cli DEL
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

from src.cache.base import RedisNotConfigured, get_redis

_KEY_PREFIX = "emb:"
_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 天


def make_key(text: str, model: str) -> str:
    """对 embedding 输入做规范化哈希。
    canonical 把 model 也拍进去, 换模型不会撞老缓存。"""
    canonical = json.dumps(
        {"text": text, "model": model},
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


def _ttl_seconds() -> int:
    raw = os.environ.get("EMBEDDING_CACHE_TTL_SECONDS")
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_TTL_SECONDS
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def get(key: str) -> Optional[list[float]]:
    """命中返回向量; 未命中 / Redis 不可用 -> None。"""
    try:
        r = get_redis()
    except RedisNotConfigured:
        return None
    try:
        raw = r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        # 网络抖动 / JSON 解析失败 都不应该影响 embedding 主链路
        return None


def set(key: str, vector: list[float]) -> None:
    """写入缓存; Redis 不可用 / 报错时静默忽略。"""
    if not vector:
        return
    try:
        r = get_redis()
    except RedisNotConfigured:
        return
    try:
        r.set(key, json.dumps(vector), ex=_ttl_seconds())
    except Exception:
        return
