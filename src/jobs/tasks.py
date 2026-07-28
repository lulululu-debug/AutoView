"""RQ worker 侧任务实现 —— Sprint 9 task 2。

与 api/routes/candidates.py 的 BackgroundTasks 路径**同一套语义**
(顺序: 分段 -> Milvus ingest -> Planner; ingest 在 plan 之前, Planner 才有
Resume RAG 可召回), 只是搬进独立进程。job/candidate 从 PG 反查而不是
序列化传递 —— enqueue 时两者已落库, worker 拿到的是持久真相。

失败语义: 分段/ingest 各自吞异常 (下游有 fallback); Planner 失败**抛出**
让 RQ 重试 (与 BackgroundTasks 吞掉不同 —— 队列有重试能力就该用)。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def process_candidate(
    job_id: str, candidate_id: str, resume_text: str, skip_segmentation: bool,
) -> None:
    from src import cache, db, ingestion
    from src.agents import planner

    job = db.load_job(job_id)
    candidate = db.load_candidate(candidate_id)
    if job is None or candidate is None:
        # 数据被删 (测试清库 / 候选人撤销): 不重试, 静默结束
        log.warning(
            "process_candidate: job/candidate 不在 PG, 放弃 (job=%s cand=%s)",
            job_id, candidate_id,
        )
        return

    if not skip_segmentation:
        try:
            sections = ingestion.segment_resume(resume_text)
            db.save_candidate_sections(candidate_id, sections)
            # 后续 plan 用最新分段
            candidate = db.load_candidate(candidate_id) or candidate
        except Exception:
            log.exception("worker segment_resume 失败 (cand=%s)", candidate_id)

    try:
        n = ingestion.ingest_resume(candidate_id, resume_text)
        log.info("worker ingested resume: cand=%s chunks=%d", candidate_id, n)
    except Exception:
        log.exception("worker ingest_resume 失败 (cand=%s)", candidate_id)

    # Planner 失败抛出 -> RQ 按退避重试
    plan = planner.plan(job, candidate)
    db.save_plan(plan, candidate_id=candidate.candidate_id)
    cache.save_plan(plan)
    log.info("worker planned: cand=%s plan=%s", candidate_id, plan.plan_id)
