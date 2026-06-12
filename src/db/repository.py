"""schemas(pydantic) <-> ORM 的转换与读写接口。

业务层(orchestrator / agents / 未来的 api)只用本模块的 save_*/load_*。
ORM 类型不向业务层暴露 —— 返回值统一是 src.schemas 里的 pydantic 模型。

幂等约定: save_session/save_report 用 session_id/report_id 做 upsert(merge)。
"""
from __future__ import annotations

from typing import Optional

from src.db.base import session_scope
from src.db.models import (
    CandidateORM,
    EvaluationReportORM,
    InterviewPlanORM,
    InterviewSessionORM,
    JobORM,
    SeedQuestionORM,
    UserORM,
)
from src.schemas import (
    CandidateProfile,
    EvaluationReport,
    InterviewPlan,
    InterviewSession,
    JobContext,
    SeedQuestion,
    User,
)


# ---------- User (Sprint 5-1) ----------

def save_user(
    *,
    user_id: str,
    username: str,
    hashed_password: str,
    role: str,
) -> None:
    """按 user_id upsert。hashed_password 必须已经过 bcrypt, 本层不再 hash。"""
    with session_scope() as s:
        s.merge(UserORM(
            user_id=user_id,
            username=username,
            hashed_password=hashed_password,
            role=role,
        ))


def load_user_by_username(username: str) -> Optional[tuple[User, str]]:
    """返回 (User pydantic, hashed_password) 或 None。
    密码 hash 仅给 verify_password 用, 上游不应进一步传播。"""
    with session_scope() as s:
        row = (
            s.query(UserORM)
            .filter(UserORM.username == username)
            .one_or_none()
        )
        if row is None:
            return None
        return (
            User(user_id=row.user_id, username=row.username, role=row.role),
            row.hashed_password,
        )


def load_user(user_id: str) -> Optional[User]:
    with session_scope() as s:
        row = s.get(UserORM, user_id)
        if row is None:
            return None
        return User(user_id=row.user_id, username=row.username, role=row.role)


# ---------- SeedQuestion (Sprint 3-3) ----------

def save_seed_question(question: SeedQuestion) -> None:
    """按 question_id upsert; 同内容 = 同 id, 脚本重跑安全。"""
    with session_scope() as s:
        s.merge(SeedQuestionORM(
            question_id=question.question_id,
            role_family=question.role_family,
            competency=question.competency,
            text=question.text,
            source=question.source,
        ))


def load_seed_question(question_id: str) -> Optional[SeedQuestion]:
    with session_scope() as s:
        row = s.get(SeedQuestionORM, question_id)
        if row is None:
            return None
        return SeedQuestion.model_validate({
            "question_id": row.question_id,
            "role_family": row.role_family,
            "competency": row.competency,
            "text": row.text,
            "source": row.source,
        })


def list_seed_questions(
    *,
    role_family: Optional[str] = None,
    competency: Optional[str] = None,
) -> list[SeedQuestion]:
    """按可选过滤列出题库; 不带过滤就是全表。"""
    with session_scope() as s:
        q = s.query(SeedQuestionORM)
        if role_family is not None:
            q = q.filter(SeedQuestionORM.role_family == role_family)
        if competency is not None:
            q = q.filter(SeedQuestionORM.competency == competency)
        return [
            SeedQuestion.model_validate({
                "question_id": r.question_id,
                "role_family": r.role_family,
                "competency": r.competency,
                "text": r.text,
                "source": r.source,
            })
            for r in q.all()
        ]


# ---------- Job ----------

def save_job(job: JobContext) -> None:
    """按 job_id upsert。"""
    payload = job.model_dump(mode="json")
    with session_scope() as s:
        s.merge(JobORM(
            job_id=payload["job_id"],
            title=payload["title"],
            jd=payload["jd"],
            requirements=payload["requirements"],
            company_materials=payload["company_materials"],
        ))


def load_job(job_id: str) -> Optional[JobContext]:
    with session_scope() as s:
        row = s.get(JobORM, job_id)
        if row is None:
            return None
        return JobContext.model_validate({
            "job_id": row.job_id,
            "title": row.title,
            "jd": row.jd,
            "requirements": row.requirements,
            "company_materials": row.company_materials,
        })


# ---------- Candidate ----------

def save_candidate(candidate: CandidateProfile) -> None:
    """按 candidate_id upsert; 要求 candidate.job_id 非空(由 API path param 注入)。"""
    if candidate.job_id is None:
        raise ValueError(
            "save_candidate 要求 candidate.job_id 非空; "
            "API 应当从路径参数 /jobs/{job_id}/candidates 注入"
        )
    payload = candidate.model_dump(mode="json")
    with session_scope() as s:
        s.merge(CandidateORM(
            candidate_id=payload["candidate_id"],
            job_id=payload["job_id"],
            resume=payload["resume"],
            projects=payload["projects"],
        ))


