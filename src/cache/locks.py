"""per-session Redis 锁 —— Sprint 9 task 3。

并发场景下 submit_answer / finalize 都是"读 session -> 改 -> 写回"的
非原子序列: 同一会话两个并发请求会互相覆盖 (答案丢失 / 状态机错乱)。
面试是单候选人串行交互, 并发同会话请求只可能是双击/重试/攻击 ——
锁上直接拒 (API 层 409), 不排队。

SET NX PX 取锁, token 比对后 DEL 释放 (Lua 原子, 不删别人的锁);
TTL 兜底防死锁 (持有者崩溃后自动过期)。
"""
from __future__ import annotations

import uuid

from src.cache.base import get_redis

# 单 turn 上限: Assessor 10s + followup 生成 + lazy resolve, 30s 富余
_LOCK_TTL_SECONDS = 30

_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _key(session_id: str) -> str:
    return f"lock:session:{session_id}"


def acquire_session_lock(session_id: str) -> str | None:
    """拿到返回 token; 已被占用返回 None。"""
    token = uuid.uuid4().hex
    ok = get_redis().set(
        _key(session_id), token, nx=True, ex=_LOCK_TTL_SECONDS,
    )
    return token if ok else None


def release_session_lock(session_id: str, token: str) -> None:
    """只释放自己的锁 (token 比对原子化); 锁已过期/被顶掉时静默。"""
    try:
        get_redis().eval(_RELEASE_LUA, 1, _key(session_id), token)
    except Exception:
        pass  # 释放失败靠 TTL 兜底, 不让释放动作影响主链路
