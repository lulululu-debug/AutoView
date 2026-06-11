"""Candidate 资源端点 + 异步 Planner 触发 —— Sprint 2-4。

为什么走 BackgroundTasks 而不是同步 200:
- Planner 在 LLM 真接通后会跑 4+ 次调用, 最坏几秒/十几秒, 同步会让上传接口
  慢得没法用。
- 用 FastAPI 内置 BackgroundTasks 是 Sprint 2 范围内的最小可行方案: 不引新依赖,
  随响应体一起注册, 由 ASGI 在返回响应后执行。
- 路由签名只暴露"异步触发"语义, 等 Sprint 7 接 RQ/Celery 时换内部实现 ——
  路由对客户端的契约不动。
- 失败语义: 不重试, 失败只在 stderr 留日志(BackgroundTasks 异常不会传到客户端);
  客户端通过 GET .../plan 轮询, 一直 404 就是有问题, 看日志/重新上传 candidate。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api.schemas import CandidateCreate, CandidateCreated
from src import cache, db, ingestion
from src.agents import planner
from src.schemas import CandidateProfile, InterviewPlan, JobContext

log = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs/{job_id}/candidates", tags=["candidates"])


def _run_planner_in_background(job: JobContext, candidate: CandidateProfile) -> None:
    """后台任务: 跑 Planner -> 同时落 PG (db.save_plan, 持久) 与 Redis
    (cache.save_plan, 会话热路径用)。双写顺序: 先 PG 后 Redis, 让"持久"成为
    真理之源; 万一 Redis 写挂了, PG 仍能让 start_session 重新加载 plan 进 Redis。

    失败处理: 任何异常吞到日志, 不抛, 因为 ASGI 后台任务的异常不会回传给
    客户端。Sprint 2 阶段不引重试, 客户端轮询不到就重新上传 candidate。
    """
    try:
        plan = planner.plan(job, candidate)
        db.save_plan(plan, candidate_id=candidate.candidate_id)
        cache.save_plan(plan)
    except Exception:
        log.exception(
            "background planner failed: job=%s candidate=%s",
            job.job_id, candidate.candidate_id,
        )


def _ingest_resume_in_background(candidate_id: str, resume_text: str) -> None:
    """后台任务 (Sprint 3-4): 切 Resume -> embed -> 入 Milvus, 给 Planner 项目深挖
    RAG 召回用。失败仅日志, 与 Planner 并行跑, 互不阻塞。"""
    try:
        n = ingestion.ingest_resume(candidate_id, resume_text)
        log.info(
            "ingested resume: candidate=%s chunks=%d", candidate_id, n,
        )
    except Exception:
        log.exception(
            "background ingest_resume failed: candidate=%s", candidate_id,
        )


@router.post("", response_model=CandidateCreated, status_code=202)
def create_candidate(
    job_id: str,
    body: CandidateCreate,
    background_tasks: BackgroundTasks,
) -> CandidateCreated:
    """上传候选人 Resume, 立刻保存 candidate 并异步触发 Planner。

    202 Accepted: 候选人入库, Planner 在后台跑。
    客户端轮询 GET /jobs/{job_id}/candidates/{candidate_id}/plan 看是否就绪。
    """
    job = db.load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} 不存在")

    candidate = CandidateProfile(
        job_id=job_id,
        resume=body.resume,
        projects=body.projects,
    )
    db.save_candidate(candidate)

    background_tasks.add_task(_run_planner_in_background, job, candidate)
    background_tasks.add_task(
        _ingest_resume_in_background, candidate.candidate_id, candidate.resume,
    )

    return CandidateCreated(
        candidate_id=candidate.candidate_id,
        job_id=job_id,
        plan_pending=True,
    )


@router.get("/{candidate_id}/plan", response_model=InterviewPlan)
def get_candidate_plan(job_id: str, candidate_id: str) -> InterviewPlan:
    """轮询: plan 已生成则返回, 否则 404。"""
    candidate = db.load_candidate(candidate_id)
    if candidate is None or candidate.job_id != job_id:
        raise HTTPException(
            status_code=404,
            detail=f"candidate {candidate_id} 不在 job {job_id} 下",
        )

    plan = db.load_latest_plan_for_candidate(candidate_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan 尚未生成, 请稍后重试")
    return plan
