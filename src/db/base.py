"""SQLAlchemy 基础设施: Base / Engine / Session 工厂。

惰性初始化:
- import 本模块本身不连接数据库, 不读 POSTGRES_URL。
- 调用 get_engine() / init_db() / session_scope() 时才真正建立连接。
- 这样 Sprint 0 的 python -m src.main 在没有 Postgres 时仍能跑通(只要不调用 save/load)。
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class DatabaseNotConfigured(RuntimeError):
    """POSTGRES_URL 未设置时, 任何需要 DB 的调用都抛出本异常。"""


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker[Session]] = None


def _build_engine() -> Engine:
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise DatabaseNotConfigured(
            "POSTGRES_URL 未配置, 无法连接 Postgres。"
            "参考 .env.example 设置例如 postgresql+psycopg://user:pass@host:5432/db"
        )
    # future=True 是 SA 2.0 默认行为, 显式写出便于阅读
    return create_engine(url, future=True, pool_pre_ping=True)


def get_engine() -> Engine:
    """返回单例 Engine, 首次调用时按 POSTGRES_URL 建立。"""
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _build_engine()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db() -> None:
    """按当前 metadata 在目标库上 create_all。
    幂等; Sprint 1 用 create_all, schema 真的开始演进时再切换到 Alembic。"""
    # 先确保所有 ORM 模型已注册到 Base.metadata
    from src.db import models  # noqa: F401  (副作用 import: 触发模型注册)

    engine = get_engine()
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务边界: with session_scope() as s: ...
    退出时正常提交, 出错则回滚。供 repository 内部使用,
    业务层不应直接进入此上下文。"""
    get_engine()  # 保证 _SessionLocal 已就绪
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
