"""LLM 调用结果的 Redis 缓存。

为什么:
- 同一个 (system, user, model, max_tokens) 在 Anthropic 端应当输出稳定语义,
  反复调用既慢又烧钱。Planner 4 题、Interviewer 追问、Evaluator 总结都会触发
  LLM 调用; 重跑骨架时, 命中率近乎 100%。
- Sprint 1 的"JD 解析结果加 Redis 缓存"也由本模块顺带覆盖 —— JD 解析在
  Sprint 2 会落到 LLM 上, 走 complete() 就自动被缓存。

设计:
- key: "llm:<sha256(canonical_input)>", canonical 用 json.dumps sort_keys + ensure_ascii=False。
  把所有影响输出的字段(system/user/model/max_tokens) 都拍进去, 漏一个就会
  在不同提示返回旧结果 —— 这是这类缓存最常见的 bug。
- value: response 纯文本。
- TTL: 默认 7 天, 由 LLM_CACHE_TTL_SECONDS 覆盖。模型升级 / Prompt 改版自然
  在一周内被冲刷。运维想强刷: redis-cli --scan --pattern 'llm:*' | xargs redis-cli DEL。

降级:
- Redis 未配置或连接失败时, get/set 静默返回 None / no-op,
  让 src/llm/complete() 直连 Anthropic。骨架开发流不被 Redis 状态绑死。
- stub 输出(`[stub] ...`)由调用方判断, 不进本模块 —— 缓存层不识别业务语义。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

from src.cache.base import RedisNotConfigured, get_redis

_KEY_PREFIX = "llm:"
_DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 天


def make_key(system: str, user: str, model: str, max_tokens: int) -> str:
    """对 LLM 输入做规范化哈希, 得到稳定的 cache key。"""
    canonical = json.dumps(
        {"system": system, "user": user, "model": model, "max_tokens": max_tokens},
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


def _ttl_seconds() -> int:
    raw = os.environ.get("LLM_CACHE_TTL_SECONDS")
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_TTL_SECONDS
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def get(key: str) -> Optional[str]:
    """命中返回缓存文本; 未命中 / Redis 不可用 一律返回 None。"""
    try:
        r = get_redis()
    except RedisNotConfigured:
        return None
    try:
        return r.get(key)
    except Exception:
        # 网络抖动 / Redis 重启 等都不应该影响 LLM 主链路
        return None


def set(key: str, value: str) -> None:
    """写入缓存; Redis 不可用 / 报错时静默忽略。"""
    if not value:
        return
    try:
        r = get_redis()
    except RedisNotConfigured:
        return
    try:
        r.set(key, value, ex=_ttl_seconds())
    except Exception:
        return
