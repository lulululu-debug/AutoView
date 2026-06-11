"""Job 资源端点 —— Sprint 2-3 + Sprint 3-4。

POST /jobs: HR 创建职位 -> 同步落 PG -> 后台 ingest JD + 公司资料到 Milvus
            (Sprint 3-4)。job_id 由 server 生成, 客户端传过来的会被忽略。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks

from api.schemas import JobCreate
from src import db, ingestion
from src.schemas import JobContext

log = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


def _ingest_job_docs_in_background(
    job_id: str, jd_text: str, material_text: str,
) -> None:
    """后台任务: 切 JD 与 公司资料 -> embed -> 入 Milvus。
    任何异常吞到 stderr 日志, 不抛 (BG 任务异常不会传到客户端)。
    Sprint 7 接队列时换内部实现, 客户端契约不动。"""
    try:
        n_jd = ingestion.ingest_jd(job_id, jd_text)
        n_cm = ingestion.ingest_company_material(job_id, material_text)
        log.info(
            "ingested job docs: job=%s jd_chunks=%d cm_chunks=%d",
            job_id, n_jd, n_cm,
        )
    except Exception:
        log.exception("background ingest_job_docs failed: job=%s", job_id)


@router.post("", response_model=JobContext, status_code=201)
def create_job(body: JobCreate, background_tasks: BackgroundTasks) -> JobContext:
    """创建职位: server 生成 job_id, 持久化到 PG, 回 JobContext。
    JD + 公司资料的向量化在后台跑, 不阻塞响应。"""
    job = JobContext(
        title=body.title,
        jd=body.jd,
        requirements=body.requirements,
        company_materials=body.company_materials,
    )
    db.save_job(job)
    background_tasks.add_task(
        _ingest_job_docs_in_background,
        job.job_id, job.jd, job.company_materials,
    )
    return job
