"""Postgres 持久化层 —— Sprint 1。

职责:
- 提供 SQLAlchemy engine / session 工厂(惰性初始化, 无 POSTGRES_URL 时不连接)
- 定义 InterviewSession / EvaluationReport 的 ORM 模型
- 提供 schemas(pydantic) <-> ORM 的转换与读写接口

调用约定:
- 业务层(orchestrator/agents/api) 只 import repository 里的 save_*/load_*,
  不直接接触 SQLAlchemy session, 也不直接读 ORM 行。
- ORM 模型对业务不透明: 拿到的永远是 src.schemas 里的 pydantic 类型。

后续:
- Sprint 1 后期: 引入 Alembic 做 schema 演进(目前先用 metadata.create_all)
- Sprint 2: FastAPI 起来后再评估是否切 async engine
"""
from __future__ import annotations

from src.db.base import (
    DatabaseNotConfigured,
    get_engine,
    init_db,
    session_scope,
)
from src.db.repository import (
    load_candidate,
    load_job,
    load_plan,
    load_report,
    load_session,
    save_candidate,
    save_job,
    save_plan,
    save_report,
    save_session,
)

__all__ = [
    "DatabaseNotConfigured",
    "get_engine",
    "init_db",
    "session_scope",
    "load_candidate",
    "load_job",
    "load_plan",
    "load_report",
    "load_session",
    "save_candidate",
    "save_job",
    "save_plan",
    "save_report",
    "save_session",
]
