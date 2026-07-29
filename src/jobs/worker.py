"""RQ worker 入口 —— Sprint 9 task 2。

    python -m src.jobs.worker            # 前台跑一个 worker
    JOBS_WORKER_BURST=1 python -m src.jobs.worker   # 处理完队列即退 (测试用)

多开几个进程就是多个并发 worker。需要 .env 里的 REDIS_URL / POSTGRES_URL /
MILVUS_* / OPENAI_API_KEY (Planner 真出题)。
"""
from __future__ import annotations

import logging
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s][%(name)s] %(message)s",
)


def main() -> None:
    import sys

    from rq import Queue, SimpleWorker, Worker

    from src.jobs import QUEUE_NAME, raw_connection

    conn = raw_connection()
    # macOS: fork 型 work horse 在 gRPC (Milvus server client)/ObjC 运行时下
    # 会 SIGABRT (实测 ret_val=6), 用 SimpleWorker (同进程不 fork);
    # Linux 部署保持 fork 型 Worker (任务超时靠 horse 强杀)。
    # JOBS_WORKER_SIMPLE=1 可在任意平台强制 SimpleWorker。
    simple = sys.platform == "darwin" or (
        os.environ.get("JOBS_WORKER_SIMPLE", "").lower() in ("1", "true")
    )
    cls = SimpleWorker if simple else Worker
    worker = cls([Queue(QUEUE_NAME, connection=conn)], connection=conn)
    burst = os.environ.get("JOBS_WORKER_BURST", "").lower() in ("1", "true")
    worker.work(burst=burst)


if __name__ == "__main__":
    main()
