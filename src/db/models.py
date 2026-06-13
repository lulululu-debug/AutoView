"""ORM 模型。

Sprint 1: InterviewSession / EvaluationReport
Sprint 2: + Job / Candidate / InterviewPlan, 让 API 端的"上传 JD -> 上传 Resume
           -> 触发 Planner"完整链路有持久化承载。

通用设计取舍:
- 嵌套结构(history / answers / content_scores / performance_observations /
  plan_data 等) 走 JSONB, 不在 sprint 阶段拆子表。理由: 没有按嵌套字段查询的需求,
  pydantic.model_dump <-> JSONB 来回最低成本。
- 顶层可索引/可统计字段(status, job_id, overall, needs_human_review) 提出来当列,
  方便后续做仪表盘与筛选, 也为将来切表预留出口。
- 主键直接复用 schemas 里生成的 hex uuid 字符串, 避免业务层与 DB 层 id 不一致。
- created_at / updated_at 由 server_default=now() / onupdate=now() 控制,
  不让业务层操心时区。
- FK 用 ondelete="RESTRICT": HR 误删职位时不静默级联干掉 candidate / plan / session,
  审计场景下"硬挡住"比"自动清"更安全。真要清的话, 后续做 soft delete + 显式归档流程。

interview_sessions.plan_id 暂不加 FK 到 interview_plans.plan_id:
- Sprint 2-3 才把 plan 真的写进 PG, 在那之前老的 session 行 plan_id 不在 plans 表中,
  现在加 FK 会让旧数据违约。等 Sprint 2-3 接通后再补 FK 约束。
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


class ReviewRecordORM(Base):
    """HR 对 EvaluationReport 的复核记录 (Sprint 5-2)。
    PK 单独用 record_id (而非 report_id), 给未来"多次复核 / 版本历史"留口子;
    当前 MVP 通过 PATCH 同 report_id 覆盖, 一份 report 实际只有一条 review,
    但 schema 允许多条。"""
    __tablename__ = "review_records"

    record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("evaluation_reports.report_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    comments: Mapped[str] = mapped_column(String, nullable=False, default="")
    dimension_overrides: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)

    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class UserORM(Base):
    """HR / admin 用户。Sprint 5-1 起接 JWT 鉴权。
    hashed_password 永远不向 pydantic User 暴露, 也不进任何响应体。"""
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SeedQuestionORM(Base):
    """题库种子题。Sprint 3 起 Planner 走"召回 + LLM 精修", 题源在这里。

    与 Milvus questions collection 的关系:
    - 本表是真理之源, Milvus 仅作检索副本; Milvus 文件丢了能从本表重建。
    - 写入顺序: PG -> embed -> Milvus, 顺序保证 PG 一致, Milvus 写挂不影响真理。

    question_id 用内容哈希: 脚本重跑不重复。

    Sprint 5.5 加 category 列: 区分 knowledge / scenario;
    server_default='knowledge' 让旧库 ALTER 加列时历史行落到 knowledge,
    与 pydantic 默认一致。
    """
    __tablename__ = "seed_questions"

    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role_family: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    competency: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    text: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="llm_generated"
    )
    category: Mapped[str] = mapped_column(
        String(32), nullable=False,
        default="knowledge", server_default="knowledge",
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class JobORM(Base):
    """对应 schemas.JobContext。HR 创建职位时落表。
    Sprint 5.5 加 track 列; server_default="lateral" 保证旧库 ALTER 加列时
    历史行自动拿到 lateral, 与 pydantic 默认值一致。"""
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    jd: Mapped[str] = mapped_column(String, nullable=False)
    requirements: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    company_materials: Mapped[str] = mapped_column(String, nullable=False, default="")
    track: Mapped[str] = mapped_column(
        String(16), nullable=False, default="lateral", server_default="lateral",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    candidates: Mapped[list["CandidateORM"]] = relationship(back_populates="job")


class CandidateORM(Base):
    """对应 schemas.CandidateProfile。候选人上传 Resume 时落表, 强制关联到一个 job。"""
    __tablename__ = "candidates"

    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("jobs.job_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    resume: Mapped[str] = mapped_column(String, nullable=False)
    projects: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    job: Mapped[JobORM] = relationship(back_populates="candidates")
    plans: Mapped[list["InterviewPlanORM"]] = relationship(back_populates="candidate")


class InterviewPlanORM(Base):
    """对应 schemas.InterviewPlan。Planner 跑完后落表, HR 端可看, 面试会话从中读题。

    不强制 unique(candidate_id): 允许同一候选人有多个版本的 plan(HR 重跑 Planner)。
    plan.plan_id 才是定位主键, 面试会话靠 plan_id 锁定使用的版本。
    """
    __tablename__ = "interview_plans"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("candidates.candidate_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # 完整 InterviewPlan (含 rounds/competencies/questions) 整体 JSONB
    plan_data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    candidate: Mapped[CandidateORM] = relationship(back_populates="plans")


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
    # Sprint 5.5: 候选人 self_intro 阶段回答全文, 供 project lazy gen 使用;
    # server_default="" 保证旧库 ALTER 加列时历史 session 行不违约。
    intro_text: Mapped[str] = mapped_column(
        String, nullable=False, default="", server_default="",
    )

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
    # Sprint 3-7: Evaluator RAG 溯源, JD/公司资料 chunk id 列表
    rag_context_chunk_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[InterviewSessionORM] = relationship(back_populates="report")