def load_candidate(candidate_id: str) -> Optional[CandidateProfile]:
    with session_scope() as s:
        row = s.get(CandidateORM, candidate_id)
        if row is None:
            return None
        return CandidateProfile.model_validate({
            "candidate_id": row.candidate_id,
            "job_id": row.job_id,
            "resume": row.resume,
            "projects": row.projects,
        })


# ---------- InterviewPlan (PG 归档版) ----------
# 注意: 与 src.cache.plan_store 区分 ——
#   db.save_plan / load_plan   持久化, HR 端可读, 走 PG
#   cache.save_plan / load_plan 进行中会话热缓存, 走 Redis, 有 TTL

def save_plan(plan: InterviewPlan, candidate_id: str) -> None:
    """plan 落 PG, 与某个 candidate_id 绑定。
    candidate_id 由调用方显式传入而非从 plan 上读, 是因为 schemas.InterviewPlan 自身
    不携带 candidate_id(plan 一旦生成对 candidate 不可知, 仅 job_id 在内)。"""
    with session_scope() as s:
        s.merge(InterviewPlanORM(
            plan_id=plan.plan_id,
            candidate_id=candidate_id,
            plan_data=plan.model_dump(mode="json"),
        ))


def load_plan(plan_id: str) -> Optional[InterviewPlan]:
    with session_scope() as s:
        row = s.get(InterviewPlanORM, plan_id)
        if row is None:
            return None
        return InterviewPlan.model_validate(row.plan_data)


def load_latest_plan_for_candidate(candidate_id: str) -> Optional[InterviewPlan]:
    """同一候选人允许多版 plan (HR 重跑 Planner), 这里返回最新生成的那个。
    用 created_at desc 而非 plan_id 排序: hex uuid 字典序无意义,
    时间戳才是"最近一次"的真正信号。"""
    with session_scope() as s:
        row = (
            s.query(InterviewPlanORM)
            .filter(InterviewPlanORM.candidate_id == candidate_id)
            .order_by(InterviewPlanORM.created_at.desc())
            .first()
        )
        if row is None:
            return None
        return InterviewPlan.model_validate(row.plan_data)


# ---------- InterviewSession ----------

def save_session(session: InterviewSession) -> None:
    """按 session_id upsert。提交后行内 timestamps 由 DB 维护。"""
    payload = session.model_dump(mode="json")
    with session_scope() as s:
        row = InterviewSessionORM(
            session_id=payload["session_id"],
            plan_id=payload["plan_id"],
            job_id=payload["job_id"],
            status=payload["status"],
            current_round=payload["current_round"],
            history=payload["history"],
            answers=payload["answers"],
        )
        s.merge(row)


def load_session(session_id: str) -> Optional[InterviewSession]:
    """按 session_id 读取; 不存在返回 None。"""
    with session_scope() as s:
        row = s.get(InterviewSessionORM, session_id)
        if row is None:
            return None
        return InterviewSession.model_validate(
            {
                "session_id": row.session_id,
                "plan_id": row.plan_id,
                "job_id": row.job_id,
                "status": row.status,
                "current_round": row.current_round,
                "history": row.history,
                "answers": row.answers,
            }
        )


# ---------- EvaluationReport ----------

def save_report(report: EvaluationReport) -> None:
    """按 report_id upsert。要求对应的 session 已先 save_session。"""
    payload = report.model_dump(mode="json")
    with session_scope() as s:
        row = EvaluationReportORM(
            report_id=payload["report_id"],
            session_id=payload["session_id"],
            content_scores=payload["content_scores"],
            performance_observations=payload["performance_observations"],
            overall=payload["overall"],
            summary=payload["summary"],
            needs_human_review=payload["needs_human_review"],
            rag_context_chunk_ids=payload["rag_context_chunk_ids"],
        )
        s.merge(row)


def load_report(report_id: str) -> Optional[EvaluationReport]:
    with session_scope() as s:
        row = s.get(EvaluationReportORM, report_id)
        if row is None:
            return None
        return EvaluationReport.model_validate(
            {
                "report_id": row.report_id,
                "session_id": row.session_id,
                "content_scores": row.content_scores,
                "performance_observations": row.performance_observations,
                "overall": row.overall,
                "summary": row.summary,
                "needs_human_review": row.needs_human_review,
                "rag_context_chunk_ids": row.rag_context_chunk_ids,
            }
        )


def load_report_by_session(session_id: str) -> Optional[EvaluationReport]:
    """便利方法: 按 session_id 反查最终报告(唯一约束保证至多一份)。"""
    with session_scope() as s:
        row = (
            s.query(EvaluationReportORM)
            .filter(EvaluationReportORM.session_id == session_id)
            .one_or_none()
        )
        if row is None:
            return None
        return EvaluationReport.model_validate(
            {
                "report_id": row.report_id,
                "session_id": row.session_id,
                "content_scores": row.content_scores,
                "performance_observations": row.performance_observations,
                "overall": row.overall,
                "summary": row.summary,
                "needs_human_review": row.needs_human_review,
                "rag_context_chunk_ids": row.rag_context_chunk_ids,
            }
        )
