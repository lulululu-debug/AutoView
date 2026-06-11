"""进行中的 InterviewSession 在 Redis 的读写。

设计取舍(Sprint 1):
- 每个 session 一个 key: session:{session_id}, 整体 JSON 字符串。
  原因: 当前没有按子字段做原子写的场景, 一把读一把写最简单, pydantic
  天然支持 model_dump_json / model_validate_json 来回。
- 默认 TTL 24 小时, 通过 SESSION_TTL_SECONDS 环境变量覆盖。
  原因: 进行中的面试不可能挂着不动 N 天, TTL 避免脏会话累积;
  归档后会显式 delete_session, 不依赖 TTL 兜底。
- 不在本模块做"写 Redis -> 写 Postgres -> 删 Redis"的编排; 那是
  Orchestrator 的事(下一个 task)。本模块只提供原语。
"""
from __future__ import annotations

import os
from typing import Optional

from src.cache.base import get_redis
from src.schemas import InterviewSession

_KEY_PREFIX = "session:"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h


def _key(session_id: str) -> str:
    return f"{_KEY_PREFIX}{session_id}"


def _ttl_seconds() -> int:
    raw = os.environ.get("SESSION_TTL_SECONDS")
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_TTL_SECONDS
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def save_session(session: InterviewSession) -> None:
    """写入(或覆盖)进行中的会话, 同时刷新 TTL。"""
    r = get_redis()
    r.set(_key(session.session_id), session.model_dump_json(), ex=_ttl_seconds())


def load_session(session_id: str) -> Optional[InterviewSession]:
    """读取进行中的会话; 不存在或已过期返回 None。"""
    r = get_redis()
    raw = r.get(_key(session_id))
    if raw is None:
        return None
    return InterviewSession.model_validate_json(raw)


def delete_session(session_id: str) -> None:
    """删除热缓存里的会话(通常在归档到 Postgres 之后调用)。
    不存在时静默成功, 调用方不用处理 KeyError。"""
    r = get_redis()
    r.delete(_key(session_id))
