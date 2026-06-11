"""ORM 模型: 仅 InterviewSession / EvaluationReport 两张表。

设计取舍(Sprint 1):
- 嵌套结构(history / answers / content_scores / performance_observations) 走 JSONB,
  不在本 sprint 拆子表。理由: 我们当前没有按嵌套字段查询的需求,
  pydantic.model_dump <-> JSONB 来回最低成本。
- 顶层可索引/可统计字段(status, job_id, overall, needs_human_review) 提出来当列,
  方便后续做仪表盘与筛选, 也为将来切表预留出口。
- 主键直接复用 schemas 里生成的 hex uuid 字符串, 避免业务层与 DB 层 id 不一致。
- created_at / updated_at 由 server_default = now() 控制, 不让业务层操心时区。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class InterviewSessionORM(Base):
    """对应 schemas.InterviewSession。一行 = 一次面试的完整状态快照。"""
    __tablename__ = "interview_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 嵌套结构整体 JSONB; 反序列化时交给 pydantic 校验
    history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    answers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    report: Mapped["EvaluationReportORM | None"] = relationship(
        back_populates="session", uselist=False
    )


class EvaluationReportORM(Base):
    """对应 schemas.EvaluationReport。一行 = 一次面试的最终评估结果。"""
    __tablename__ = "evaluation_reports"

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("interview_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # 一次面试一份报告
        index=True,
    )

    content_scores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    performance_observations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    overall: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False, default="")
    # 默认 True; 合规约束(见 ARCHITECTURE.md §7): AI 报告不直接作为唯一决定依据
    needs_human_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[InterviewSessionORM] = relationship(back_populates="report")
