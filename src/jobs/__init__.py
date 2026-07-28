"""RQ 任务队列 —— Sprint 9 task 2。

真异步后台: Redis broker + 独立 worker 进程 (python -m src.jobs.worker),
带重试、进程崩溃不丢任务 —— 替代 FastAPI BackgroundTasks 的
"进程内串行假异步" (CLAUDE.md Sprint 7 预留的路线)。

**默认关** (JOBS_QUEUE_ENABLED): dev / eval 里 TestClient 依赖
BackgroundTasks 内联执行 (不起 worker 也能拿到 plan), 生产才开。
降级铁律: 开了但 rq 未装 / Redis 不可用 / enqueue 抛错 -> 返回 False,
调用方退回 BackgroundTasks —— 任何情况下候选人上传链路不挂,
plan_pending 轮询语义不变。
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

QUEUE_NAME = "interview-jobs"
# 重试退避: 三次, 10s/30s/60s (Planner 失败多为 LLM 瞬时抖动)
_RETRY_INTERVALS = [10, 30, 60]
_JOB_TIMEOUT_SECONDS = 600


def queue_enabled() -> bool:
    return os.environ.get("JOBS_QUEUE_ENABLED", "").lower() in ("1", "true", "yes")


def raw_connection():
    """RQ 专用 raw Redis 连接 (decode_responses=False)。

    不复用 src.cache.get_redis: 业务 client 开了 decode_responses=True
    (存 JSON 字符串), 而 RQ 任务体是 pickle 二进制, 用解码连接取任务会
    UnicodeDecodeError (实测坐实)。REDIS_URL 未配置抛 KeyError 由调用方
    的 try/except 兜住。"""
    import redis as _redis

    return _redis.Redis.from_url(os.environ["REDIS_URL"])


def enqueue_candidate_processing(
    job_id: str, candidate_id: str, resume_text: str, skip_segmentation: bool,
) -> bool:
    """入队候选人处理 (ingest + plan)。成功 True; 任何原因失败 False
    (调用方退 BackgroundTasks)。"""
    if not queue_enabled():
        return False
    try:
        from rq import Queue, Retry

        q = Queue(QUEUE_NAME, connection=raw_connection())
        q.enqueue(
            "src.jobs.tasks.process_candidate",
            job_id, candidate_id, resume_text, skip_segmentation,
            retry=Retry(max=len(_RETRY_INTERVALS), interval=_RETRY_INTERVALS),
            job_timeout=_JOB_TIMEOUT_SECONDS,
        )
        log.info(
            "enqueued candidate processing: job=%s candidate=%s",
            job_id, candidate_id,
        )
        return True
    except Exception:
        log.exception(
            "enqueue 失败, 退回 BackgroundTasks (candidate=%s)", candidate_id,
        )
        return False
