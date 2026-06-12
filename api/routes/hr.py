"""HR / admin 端 API —— Sprint 5-2。

挂在 /hr/* 前缀下, 全部要求 require_hr_user (Bearer JWT)。

端点:
- GET   /hr/jobs                            列所有职位
- GET   /hr/jobs/{job_id}/candidates        列某职位的候选人 + 状态
- GET   /hr/reports/{report_id}             HR 视角的报告详情
- PATCH /hr/reports/{report_id}/review      HR 提交复核结论

候选人端 (Sprint 4) 的端点继续保留在 /jobs/* /interviews/*, 走候选人
candidate_id 路径 soft-auth。两层路由互不影响。

为什么不复用候选人端的 GET /jobs/{id}/candidates/{cid}/plan 等:
- 候选人只能看自己的 (URL 含 candidate_id 即"知道才能看")
- HR 要看"某职位下所有候选人列表", 是不同的查询语义, 干脆放新前缀

review_decision 在 PATCH 处校验合法值, 不让前端塞奇怪字符串。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.schemas import CandidateWithStatus, ReviewSubmit
from src import auth, db
from src.schemas import (
    EvaluationReport,
    JobContext,
    ReviewDecision,
    ReviewRecord,
    User,
)

router = APIRouter(prefix="/hr", tags=["hr"])

HrUser = Annotated[User, Depends(auth.require_hr_user)]


@router.get("/jobs", response_model=list[JobContext])
def list_jobs(_user: HrUser) -> list[JobContext]:
    """列所有职位 (created_at 倒序)。"""
    return db.list_jobs()


@router.get(
    "/jobs/{job_id}/candidates",
    response_model=list[CandidateWithStatus],
)
def list_candidates_for_job(
    job_id: str, _user: HrUser,
) -> list[CandidateWithStatus]:
    """列某职位的候选人 + 当前状态 (plan_pending / ready / completed / reviewed)。
    job 不存在时也返空列表 (无意义的 job_id 不算错), HR 端 UI 应当先调
    GET /hr/jobs 选择已知 job。"""
    rows = db.list_candidates_with_status_for_job(job_id)
    return [CandidateWithStatus.model_validate(r) for r in rows]


@router.get("/reports/{report_id}", response_model=EvaluationReport)
def get_report(report_id: str, _user: HrUser) -> EvaluationReport:
    """HR 视角的报告详情。
    与候选人端 GET /interviews/{id}/report 区别:
    - 该端点 by session_id, 隐式触发 finalize, 任何人可调
    - 本端点 by report_id, 不触发 finalize, 仅 HR 可调
    HR 拿 report_id 来源是 GET /hr/jobs/{j}/candidates 的列表 (含 report_id)。"""
    report = db.load_report(report_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"report {report_id} 不存在",
        )
    return report


@router.get("/reports/{report_id}/review", response_model=ReviewRecord | None)
def get_review(report_id: str, _user: HrUser) -> ReviewRecord | None:
    """查询当前复核记录 (null 表示还没复核过)。"""
    return db.load_review_for_report(report_id)


@router.patch(
    "/reports/{report_id}/review",
    response_model=ReviewRecord,
)
def submit_review(
    report_id: str, body: ReviewSubmit, user: HrUser,
) -> ReviewRecord:
    """HR 提交复核结论。reviewer_id 由 server 从 JWT 取, 客户端不传。
    重复 PATCH 同 report_id 会"覆盖"为新 reviewer + 新结论 (MVP 不做版本历史)。"""
    # 1) report 必须存在
    if db.load_report(report_id) is None:
        raise HTTPException(
            status_code=404, detail=f"report {report_id} 不存在, 无法复核",
        )
    # 2) decision 必须是合法 enum 值
    try:
        decision = ReviewDecision(body.decision)
    except ValueError:
        valid = ", ".join(d.value for d in ReviewDecision)
        raise HTTPException(
            status_code=422,
            detail=f"decision 必须是 {valid} 之一, 实际收到 {body.decision!r}",
        )
    # 3) 构造 + 落库
    review = ReviewRecord(
        report_id=report_id,
        reviewer_id=user.user_id,
        comments=body.comments,
        dimension_overrides=[
            {
                "competency_id": d.get("competency_id", ""),
                "score": float(d.get("score", 0.0)),
                "note": d.get("note", ""),
            }
            for d in body.dimension_overrides
        ],
        decision=decision,
    )
    db.save_review_record(review)
    return review
