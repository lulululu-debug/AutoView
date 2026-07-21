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

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.schemas import CandidateWithStatus, ResumeChunk, ReviewSubmit
from src import auth, db, vector_store
from src.agents import planner
from src.schemas import (
    EvaluationReport,
    InterviewPlan,
    InterviewSession,
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


@router.get(
    "/jobs/{job_id}/candidates/{candidate_id}/plan",
    response_model=InterviewPlan,
)
def get_hr_plan(job_id: str, candidate_id: str, _user: HrUser) -> InterviewPlan:
    """HR 视角的 plan: 与候选人端同一份数据, 但**保留 trace**
    (出题过程审计: topic 匹配明细 + 每题来源路径)。候选人端接口剥 trace,
    这里不剥 —— HR 是 trace 的目标读者 (Sprint E)。"""
    candidate = db.load_candidate(candidate_id)
    if candidate is None or candidate.job_id != job_id:
        raise HTTPException(
            status_code=404,
            detail=f"candidate {candidate_id} 不在 job {job_id} 下",
        )
    plan = db.load_latest_plan_for_candidate(candidate_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan 尚未生成")
    return plan


def _dev_plan_preview_enabled() -> bool:
    return os.environ.get("DEV_PLAN_PREVIEW", "").lower() in ("1", "true", "yes")


@router.get(
    "/jobs/{job_id}/candidates/{candidate_id}/plan-preview",
    response_model=InterviewPlan,
)
def get_plan_preview(
    job_id: str, candidate_id: str, _user: HrUser,
) -> InterviewPlan:
    """开发者测试端点: 不答题预览全部题目 (含 lazy project 题)。

    knowledge / scenario / self_intro 题在 plan 阶段已生成, 直接返回;
    lazy project 题用 intro_text="" 在**内存里** resolve 一份预览。

    硬约束:
    - resolve 结果**绝不写回** Redis / PG —— 正式面试进 project stage 时
      必须带真实 intro_text 重新生成, 否则 lazy generation 设计失效。
      因此预览的 project 题与正式面试的题面不会逐字一致, 仅示意深挖方向。
    - 双门控: require_hr_user + DEV_PLAN_PREVIEW env, 默认关闭,
      候选人端永远接触不到本端点 (防泄题)。
    """
    if not _dev_plan_preview_enabled():
        raise HTTPException(
            status_code=403,
            detail="plan 预览未开启 (需要环境变量 DEV_PLAN_PREVIEW=true)",
        )

    candidate = db.load_candidate(candidate_id)
    if candidate is None or candidate.job_id != job_id:
        raise HTTPException(
            status_code=404,
            detail=f"candidate {candidate_id} 不在 job {job_id} 下",
        )
    plan = db.load_latest_plan_for_candidate(candidate_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan 尚未生成, 请稍后重试")
    job = db.load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} 不存在")

    return planner.resolve_lazy_questions(plan, job, candidate, intro_text="")


@router.get(
    "/candidates/{candidate_id}/resume-chunks",
    response_model=list[ResumeChunk],
)
def list_resume_chunks(candidate_id: str, _user: HrUser) -> list[ResumeChunk]:
    """Sprint E 出题过程视图: 该候选人 Resume 在 Milvus 里的全部切片。
    project 题的 source_chunk_ids 指向这些 document_id, HR 端对照展示
    「哪段简历催生了哪道深挖题」。Milvus 未配置 / 无切片时返空列表。"""
    if db.load_candidate(candidate_id) is None:
        raise HTTPException(
            status_code=404, detail=f"candidate {candidate_id} 不存在",
        )
    try:
        rows = vector_store.list_documents(
            kind=vector_store.DOC_KIND_RESUME, source_id=candidate_id,
        )
    except vector_store.MilvusNotConfigured:
        return []
    except Exception:
        # 观测性接口不应因 Milvus 抖动报 500, 返空让 UI 降级展示
        return []
    return [
        ResumeChunk(
            document_id=r["document_id"],
            chunk_index=r.get("chunk_index", 0),
            text=r.get("text", ""),
        )
        for r in rows
    ]


@router.get(
    "/jobs/{job_id}/candidates/{candidate_id}",
    response_model=CandidateWithStatus,
)
def get_candidate(
    job_id: str, candidate_id: str, _user: HrUser,
) -> CandidateWithStatus:
    """HR 单候选人详情 + 进度状态. Sprint 5-5 详情页用。
    校验 candidate 确实在该 job 下, 防跨 job 偷看 (与候选人端
    GET /jobs/{j}/candidates/{c} 同款护栏)。"""
    row = db.get_candidate_with_status(candidate_id)
    if row is None or row["job_id"] != job_id:
        raise HTTPException(
            status_code=404,
            detail=f"candidate {candidate_id} 不在 job {job_id} 下",
        )
    return CandidateWithStatus.model_validate(row)


@router.get("/sessions/{session_id}", response_model=InterviewSession)
def get_session(session_id: str, _user: HrUser) -> InterviewSession:
    """HR 视角的完整 session: 含 history / answers / intro_text / assessments。
    Sprint 5.7: HR 阶段视图 "面试过程" 区域用 assessments 字段展示每题的
    missing_signals / strengths / concerns / followup_goal。

    注: 不暴露 sufficiency / confidence 数字给前端? 这里 API 把 session 全量返回,
    合规约束在前端 UI 层守 —— AssessmentView 不渲染这两个字段, 不在 schema 层
    剥离, 让"内部诊断" 接口 (future) 仍能拿到完整数据。"""
    session = db.load_session(session_id)
    if session is None:
        # session 已 finalize 归档 PG, db.load_session 仍能命中; 真不存在才 404
        raise HTTPException(
            status_code=404, detail=f"session {session_id} 不存在",
        )
    return session


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
