"""PG repository round-trip eval —— Sprint 2-2。

跑前提: POSTGRES_URL 已配置 + Postgres 在跑。否则全部 skip。
本 eval 验证:
- Job/Candidate/InterviewPlan 都能 round-trip 而不丢字段
- save_candidate 在缺 job_id 时显式拒绝(API 误用的护栏)
- FK 阻挡: candidate 引用不存在的 job_id 会被 PG 拒绝
- 跑前自动 init_db, 让冷机器也能直接 unittest 而无须手动建表

跑法:
    POSTGRES_URL=... python -m unittest evals.test_repository
"""
from __future__ import annotations

import os
import unittest
import uuid


def _has_postgres() -> bool:
    return bool(os.environ.get("POSTGRES_URL"))


@unittest.skipUnless(_has_postgres(), "需要 POSTGRES_URL")
class JobRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.db import init_db
        init_db()

    def test_save_load_job(self):
        from src.db import load_job, save_job
        from src.schemas import JobContext

        job = JobContext(
            title="后端工程师",
            jd="负责核心交易系统",
            requirements=["分布式", "数据库"],
            company_materials="一家交易公司",
        )
        save_job(job)
        loaded = load_job(job.job_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.job_id, job.job_id)
        self.assertEqual(loaded.title, job.title)
        self.assertEqual(loaded.requirements, ["分布式", "数据库"])
        self.assertEqual(loaded.company_materials, "一家交易公司")

    def test_load_missing_returns_none(self):
        from src.db import load_job
        self.assertIsNone(load_job("does-not-exist"))

    def test_upsert_overwrites(self):
        from src.db import load_job, save_job
        from src.schemas import JobContext
        j = JobContext(title="t1", jd="x", requirements=["a"])
        save_job(j)
        j2 = JobContext(job_id=j.job_id, title="t2", jd="y", requirements=["b"])
        save_job(j2)
        loaded = load_job(j.job_id)
        self.assertEqual(loaded.title, "t2")
        self.assertEqual(loaded.requirements, ["b"])


@unittest.skipUnless(_has_postgres(), "需要 POSTGRES_URL")
class CandidateRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.db import init_db, save_job
        from src.schemas import JobContext
        init_db()
        cls.job = JobContext(title="测试岗位", jd="x", requirements=[])
        save_job(cls.job)

    def test_save_load_candidate(self):
        from src.db import load_candidate, save_candidate
        from src.schemas import CandidateProfile

        cand = CandidateProfile(
            job_id=self.job.job_id,
            resume="张三 / 后端 / 4 年",
            projects=["P99 优化", "对账中台"],
        )
        save_candidate(cand)
        loaded = load_candidate(cand.candidate_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.candidate_id, cand.candidate_id)
        self.assertEqual(loaded.job_id, self.job.job_id)
        self.assertEqual(loaded.projects, ["P99 优化", "对账中台"])

    def test_save_candidate_requires_job_id(self):
        """API 误用护栏: candidate 没绑 job 不可入库。"""
        from src.db import save_candidate
        from src.schemas import CandidateProfile
        cand = CandidateProfile(resume="r", projects=[])  # job_id=None
        with self.assertRaises(ValueError):
            save_candidate(cand)

    def test_fk_blocks_orphan_candidate(self):
        """job_id 指向不存在职位时, PG 应拒绝(FK 约束)。"""
        from sqlalchemy.exc import IntegrityError
        from src.db import save_candidate
        from src.schemas import CandidateProfile
        cand = CandidateProfile(
            job_id="ghost-job-" + uuid.uuid4().hex[:8],
            resume="r",
        )
        with self.assertRaises(IntegrityError):
            save_candidate(cand)


@unittest.skipUnless(_has_postgres(), "需要 POSTGRES_URL")
class InterviewPlanRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.agents.planner import plan as run_planner
        from src.db import init_db, save_candidate, save_job
        from src.schemas import CandidateProfile, JobContext
        init_db()
        cls.job = JobContext(title="计划测试岗位", jd="负责核心系统", requirements=[])
        save_job(cls.job)
        cls.cand = CandidateProfile(
            job_id=cls.job.job_id,
            resume="李四 / 4 年",
            projects=["A 项目", "B 项目"],
        )
        save_candidate(cls.cand)
        # planner 本身不接触 DB, 仅产 InterviewPlan
        cls.plan = run_planner(cls.job, cls.cand)

    def test_save_load_plan_preserves_structure(self):
        from src.db import load_plan, save_plan
        save_plan(self.plan, candidate_id=self.cand.candidate_id)
        loaded = load_plan(self.plan.plan_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.plan_id, self.plan.plan_id)
        self.assertEqual(loaded.job_id, self.job.job_id)
        # 结构保真: 轮次 / 维度 / 题目数量、ID 全对得上
        self.assertEqual(len(loaded.rounds), len(self.plan.rounds))
        for r_loaded, r_orig in zip(loaded.rounds, self.plan.rounds):
            self.assertEqual(
                [c.competency_id for c in r_loaded.competencies],
                [c.competency_id for c in r_orig.competencies],
            )
            self.assertEqual(
                [q.question_id for q in r_loaded.questions],
                [q.question_id for q in r_orig.questions],
            )

    def test_fk_blocks_plan_for_unknown_candidate(self):
        from sqlalchemy.exc import IntegrityError
        from src.db import save_plan
        with self.assertRaises(IntegrityError):
            save_plan(self.plan, candidate_id="ghost-cand-" + uuid.uuid4().hex[:8])


if __name__ == "__main__":
    unittest.main()
