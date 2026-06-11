"""Redis 客户端单例 + 惰性连接。

与 src.db.base 同款套路:
- import 本模块不读 REDIS_URL, 不建连接。
- 调用 get_redis() 时才真正建池; 未配置时抛 RedisNotConfigured。
- 这样骨架 src.main 仍能在没有 Redis 的环境跑通。
"""
from __future__ import annotations

import os
from typing import Optional

from redis import Redis


class RedisNotConfigured(RuntimeError):
    """REDIS_URL 未设置时, 任何需要 Redis 的调用都抛出本异常。"""


_client: Optional[Redis] = None


def _build_client() -> Redis:
    url = os.environ.get("REDIS_URL")
    if not url:
        raise RedisNotConfigured(
            "REDIS_URL 未配置, 无法连接 Redis。"
            "参考 .env.example, 例如 redis://localhost:6379/0"
        )
    # decode_responses=True: 直接拿 str, 不用每次 .decode()
    return Redis.from_url(url, decode_responses=True)


def get_redis() -> Redis:
    """返回单例 Redis 客户端, 首次调用时按 REDIS_URL 建立连接池。"""
    global _client
    if _client is None:
        _client = _build_client()
    return _client


_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h


def ttl_seconds() -> int:
    """会话/计划在 Redis 的 TTL, 由 SESSION_TTL_SECONDS 覆盖。
    session_store 与 plan_store 共用同一个 TTL, 避免两者错位失效。"""
    raw = os.environ.get("SESSION_TTL_SECONDS")
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_TTL_SECONDS
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def reset_client_for_testing() -> None:
    """测试用: 清掉单例, 让下一次 get_redis() 重新读 REDIS_URL。
    业务代码不要调用本函数。"""
    global _client
    _client = None
