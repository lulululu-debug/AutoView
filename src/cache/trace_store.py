"""进行中面试的 DecisionTrace 在 Redis 的读写 —— Sprint 8.2。

与 session_store 同款: 每 trace 一个 key trace:{session_id}, 整体 JSON,
同 SESSION_TTL_SECONDS 与 session 同生共死。finalize 归档 PG 后由
orchestrator 显式 delete_trace。

纪律: trace 是旁路观测 —— 本模块任何 Redis 异常都不该让面试链路挂,
所以 orchestrator 侧的调用都包 try/except 静默降级 (trace 丢了 ≠ 面试失败)。
"""
from __future__ import annotations

from typing import Optional

from src.cache.base import get_redis, ttl_seconds
from src.schemas import DecisionTrace

_KEY_PREFIX = "trace:"


def _key(session_id: str) -> str:
    return f"{_KEY_PREFIX}{session_id}"


def save_trace(trace: DecisionTrace) -> None:
    r = get_redis()
    r.set(_key(trace.session_id), trace.model_dump_json(), ex=ttl_seconds())


def load_trace(session_id: str) -> Optional[DecisionTrace]:
    r = get_redis()
    raw = r.get(_key(session_id))
    if raw is None:
        return None
    return DecisionTrace.model_validate_json(raw)


def delete_trace(session_id: str) -> None:
    r = get_redis()
    r.delete(_key(session_id))
