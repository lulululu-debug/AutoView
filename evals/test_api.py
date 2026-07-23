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

# Sprint 5.9 patch: swap to TEST_POSTGRES_URL 防 TRUNCATE 抹掉 dev DB.
from evals._test_db import swap_to_test_url  # noqa: E402
swap_to_test_url()
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import API_TITLE, API_VERSION, create_app

# Sprint 5.9 patch: 防 .env 里 ASSESSOR_ENABLED=true + pymilvus.settings.load_dotenv()
# 让 e2e walk 因 Assessor fallback (confidence=0.3 < 0.5 阈值) 触发追问失控。
# set, not pop, 防 dotenv 把 .env 值加回。
os.environ["ASSESSOR_ENABLED"] = "false"


# Sprint 5.5: lateral plan 默认 7 题 (self_intro+project*3+scenario*2+knowledge*1)。
# 每条答案 >60 字 + 含 _SPECIFICITY_HINTS 至少一个 ("比如"/"我们"/"结果"/"%"),
# 避免现行 Interviewer 启发式触发额外追问让 walk 超出 pool。
# 池子放 10 条留 buffer; walk loop 自带 done short-circuit, 多余的不消费。
# task 4 接 FollowUpPolicy 之后, self_intro 强制 0 追问, 这里的约束会进一步放宽。
_PROVEN_ANSWERS = [
    "我是张三, 后端 4 年, 比如最近在订单和对账中台做高可用 + 性能, "
    "我们组同时把 P99 从 800ms 降到 350ms, 结果对线上影响很可控。",
    "去年大促前订单 P99 从 800ms 涨到 2s, 比如复合索引失效 + 热点 key 击穿, "
    "我们加回索引并改造成本地缓存 + Redis 二级缓存, 结果 P99 回到 350ms。",
    "对账中台日处理 2 亿笔, 早期延迟 30 分钟+。我们改成分桶 + 并行 + 幂等键 + Kafka 回放, "
    "结果延迟降到 3 分钟, 漏对率从 0.4‰ 降到 0.02‰。",
    "我会先用数据让对方理解我担心的点, 比如拉一份线上回放, "
    "我们再一起定义可灰度的中间方案, 结果上半年风控争议就这么收的。",
    "前 5 分钟先看链路 RT 和错误率分布, 比如下游谁慢、是不是热点 key, "
    "我们再决定先扩容还是先限流, 结果优先选不破坏可观测性的动作。",
    "我先在群里说定位到根因 + ETA 15 分钟, 比如先告知业务 PM 影响范围, "
    "我们再 1on1 同步细节, 结果对外口径不会跑偏。",
    "我对 CAP 的理解是: P 是默认前提, 比如订单状态我们选 C 用强一致状态机, "
    "结果不会出现『已支付未发货』那种异常态。",
    "补一句: A/B 实验里我们最看重误判, 比如下游业务对一致性敏感, "
    "结果会让我们倾向保守的发布节奏。",
    "再补一段: 我会把 SLO 和 SLA 拆开看, 比如 SLA 是对外承诺, 我们内部 SLO 留 buffer, "
    "结果 oncall 不会被边界值反复打扰。",
    "最后补一段: 灰度策略上, 比如订单类我们走 1% → 10% → 全量, "
    "结果异常能在 10% 之前被捕获。",
]


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
        # Sprint 5.5: 默认 lateral track 4 stage 7 题
        total = sum(len(r.questions) for r in plan_pg.rounds)
        self.assertEqual(len(plan_pg.rounds), 4, "lateral 4 stage 序列")
        # Sprint 6.5 F5: tech-lateral 配比 1+6+3+2=12 (cap 15 预留 3 追问)
        self.assertEqual(total, 12, "lateral 配比 1+6+3+2")
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
        # Sprint 5.5: 默认 lateral track 4 stage 7 题
        self.assertEqual(len(plan["rounds"]), 4)
        total = sum(len(r["questions"]) for r in plan["rounds"])
        # Sprint 6.5 F5: tech-lateral 12 主问题
        self.assertEqual(total, 12)

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
        from src.schemas import CandidateProfile, CompletionPolicy, JobContext
        init_db()
        cls.client = TestClient(create_app())
        # 准备: 1 个 job + 1 个 candidate + 1 个 plan
        # Sprint 5.9: tech-lateral plan 现在 22 主问题, _PROVEN_ANSWERS 10 条
        # 答案池. completion_policy.max_total=10 让 walk 命中 hard cap done.
        # 本类测的是 session 状态机 + finalize 链路, 不是 Planner 配比。
        cls.job = JobContext(
            title="后端工程师", jd="负责交易系统",
            completion_policy=CompletionPolicy(max_total_questions=10),
        )
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
        done = False
        for ans in _PROVEN_ANSWERS:
            r = self.client.post(f"/interviews/{sid}/answers", json={"text": ans})
            self.assertEqual(r.status_code, 200)
            if r.json()["done"]:
                done = True
                break
        self.assertTrue(done, "答案池应当能走到 done")
        # 已 COMPLETED 还在 Redis (没 finalize), 再 submit 应当 409
        r = self.client.post(f"/interviews/{sid}/answers", json={"text": "再答一句"})
        self.assertEqual(r.status_code, 409)

    def test_validation_empty_answer_text(self):
        start = self._start()
        sid = start["session_id"]
        r = self.client.post(f"/interviews/{sid}/answers", json={"text": ""})
        self.assertEqual(r.status_code, 422)

    # ----- GET /interviews/{id}/report (Sprint 2-6) -----

    def _walk_to_done(self) -> str:
        """开一次会话, 喂答案到 done, 返回 session_id。"""
        start = self._start()
        sid = start["session_id"]
        for ans in _PROVEN_ANSWERS:
            r = self.client.post(f"/interviews/{sid}/answers", json={"text": ans})
            if r.json()["done"]:
                return sid
        self.fail("应当能走到 done")

    def test_get_report_when_completed_returns_200(self):
        sid = self._walk_to_done()
        r = self.client.get(f"/interviews/{sid}/report")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["session_id"], sid)
        self.assertEqual(len(body["content_scores"]), 2, "2 个考察维度")
        self.assertTrue(body["needs_human_review"], "合规: 默认人工复核")
        self.assertEqual(body["performance_observations"], [])

    def test_get_report_finalizes_and_clears_redis(self):
        """副作用: 第一次 GET 应当把 session + plan 从 Redis 清掉, 报告写入 PG。"""
        from src import cache
        from src.db import load_report_by_session
        sid = self._walk_to_done()
        # 走前: Redis 里仍有 session
        session_before = cache.load_session(sid)
        self.assertIsNotNone(session_before)
        plan_id = session_before.plan_id

        r = self.client.get(f"/interviews/{sid}/report")
        self.assertEqual(r.status_code, 200)

        # 走后: Redis 清空
        self.assertIsNone(cache.load_session(sid))
        self.assertIsNone(cache.load_plan(plan_id))
        # PG 有报告
        archived = load_report_by_session(sid)
        self.assertIsNotNone(archived)
        self.assertEqual(archived.overall, r.json()["overall"])

    def test_get_report_idempotent(self):
        """同一 session_id 多次 GET: 第一次走 Redis -> finalize 分支, 之后走 PG 分支。"""
        sid = self._walk_to_done()
        r1 = self.client.get(f"/interviews/{sid}/report")
        r2 = self.client.get(f"/interviews/{sid}/report")
        r3 = self.client.get(f"/interviews/{sid}/report")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r3.status_code, 200)
        # 同一份: report_id 相同(不会每次新生成)
        self.assertEqual(r1.json()["report_id"], r2.json()["report_id"])
        self.assertEqual(r2.json()["report_id"], r3.json()["report_id"])

    def test_get_report_in_progress_returns_409(self):
        """会话还在答题阶段, 不允许提前取报告(方案 B)。"""
        start = self._start()
        sid = start["session_id"]
        r = self.client.get(f"/interviews/{sid}/report")
        self.assertEqual(r.status_code, 409)

    def test_get_report_unknown_session_404(self):
        r = self.client.get("/interviews/ghost-session/report")
        self.assertEqual(r.status_code, 404)

    # ----- POST /interviews/{id}/finalize (Sprint 5-3) -----

    def test_finalize_walks_completed_session_to_204(self):
        from src import cache
        from src.db import load_report_by_session
        sid = self._walk_to_done()
        # 走前: Redis 里仍有 session
        self.assertIsNotNone(cache.load_session(sid))
        r = self.client.post(f"/interviews/{sid}/finalize")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.text, "", "204 应当无 body")
        # 走后: PG 有 report
        self.assertIsNotNone(load_report_by_session(sid))
        # Redis 清空
        self.assertIsNone(cache.load_session(sid))

    def test_finalize_idempotent(self):
        sid = self._walk_to_done()
        r1 = self.client.post(f"/interviews/{sid}/finalize")
        r2 = self.client.post(f"/interviews/{sid}/finalize")
        r3 = self.client.post(f"/interviews/{sid}/finalize")
        for r in (r1, r2, r3):
            self.assertEqual(r.status_code, 204, f"幂等 finalize 应 204, 实际 {r.status_code}")

    def test_finalize_in_progress_returns_409(self):
        start = self._start()
        sid = start["session_id"]
        # 没答完, 直接 finalize
        r = self.client.post(f"/interviews/{sid}/finalize")
        self.assertEqual(r.status_code, 409)

    def test_finalize_unknown_session_404(self):
        r = self.client.post("/interviews/ghost-session/finalize")
        self.assertEqual(r.status_code, 404)


