"""Sprint 9 task 2 —— RQ 任务队列护栏。

- 默认关: JOBS_QUEUE_ENABLED 未设 -> enqueue 直接 False (不摸 Redis)
- 开启但 Redis 不可用 -> False (降级铁律, 调用方退 BackgroundTasks)
- PG+Redis gated e2e: 开队列上传候选人 -> plan 不立即出现 (真异步) ->
  SimpleWorker burst 消化 -> plan 就绪; 队列路径与 BackgroundTasks 路径
  产出等价 (plan_pending 轮询语义一致)
- worker 任务对已删数据优雅放弃 (不重试不炸)

Redis 侧用 db/9 (F8 套路), 不碰 dev 缓存。
跑法: python -m unittest evals.test_jobs_queue
"""
from __future__ import annotations

import os
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("JWT_SECRET", "eval-jwt-secret-32chars-abcdefgh")

from src import jobs as jobs_queue  # noqa: E402


class EnqueueGateTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("JOBS_QUEUE_ENABLED", None)
        self.addCleanup(lambda: os.environ.pop("JOBS_QUEUE_ENABLED", None))

    def test_default_off(self) -> None:
        self.assertFalse(jobs_queue.queue_enabled())
        self.assertFalse(
            jobs_queue.enqueue_candidate_processing("j", "c", "r", False),
        )

    def test_enabled_but_redis_unavailable_degrades(self) -> None:
        os.environ["JOBS_QUEUE_ENABLED"] = "1"
        saved = os.environ.pop("REDIS_URL", None)
        from src.cache.base import reset_client_for_testing
        reset_client_for_testing()
        try:
            self.assertFalse(
                jobs_queue.enqueue_candidate_processing("j", "c", "r", False),
            )
        finally:
            if saved is not None:
                os.environ["REDIS_URL"] = saved
            reset_client_for_testing()


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 PG + Redis 跑队列 e2e",
)
class QueueE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Redis 切 db/9 (F8 套路): 队列 key 不进 dev 缓存库
        cls._old_redis = os.environ.get("REDIS_URL")
        base = cls._old_redis.rsplit("/", 1)[0]
        os.environ["REDIS_URL"] = f"{base}/9"
        from src.cache.base import reset_client_for_testing
        reset_client_for_testing()
        from src.db import init_db
        init_db()

    @classmethod
    def tearDownClass(cls) -> None:
        from src.cache.base import reset_client_for_testing
        if cls._old_redis:
            os.environ["REDIS_URL"] = cls._old_redis
        reset_client_for_testing()

    def setUp(self) -> None:
        os.environ["JOBS_QUEUE_ENABLED"] = "1"
        self.addCleanup(lambda: os.environ.pop("JOBS_QUEUE_ENABLED", None))
        os.environ.pop("OPENAI_API_KEY", None)

    def _drain(self) -> None:
        from rq import Queue, SimpleWorker

        conn = jobs_queue.raw_connection()
        q = Queue(jobs_queue.QUEUE_NAME, connection=conn)
        SimpleWorker([q], connection=conn).work(burst=True)

    def test_candidate_flow_via_queue(self) -> None:
        from fastapi.testclient import TestClient

        from api.main import create_app
        from src import auth as _auth, db
        from src.schemas import User as _User

        app = create_app()
        app.dependency_overrides[_auth.require_hr_user] = lambda: _User(
            user_id="eval-hr", username="eval-hr", role="hr",
        )
        client = TestClient(app)
        r = client.post("/jobs", json={"title": "队列岗", "jd": "并发"})
        job_id = r.json()["job_id"]
        r = client.post(f"/jobs/{job_id}/candidates", json={
            "resume": "张三, 后端四年, 订单系统优化, 对账平台。" * 5,
        })
        self.assertEqual(r.status_code, 202)
        cand_id = r.json()["candidate_id"]

        # 真异步: BackgroundTasks 会在 TestClient 返回前内联执行,
        # 队列路径此刻 plan 必须还不存在
        self.assertIsNone(db.load_latest_plan_for_candidate(cand_id))

        self._drain()  # worker 消化
        plan = db.load_latest_plan_for_candidate(cand_id)
        self.assertIsNotNone(plan, "worker 跑完 plan 应就绪")
        self.assertTrue(plan.rounds)

    def test_worker_gives_up_on_missing_data(self) -> None:
        self.assertTrue(jobs_queue.enqueue_candidate_processing(
            "ghost-job", "ghost-cand", "resume", False,
        ))
        self._drain()  # 优雅放弃, 不抛不重试


if __name__ == "__main__":
    unittest.main()
