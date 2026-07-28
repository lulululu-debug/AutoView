"""Sprint 9 task 3 —— 并发承载护栏。

- per-session 锁: 互斥 / token 校验释放 / 过期自愈 (Redis gated)
- orchestrator: 锁被占时 submit/finalize 抛 SessionBusy (API 层 409)
- 池容量: PG engine 尊重 POSTGRES_POOL_SIZE / POSTGRES_MAX_OVERFLOW
- 小规模并发冒烟: 4 路并发面试零串扰 (大规模走 scripts/load_test.py)

跑法: python -m unittest evals.test_concurrency
"""
from __future__ import annotations

import os
import threading
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)


@unittest.skipUnless(os.environ.get("REDIS_URL"), "需要 Redis")
class SessionLockTests(unittest.TestCase):
    def setUp(self) -> None:
        from src import cache
        self.cache = cache

    def test_mutual_exclusion_and_release(self) -> None:
        t1 = self.cache.acquire_session_lock("lock-eval-1")
        self.assertIsNotNone(t1)
        self.assertIsNone(self.cache.acquire_session_lock("lock-eval-1"))
        self.cache.release_session_lock("lock-eval-1", t1)
        t2 = self.cache.acquire_session_lock("lock-eval-1")
        self.assertIsNotNone(t2)
        self.cache.release_session_lock("lock-eval-1", t2)

    def test_release_requires_own_token(self) -> None:
        t1 = self.cache.acquire_session_lock("lock-eval-2")
        self.cache.release_session_lock("lock-eval-2", "wrong-token")
        # 错 token 释放无效, 锁仍被占
        self.assertIsNone(self.cache.acquire_session_lock("lock-eval-2"))
        self.cache.release_session_lock("lock-eval-2", t1)


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 PG + Redis",
)
class OrchestratorBusyTests(unittest.TestCase):
    def setUp(self) -> None:
        from src import cache, orchestrator
        from src.db import init_db
        os.environ.pop("OPENAI_API_KEY", None)
        init_db()
        self.cache = cache
        self.orchestrator = orchestrator

    def test_submit_rejected_while_locked(self) -> None:
        from src.schemas import CandidateProfile, JobContext
        job = JobContext(title="并发岗", jd="x", track="campus")
        cand = CandidateProfile(resume="张三, 后端。" * 5)
        turn = self.orchestrator.start_session(job, cand)
        sid = turn.session_id

        token = self.cache.acquire_session_lock(sid)
        self.assertIsNotNone(token)
        try:
            with self.assertRaises(self.orchestrator.SessionBusy):
                self.orchestrator.submit_answer(sid, "并发提交")
            with self.assertRaises(self.orchestrator.SessionBusy):
                self.orchestrator.finalize(sid)
        finally:
            self.cache.release_session_lock(sid, token)
        # 释放后恢复正常
        out = self.orchestrator.submit_answer(sid, "正常提交")
        self.assertEqual(out.session_id, sid)

    def test_parallel_interviews_no_crosstalk(self) -> None:
        from src import db
        from src.schemas import CandidateProfile, JobContext
        results: list[tuple[str, str]] = []
        errors: list[str] = []

        def one(idx: int) -> None:
            marker = f"EVALMARK-{idx}"
            try:
                job = JobContext(title=f"并发-{idx}", jd="x", track="campus")
                db.save_job(job)
                cand = CandidateProfile(
                    job_id=job.job_id, resume=f"{marker} 简历。" * 5,
                )
                db.save_candidate(cand)
                turn = self.orchestrator.start_session(job, cand)
                sid = turn.session_id
                n = 0
                while not turn.done and n < 25:
                    turn = self.orchestrator.submit_answer(
                        sid, f"{marker} 第 {n} 答, 做过缓存优化。",
                    )
                    n += 1
                self.orchestrator.finalize(sid)
                results.append((sid, marker))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=one, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 4)
        for sid, marker in results:
            archived = db.load_session(sid)
            for a in archived.answers:
                self.assertIn(marker, a.text)
                self.assertEqual(a.text.count("EVALMARK-"), 1)


class PoolConfigTests(unittest.TestCase):
    def test_engine_respects_pool_env(self) -> None:
        os.environ["POSTGRES_POOL_SIZE"] = "7"
        os.environ["POSTGRES_MAX_OVERFLOW"] = "11"
        self.addCleanup(lambda: os.environ.pop("POSTGRES_POOL_SIZE", None))
        self.addCleanup(lambda: os.environ.pop("POSTGRES_MAX_OVERFLOW", None))
        saved = os.environ.get("POSTGRES_URL")
        os.environ["POSTGRES_URL"] = "postgresql+psycopg://u:p@localhost:5/x"
        try:
            from src.db.base import _build_engine
            eng = _build_engine()
            self.assertEqual(eng.pool.size(), 7)
            self.assertEqual(eng.pool._max_overflow, 11)
        finally:
            if saved is not None:
                os.environ["POSTGRES_URL"] = saved
            else:
                os.environ.pop("POSTGRES_URL", None)


if __name__ == "__main__":
    unittest.main()
