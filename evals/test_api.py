"""API 层 eval —— Sprint 2 在跑期间逐步增长。

Sprint 2-1: /health 冒烟 (3 个)
Sprint 2-3: POST /jobs + 异常映射

设计:
- 端到端类(需 POSTGRES_URL): TestClient + 真 PG 验证落库
- 异常映射类(无需 infra): 用 unittest.mock 替换 db.save_job, 验证 503 映射
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import API_TITLE, API_VERSION, create_app


class HealthEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app())

    def test_health_returns_200(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)

    def test_health_payload(self):
        r = self.client.get("/health")
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], API_TITLE)
        self.assertEqual(body["version"], API_VERSION)

    def test_openapi_schema_available(self):
        """开发期 /docs 与 /openapi.json 应当可访问, 没有它 API 没法被 HR 端联调。"""
        r = self.client.get("/openapi.json")
        self.assertEqual(r.status_code, 200)
        schema = r.json()
        self.assertIn("/health", schema.get("paths", {}))


@unittest.skipUnless(os.environ.get("POSTGRES_URL"), "需要 POSTGRES_URL")
class CreateJobTests(unittest.TestCase):
    """POST /jobs: 创建 + 校验 + 真的入 PG。"""

    @classmethod
    def setUpClass(cls):
        from src.db import init_db
        init_db()
        cls.client = TestClient(create_app())

    def test_create_job_returns_201_and_persists(self):
        from src.db import load_job
        r = self.client.post("/jobs", json={
            "title": "后端工程师",
            "jd": "负责交易系统",
            "requirements": ["分布式", "数据库"],
            "company_materials": "x 公司",
        })
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["title"], "后端工程师")
        self.assertEqual(body["requirements"], ["分布式", "数据库"])
        self.assertEqual(body["company_materials"], "x 公司")
        self.assertIn("job_id", body)
        # 真落 PG 了
        loaded = load_job(body["job_id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.title, "后端工程师")

    def test_defaults_apply_when_optional_fields_missing(self):
        r = self.client.post("/jobs", json={"title": "t", "jd": "j"})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["requirements"], [])
        self.assertEqual(body["company_materials"], "")

    def test_validation_missing_title(self):
        r = self.client.post("/jobs", json={"jd": "j"})
        self.assertEqual(r.status_code, 422)

    def test_validation_empty_title(self):
        r = self.client.post("/jobs", json={"title": "", "jd": "j"})
        self.assertEqual(r.status_code, 422)

    def test_validation_empty_jd(self):
        r = self.client.post("/jobs", json={"title": "t", "jd": ""})
        self.assertEqual(r.status_code, 422)

    def test_client_cannot_inject_job_id(self):
        """安全: 客户端塞 job_id 不应生效, server 永远自己生成。"""
        r = self.client.post("/jobs", json={
            "title": "t", "jd": "j", "job_id": "client-forged",
        })
        self.assertEqual(r.status_code, 201)
        self.assertNotEqual(r.json()["job_id"], "client-forged")


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL",
)
class CreateCandidateTests(unittest.TestCase):
    """POST /jobs/{id}/candidates: 创建 + 异步触发 Planner + 双写 PG/Redis。

    注: FastAPI BackgroundTasks 在 TestClient 上下文中, 会在响应返回前同步执行完。
    所以 POST 之后我们能直接读到 plan, 不用 sleep。生产 ASGI 是真异步, 但语义一致。
    """

    @classmethod
    def setUpClass(cls):
        from src.db import init_db, save_job
        from src.schemas import JobContext
        init_db()
        cls.client = TestClient(create_app())
        cls.job = JobContext(title="后端工程师", jd="负责交易系统", requirements=["分布式"])
        save_job(cls.job)

    def test_create_candidate_returns_202(self):
        r = self.client.post(
            f"/jobs/{self.job.job_id}/candidates",
            json={"resume": "张三/后端/4年", "projects": ["P99 优化"]},
        )
        self.assertEqual(r.status_code, 202)
        body = r.json()
        self.assertIn("candidate_id", body)
        self.assertEqual(body["job_id"], self.job.job_id)
        self.assertTrue(body["plan_pending"])

    def test_create_candidate_persists_to_pg(self):
        from src.db import load_candidate
        r = self.client.post(
            f"/jobs/{self.job.job_id}/candidates",
            json={"resume": "李四/后端/5年", "projects": []},
        )
        cid = r.json()["candidate_id"]
        loaded = load_candidate(cid)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.job_id, self.job.job_id)
        self.assertEqual(loaded.resume, "李四/后端/5年")

    def test_background_planner_writes_plan_to_pg_and_redis(self):
        """双写校验: PG 是真理之源, Redis 是会话热路径。"""
        from src import cache
        from src.db import load_latest_plan_for_candidate
        r = self.client.post(
            f"/jobs/{self.job.job_id}/candidates",
            json={"resume": "王五/后端/3年", "projects": ["对账中台"]},
        )
        cid = r.json()["candidate_id"]
        # PG
        plan_pg = load_latest_plan_for_candidate(cid)
        self.assertIsNotNone(plan_pg, "Planner 应当已写 PG")
        self.assertEqual(len(plan_pg.rounds[0].questions), 4, "骨架: 4 题")
        # Redis
        plan_redis = cache.load_plan(plan_pg.plan_id)
        self.assertIsNotNone(plan_redis, "Planner 应当已写 Redis")
        self.assertEqual(plan_redis.plan_id, plan_pg.plan_id)

    def test_unknown_job_returns_404(self):
        r = self.client.post(
            "/jobs/ghost-job/candidates",
            json={"resume": "r", "projects": []},
        )
        self.assertEqual(r.status_code, 404)
        self.assertIn("不存在", r.json()["detail"])

    def test_validation_empty_resume(self):
        r = self.client.post(
            f"/jobs/{self.job.job_id}/candidates",
            json={"resume": "", "projects": []},
        )
        self.assertEqual(r.status_code, 422)

    def test_validation_missing_resume(self):
        r = self.client.post(
            f"/jobs/{self.job.job_id}/candidates",
            json={"projects": []},
        )
        self.assertEqual(r.status_code, 422)

    def test_get_plan_returns_plan_when_ready(self):
        post = self.client.post(
            f"/jobs/{self.job.job_id}/candidates",
            json={"resume": "赵六/后端/2年", "projects": []},
        )
        cid = post.json()["candidate_id"]
        r = self.client.get(f"/jobs/{self.job.job_id}/candidates/{cid}/plan")
        self.assertEqual(r.status_code, 200)
        plan = r.json()
        self.assertEqual(plan["job_id"], self.job.job_id)
        self.assertEqual(len(plan["rounds"]), 1)
        self.assertEqual(len(plan["rounds"][0]["questions"]), 4)

    def test_get_plan_404_when_candidate_not_in_job(self):
        """安全: candidate_id 不在该 job 下的, 不能跨 job 偷看 plan。"""
        from src.db import save_candidate, save_job
        from src.schemas import CandidateProfile, JobContext
        other_job = JobContext(title="其他岗位", jd="x")
        save_job(other_job)
        other_cand = CandidateProfile(job_id=other_job.job_id, resume="r")
        save_candidate(other_cand)
        # 用 self.job 的 path 去查 other_job 下的 candidate
        r = self.client.get(
            f"/jobs/{self.job.job_id}/candidates/{other_cand.candidate_id}/plan"
        )
        self.assertEqual(r.status_code, 404)

    def test_get_plan_404_when_not_generated_yet(self):
        """直接通过 DB 建 candidate, 跳过 API 也就跳过 Planner; GET 应 404。"""
        from src.db import save_candidate
        from src.schemas import CandidateProfile
        cand = CandidateProfile(job_id=self.job.job_id, resume="r", projects=[])
        save_candidate(cand)
        r = self.client.get(
            f"/jobs/{self.job.job_id}/candidates/{cand.candidate_id}/plan"
        )
        self.assertEqual(r.status_code, 404)


class ExceptionMappingTests(unittest.TestCase):
    """领域异常 -> HTTP 状态码映射 (无需真 infra, 用 mock 注入异常)。"""

    def test_database_not_configured_returns_503(self):
        from src.db import DatabaseNotConfigured
        with patch(
            "src.db.save_job",
            side_effect=DatabaseNotConfigured("无 POSTGRES_URL"),
        ):
            client = TestClient(create_app())
            r = client.post("/jobs", json={"title": "t", "jd": "j"})
        self.assertEqual(r.status_code, 503)
        body = r.json()
        self.assertIn("数据库", body["detail"])
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
