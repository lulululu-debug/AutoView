"""Redis 热缓存层 —— Sprint 1。

职责:
- 进行中的 InterviewSession 的热存储(读写吞吐远高于 Postgres)
- Sprint 1 后期: JD 解析结果 / LLM 输出缓存
- Sprint 7: 多模态分析任务队列 broker(届时单独建子模块)

调用约定:
- 业务层只用 session_store / cache 里的函数, 不直接接触 redis client。
- 与 src.db 一样: 惰性连接, 未配置 REDIS_URL 时 import 不报错,
  调用读写函数才抛 RedisNotConfigured。

与 Postgres 的关系:
- Redis 是热存储, Postgres 是归档存储。
- 会话进行中: 只写 Redis。
- 会话结束: 写 Postgres 后从 Redis 删除(archive_session)。
- 这部分编排在下一个 task(改造 Orchestrator) 中接入, 本模块只提供原语。
"""
from __future__ import annotations

from src.cache import embedding_cache, llm_cache
from src.cache.base import RedisNotConfigured, get_redis
from src.cache.plan_store import (
    delete_plan,
    load_plan,
    save_plan,
)
from src.cache.session_store import (
    delete_session,
    load_session,
    save_session,
)

__all__ = [
    "RedisNotConfigured",
    "get_redis",
    "embedding_cache",
    "llm_cache",
    "delete_plan",
    "load_plan",
    "save_plan",
    "delete_session",
    "load_session",
    "save_session",
]
