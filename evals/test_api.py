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


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL",
)
class InterviewSessionTests(unittest.TestCase):
    """面试会话三段式: POST /interviews + POST /answers + GET /interviews/{id}。"""

    @classmethod
    def setUpClass(cls):
        from src.db import init_db, save_candidate, save_job, save_plan
        from src.agents.planner import plan as run_planner
        from src.schemas import CandidateProfile, JobContext
        init_db()
        cls.client = TestClient(create_app())
        # 准备: 1 个 job + 1 个 candidate + 1 个 plan
        cls.job = JobContext(title="后端工程师", jd="负责交易系统")
        save_job(cls.job)
        cls.cand = CandidateProfile(
            job_id=cls.job.job_id,
            resume="张三/后端/4年, 订单 P99 优化",
            projects=["P99 优化"],
        )
        save_candidate(cls.cand)
        cls.plan = run_planner(cls.job, cls.cand)
        save_plan(cls.plan, candidate_id=cls.cand.candidate_id)

    def _start(self) -> dict:
        r = self.client.post("/interviews", json={"candidate_id": self.cand.candidate_id})
        self.assertEqual(r.status_code, 201)
        return r.json()

    def test_start_interview_returns_session_id_and_first_prompt(self):
        body = self._start()
        self.assertIn("session_id", body)
        self.assertFalse(body["done"])
        self.assertIsNotNone(body["prompt"])
        self.assertIsNotNone(body["ref_id"])

    def test_start_reuses_pg_plan_does_not_run_planner_again(self):
        """关键: API 必须复用 PG 里的 plan, plan_id 不能漂移。"""
        body = self._start()
        # session 已写 Redis, 顺手从 Redis 拿出来对一下 plan_id
        from src import cache
        session = cache.load_session(body["session_id"])
        self.assertEqual(
            session.plan_id, self.plan.plan_id,
            "API 路径用的 plan 必须是 PG 里那份, 不能让 orchestrator 重跑 planner",
        )

    def test_unknown_candidate_returns_404(self):
        r = self.client.post("/interviews", json={"candidate_id": "ghost-candidate"})
        self.assertEqual(r.status_code, 404)

    def test_no_plan_yet_returns_409(self):
        """candidate 存在但 plan 还没出 (BG Planner 在跑或失败), 应 409。"""
        from src.db import save_candidate
        from src.schemas import CandidateProfile
        cand = CandidateProfile(job_id=self.job.job_id, resume="r")
        save_candidate(cand)  # 没 save_plan
        r = self.client.post("/interviews", json={"candidate_id": cand.candidate_id})
        self.assertEqual(r.status_code, 409)
        self.assertIn("plan", r.json()["detail"].lower())

    def test_submit_answer_advances_to_next_prompt(self):
        start = self._start()
        sid = start["session_id"]
        r = self.client.post(
            f"/interviews/{sid}/answers",
            json={"text": "做过一些性能优化, 主要是慢查询和缓存。"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["session_id"], sid)
        self.assertIsNotNone(body["prompt"], "应有下一句(可能是追问也可能是下一题)")
        self.assertNotEqual(body["ref_id"], start["ref_id"], "ref_id 应当推进")

    def test_resume_returns_pending_prompt(self):
        start = self._start()
        sid = start["session_id"]
        r = self.client.get(f"/interviews/{sid}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["prompt"], start["prompt"], "GET 应当返回同一句待答提示")
        self.assertEqual(body["ref_id"], start["ref_id"])

    def test_resume_unknown_session_404(self):
        r = self.client.get("/interviews/ghost-session")
        self.assertEqual(r.status_code, 404)

    def test_submit_to_unknown_session_404(self):
        r = self.client.post("/interviews/ghost-session/answers", json={"text": "hi"})
        self.assertEqual(r.status_code, 404)

    def test_walk_session_to_done_then_submit_404(self):
        """注: finalize 后会话从 Redis 删除, 再 submit 是 SessionNotFound -> 404,
        不是 SessionInvalidState -> 409。Sprint 2-6 加 GET /report 会自动 finalize,
        此处仅验证走到 done 后状态机正确流转 (Redis 里 status=COMPLETED, 不在 PG)。"""
        start = self._start()
        sid = start["session_id"]
        # 沿用 src/main.py 那套答案: 首答短触发追问, 后四答含 specificity hints
        # (比如 / 我们 / 结果 / %) 避免重复触发, 一共 5 turn 走到 done。
        answers = [
            "做过一些性能优化, 主要是慢查询和缓存。",
            "比如去年大促前, 订单查询 P99 从 800ms 涨到 2s。"
            "我们排查发现是某个复合索引被改后失效, 同时 Redis 出现热点 key 击穿。"
            "我加回索引并改造为本地缓存 + Redis 二级缓存, 最终 P99 回到 350ms。",
            "通常我会先用数据让对方理解我担心的点, 比如拉一份线上回放或历史 case,"
            "再一起定义可灰度的中间方案; 我们组上半年的风控规则争议就是这么收的。",
            "对账中台那段最有挑战。日处理 2 亿笔, 早期对账延迟超过 30 分钟。"
            "我们把单表对账改成分桶 + 并行 worker, 引入幂等键, 用 Kafka 做回放,"
            "结果延迟降到 3 分钟, 漏对率从 0.4‰ 降到 0.02‰。",
            "上半年风控那次, 产品要求 24 小时内全量, 我担心误杀率。"
            "我拉了 SRE 一起跑离线回放, 用数据让产品同意先灰度 5%, 一周后再全量,"
            "误杀率从 3.1% 降到 0.4% 才放开。",
        ]
        done = False
        for ans in answers:
            r = self.client.post(f"/interviews/{sid}/answers", json={"text": ans})
            self.assertEqual(r.status_code, 200)
            if r.json()["done"]:
                done = True
                break
        self.assertTrue(done, "5 条回答应当能走到 done")
        # 已 COMPLETED 还在 Redis (没 finalize), 再 submit 应当 409
        r = self.client.post(f"/interviews/{sid}/answers", json={"text": "再答一句"})
        self.assertEqual(r.status_code, 409)

    def test_validation_empty_answer_text(self):
        start = self._start()
        sid = start["session_id"]
        r = self.client.post(f"/interviews/{sid}/answers", json={"text": ""})
        self.assertEqual(r.status_code, 422)


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