@unittest.skipUnless(os.environ.get("POSTGRES_URL"), "需要 POSTGRES_URL")
class GetJobTests(unittest.TestCase):
    """GET /jobs/{id} 候选人端要拿来显示职位标题等。"""

    @classmethod
    def setUpClass(cls):
        from src.db import init_db
        init_db()
        cls.client = TestClient(create_app())

    def test_get_job_returns_persisted_fields(self):
        r = self.client.post("/jobs", json={
            "title": "后端工程师",
            "jd": "负责交易系统",
            "requirements": ["分布式"],
            "company_materials": "x 公司",
        })
        job_id = r.json()["job_id"]

        r = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["job_id"], job_id)
        self.assertEqual(body["title"], "后端工程师")
        self.assertEqual(body["jd"], "负责交易系统")
        self.assertEqual(body["role_family"], "backend", "默认 role_family")

    def test_get_unknown_job_404(self):
        r = self.client.get("/jobs/ghost-job")
        self.assertEqual(r.status_code, 404)


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL",
)
class GetCandidateTests(unittest.TestCase):
    """GET /jobs/{id}/candidates/{cid} 给候选人端轮询用 (有没有 resume / plan 进度 等)。"""

    @classmethod
    def setUpClass(cls):
        from src.db import init_db
        init_db()
        cls.client = TestClient(create_app())

    def test_get_candidate_after_post(self):
        from src.db import save_job
        from src.schemas import JobContext
        job = JobContext(title="t", jd="x")
        save_job(job)
        r = self.client.post(
            f"/jobs/{job.job_id}/candidates",
            json={"resume": "张三 / 后端 / 4 年", "projects": ["P99"]},
        )
        cid = r.json()["candidate_id"]

        r = self.client.get(f"/jobs/{job.job_id}/candidates/{cid}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["candidate_id"], cid)
        self.assertEqual(body["job_id"], job.job_id)
        self.assertEqual(body["resume"], "张三 / 后端 / 4 年")

    def test_cross_job_404(self):
        """安全: 用另一个 job 的 path 偷看 candidate 应当 404。"""
        from src.db import save_candidate, save_job
        from src.schemas import CandidateProfile, JobContext
        job_a = JobContext(title="A", jd="x")
        job_b = JobContext(title="B", jd="y")
        save_job(job_a)
        save_job(job_b)
        cand_a = CandidateProfile(job_id=job_a.job_id, resume="A 的简历")
        save_candidate(cand_a)
        r = self.client.get(f"/jobs/{job_b.job_id}/candidates/{cand_a.candidate_id}")
        self.assertEqual(r.status_code, 404)


class CorsTests(unittest.TestCase):
    """CORS preflight 应当允许 Next.js dev server (localhost:3000)。"""

    def setUp(self):
        self.client = TestClient(create_app())

    def test_preflight_from_localhost_3000_allowed(self):
        r = self.client.options(
            "/jobs",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.headers.get("access-control-allow-origin"),
            "http://localhost:3000",
        )

    def test_actual_request_has_cors_header(self):
        r = self.client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.headers.get("access-control-allow-origin"),
            "http://localhost:3000",
        )

    def test_disallowed_origin_no_cors_header(self):
        r = self.client.options(
            "/jobs",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        # FastAPI/Starlette 拒绝时不会带 allow-origin: evil.example.com
        self.assertNotEqual(
            r.headers.get("access-control-allow-origin"),
            "http://evil.example.com",
        )


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
