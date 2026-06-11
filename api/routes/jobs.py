"""Job 资源端点 —— Sprint 2-3。

POST /jobs: HR 创建职位。job_id 由 server 生成, 客户端传过来的会被忽略。
"""
from __future__ import annotations

from fastapi import APIRouter

from api.schemas import JobCreate
from src import db
from src.schemas import JobContext

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobContext, status_code=201)
def create_job(body: JobCreate) -> JobContext:
    """创建职位: server 生成 job_id, 持久化到 PG, 回 JobContext。"""
    job = JobContext(
        title=body.title,
        jd=body.jd,
        requirements=body.requirements,
        company_materials=body.company_materials,
    )
    db.save_job(job)
    return job
