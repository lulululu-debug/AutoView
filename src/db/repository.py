"""schemas(pydantic) <-> ORM 的转换与读写接口。

业务层(orchestrator / agents / 未来的 api)只用本模块的 save_*/load_*。
ORM 类型不向业务层暴露 —— 返回值统一是 src.schemas 里的 pydantic 模型。

幂等约定: save_session/save_report 用 session_id/report_id 做 upsert(merge)。
"""
from __future__ import annotations

from typing import Optional

from src.db.base import session_scope
from src.db.models import EvaluationReportORM, InterviewSessionORM
from src.schemas import EvaluationReport, InterviewSession


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
            }
        )
