"""Sprint 5.5 task 4: orchestrator 跨阶段 + lazy gen + intro_text 流转 护栏。

护栏对象:
- self_intro 答案落进 session.intro_text (不暴露给 HR UI, 但供 lazy gen 用)
- self_intro 短答 (<60 字、不含 specificity hint) 也不触发 followup
- 进 project stage 前, lazy 题 text 空; 答完 self_intro 后, 下一题就是已 resolve
  的 project 题, text 非空
- lazy 静态信号: resolve 后 question.lazy 仍为 True (HR 审计用)
- 整轮 walk 可走到 done, 总题数与 lateral 配比一致 (7 道)

跑法:
    python -m unittest evals.test_orchestrator_stage

需要 PG + Redis (无则 skip)。LLM stub 路径走 fallback 文本, 不烧 token。
"""
from __future__ import annotations

import os
import unittest

# Sprint 5.9 patch: swap to TEST_POSTGRES_URL 防 TRUNCATE 抹掉 dev DB.
from evals._test_db import swap_to_test_url  # noqa: E402
swap_to_test_url()

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
os.environ.pop("OPENAI_API_KEY", None)
# Sprint 5.9 patch: .env 里 HR 翻了 ASSESSOR_ENABLED=true 后 e2e walk 会走 Assessor
# fallback 启发式 (confidence=0.3) → 每题触发 followup → walk 超 7 题. 本模块测的是
# Sprint 5.5 行为 (无 Assessor), 显式 pop 让走纯启发式 _needs_followup 路径。
os.environ["ASSESSOR_ENABLED"] = "false"  # set, not pop, 防 pymilvus load_dotenv 再加回


def _short_intro() -> str:
    """30 字以内的自我介绍, 没有 specificity hint, 故意触发原启发式的"短答+无 hint"。
    self_intro 在 task 4 必须豁免追问, 否则走不到 project stage。"""
    return "我叫张三, 后端 4 年。"


def _CAMPUS_REST_ANSWERS() -> list[str]:
    """campus 路径剩 6 道题的答案。stage 顺序: knowledge*3, project*2, scenario*1。
    每条 >60 字 + 含 hint ("比如" / "我们" / "结果" / "%"), 防 followup 把题数推爆。"""
    return [
        "在分布式场景里, 比如订单服务, 我们用一致性哈希分桶 + 副本备份, "
        "结果 P99 在大促也稳定在 80ms 以内, 没出现热点 key 击穿的情况。",
        "缓存与 DB 一致性, 比如订单写入我们走 write-through + Cache Aside, "
        "结果一致性窗口控制在 5ms 内, 业务感知不到不一致。",
        "我会先用数据让对方理解风险, 比如拉历史 incident 复盘记录, "
        "我们再共同定灰度方案, 结果上半年风控争议这么收的, 没有回滚。",
        "比如订单 P99 优化, 我们改本地缓存 + Redis 二级缓存, "
        "结果 P99 从 800ms 回到 350ms, 漏对率从 0.4‰ 降到 0.02‰。",
        "对账中台从 0 到 1, 比如分桶并行 + 幂等键 + Kafka 回放, "
        "我们结果延迟从 30 分钟降到 3 分钟, 漏对率显著下降。",
        "前 5 分钟先看链路 RT, 比如下游谁慢、是不是热点 key, "
        "我们再决定是先扩容还是先限流, 结果优先不破坏可观测性。",
    ]


def _LATERAL_REST_ANSWERS() -> list[str]:
    """self_intro 之后剩下 6 道题的答案。每条 >60 字 + 含 hint, 防 followup。
    顺序: project*3, scenario*2, knowledge*1。"""
    return [
        "比如订单 P99 优化, 我们把热点 key 击穿改成本地缓存 + Redis 二级缓存, "
        "结果 P99 从 800ms 回到 350ms, 漏对率从 0.4‰ 降到 0.02‰。",
        "对账中台从 0 到 1, 比如分桶并行 + 幂等键 + Kafka 回放, "
        "我们结果延迟从 30 分钟降到 3 分钟, 漏对率从 0.4‰ 降到 0.02‰。",
        "跨职能协作上, 比如我会先拉数据让对方理解风险, 我们再定义可灰度的中间方案, "
        "结果上半年风控争议这么收的, 没出现回滚。",
        "前 5 分钟先看链路 RT 分布, 比如下游谁慢、是不是热点 key, "
        "我们再决定是先扩容还是先限流, 结果优先不破坏可观测性。",
        "我先在群里说定位到根因 + ETA 15 分钟, 比如先告知业务 PM 影响范围, "
        "我们再 1on1 同步细节, 结果对外口径不会跑偏。",
        "对 CAP 的理解: P 是默认前提, 比如订单状态我们选 C 用强一致状态机, "
        "结果不会出现『已支付未发货』那种异常态。",
    ]


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL 跑 orchestrator 状态机",
)
class StageTransitionTests(unittest.TestCase):
    """端到端跑 lateral track: self_intro -> project (lazy resolve) -> scenario
    -> knowledge -> done."""

    @classmethod
    def setUpClass(cls):
        # pymilvus.settings.load_dotenv() 在 import 时再把 ASSESSOR_ENABLED 加回,
        # 模块顶 pop 已被覆盖, 在 setUpClass 再 pop 一次保证 e2e 走启发式路径.
        os.environ["ASSESSOR_ENABLED"] = "false"  # set, not pop, 防 pymilvus load_dotenv 再加回
        from src import cache, db
        from src.agents import planner
        from src.schemas import (
            CandidateProfile, CompletionPolicy, JobContext, Track,
        )
        # 关键: 显式把 job/candidate/plan 落 PG, 让 submit_answer 里
        # db.load_candidate_for_plan 能找到 (否则走 fallback 路径文本是空 job 模板)。
        db.init_db()
        # Sprint 5.9: tech-lateral plan 现在 22 主问题, 但本 fixture 仍用 7
        # answer 答案池. completion_policy.max_total_questions=7 让 walk 走完
        # 7 answer 后命中 hard cap done. 测的是状态机, 不是 Planner 配比。
        job = JobContext(
            title="后端工程师", jd="负责核心交易系统的稳定性与性能。",
            requirements=["分布式", "数据库优化"],
            track=Track.LATERAL,
            completion_policy=CompletionPolicy(max_total_questions=7),
        )
        candidate = CandidateProfile(
            job_id=job.job_id,
            resume="张三 / 后端 / 4 年。订单 P99 优化; 对账中台从 0 到 1。",
            projects=["订单 P99 优化", "对账中台"],
        )
        plan = planner.plan(job, candidate)

        db.save_job(job)
        db.save_candidate(candidate)
        db.save_plan(plan, candidate.candidate_id)

        cls.job = job
        cls.candidate = candidate
        cls.plan = plan
        cls.cache = cache

    def _start_and_get_session_id(self) -> str:
        from src.orchestrator import start_session
        result = start_session(self.job, self.candidate, plan=self.plan)
        self.assertFalse(result.done, "start_session 应当返回首问")
        self.assertIsNotNone(result.prompt)
        return result.session_id

    def test_self_intro_short_answer_does_not_trigger_followup(self):
        """self_intro 答 10 字也不应触发 followup; 下一句应该是 project 题。"""
        from src.orchestrator import submit_answer
        sid = self._start_and_get_session_id()
        next_turn = submit_answer(sid, _short_intro())
        # 下一句不该是追问 (TurnResult 没有显式区分, 但 prompt 应当不是
        # "能再展开" / "请补充" 这类追问文本)。更稳的 check: 看下一题 question_id
        # 是 plan 里第二个 round 的第一题 (即 project 第一题), 不是 followup_id。
        plan = self.cache.load_plan(self.plan.plan_id)
        project_round = next(r for r in plan.rounds if r.stage.value == "project")
        expected_first_project = project_round.questions[0]
        self.assertEqual(
            next_turn.ref_id, expected_first_project.question_id,
            "self_intro 之后应直接跳到 project 第一题, 不能触发 followup",
        )

    def test_self_intro_answer_saved_to_intro_text(self):
        """提交 self_intro 答案后, session.intro_text 必须等于该答案。"""
        from src.orchestrator import submit_answer
        sid = self._start_and_get_session_id()
        intro = _short_intro()
        submit_answer(sid, intro)
        session = self.cache.load_session(sid)
        self.assertEqual(session.intro_text, intro)

    def test_project_question_lazy_resolved_when_asked(self):
        """plan 阶段 project 题 lazy=True text='', 答完 self_intro 后:
        - 缓存里整 plan 的 project 题已 resolve (text 非空)
        - lazy 仍 True (静态信号, 作 HR 审计)
        - 下一句 prompt 就是已 resolve 的 text"""
        from src.orchestrator import submit_answer
        # plan 阶段确认 lazy+empty
        for r in self.plan.rounds:
            if r.stage.value == "project":
                for q in r.questions:
                    self.assertTrue(q.lazy)
                    self.assertEqual(q.text, "")

        sid = self._start_and_get_session_id()
        next_turn = submit_answer(sid, _short_intro())

        # 缓存里 plan 的 project 题已 resolve
        plan = self.cache.load_plan(self.plan.plan_id)
        for r in plan.rounds:
            if r.stage.value == "project":
                for q in r.questions:
                    self.assertTrue(q.lazy, "lazy 静态信号 resolve 后仍 True")
                    self.assertNotEqual(q.text, "", "project 题 text 应已回灌")

        # 返回的 prompt 就是回灌后的 text
        self.assertTrue(next_turn.prompt and next_turn.prompt.strip())

    def test_full_lateral_walk_to_done(self):
        """跑完 lateral 全 7 道, 最后 finalize 拿到报告。"""
        from src.orchestrator import finalize, submit_answer
        sid = self._start_and_get_session_id()
        # self_intro
        result = submit_answer(sid, _short_intro())
        # 剩下 6 道
        for ans in _LATERAL_REST_ANSWERS():
            if result.done:
                break
            result = submit_answer(sid, ans)
        self.assertTrue(result.done, "lateral 走完 7 题应当 done")

        report = finalize(sid)
        # 内容维度 = plan.competencies 数 (2)
        self.assertEqual(len(report.content_scores), 2)


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL 跑 orchestrator 状态机",
)
class LazyResolveSyncsPgPlanTests(unittest.TestCase):
    """Sprint 5.8 patch: lazy resolve 后 PG.plan_data 同步, 让 HR StageView
    走 GET /jobs/{j}/candidates/{c}/plan (拉 PG) 不再永远显示"待懒生成"。"""

    @classmethod
    def setUpClass(cls):
        # 同 StageTransitionTests: 在 setUpClass 显式 pop ASSESSOR_ENABLED 防
        # pymilvus.settings.load_dotenv() 把 .env 里的 true 加回, 让 e2e 走启发式.
        os.environ["ASSESSOR_ENABLED"] = "false"  # set, not pop, 防 pymilvus load_dotenv 再加回
        from src import db
        from src.agents import planner
        from src.schemas import CandidateProfile, JobContext, Track
        db.init_db()
        job = JobContext(
            title="后端", jd="x", requirements=[], track=Track.LATERAL,
        )
        candidate = CandidateProfile(
            job_id=job.job_id, resume="张三 / 后端 / 4年", projects=[],
        )
        plan = planner.plan(job, candidate)
        db.save_job(job)
        db.save_candidate(candidate)
        db.save_plan(plan, candidate.candidate_id)
        cls.job = job
        cls.candidate = candidate
        cls.plan = plan

    def test_pg_plan_data_synced_after_lazy_resolve(self):
        """submit self_intro 触发 resolve_lazy_questions, PG.plan_data 应当
        反映 project 题已生成 (text 非空) + lazy 仍为 True (审计静态信号)。
        在 patch 之前 PG.plan_data 是 stale 的 -> HR StageView 永远显示
        "待懒生成"。"""
        from src import db
        from src.orchestrator import start_session, submit_answer

        result = start_session(self.job, self.candidate, plan=self.plan)
        sid = result.session_id
        submit_answer(
            sid,
            "我是张三, 后端 4 年, 比如最近在订单系统做 P99 优化, 我们结果 P99 降到 350ms。",
        )
        pg_plan = db.load_latest_plan_for_candidate(self.candidate.candidate_id)
        self.assertIsNotNone(pg_plan)
        project_qs = [
            q for r in pg_plan.rounds for q in r.questions
            if r.stage.value == "project"
        ]
        self.assertTrue(project_qs)
        for q in project_qs:
            self.assertNotEqual(
                q.text, "",
                f"PG plan_data 里 project 题 text 应非空 (text='{q.text}')",
            )
            self.assertTrue(q.lazy, "lazy 静态信号生成后仍 True (审计)")


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL 跑 orchestrator 状态机",
)
class IntroTextFlowsIntoProjectPromptTests(unittest.TestCase):
    """Sprint 5.5 task 4 核心承诺: project 题在 lazy gen 时把 intro_text 喂进 LLM,
    让题目真正反映候选人自我介绍里提到的内容。
    用魔法字符串 + monkey-patch llm.complete 验证流转链路。"""

    MAGIC = "ZQ8X-INTRO-MARKER-9K7P"

    @classmethod
    def setUpClass(cls):
        # 同 StageTransitionTests: 在 setUpClass 显式 pop ASSESSOR_ENABLED 防
        # pymilvus.settings.load_dotenv() 把 .env 里的 true 加回, 让 e2e 走启发式.
        os.environ["ASSESSOR_ENABLED"] = "false"  # set, not pop, 防 pymilvus load_dotenv 再加回
        from src import db
        from src.agents import planner
        from src.schemas import CandidateProfile, JobContext, Track
        db.init_db()
        job = JobContext(
            title="后端工程师", jd="负责核心交易系统的稳定性与性能。",
            requirements=["分布式"], track=Track.LATERAL,
        )
        candidate = CandidateProfile(
            job_id=job.job_id,
            resume="王五 / 后端 / 5 年。订单 P99 优化; 风控引擎。",
            projects=["订单 P99 优化"],
        )
        plan = planner.plan(job, candidate)
        db.save_job(job)
        db.save_candidate(candidate)
        db.save_plan(plan, candidate.candidate_id)
        cls.job = job
        cls.candidate = candidate
        cls.plan = plan

    def test_intro_text_appears_in_some_project_llm_prompt(self):
        """流转链路:
        candidate self_intro 答 -> orchestrator.submit_answer 落 session.intro_text
        -> 下一题 lazy + empty -> _resolve_lazy_now -> planner.resolve_lazy_questions
        -> _project_question 把 intro_text 拼进 user prompt -> llm.complete

        验证: monkey-patch llm.complete 记录所有调用, 至少有一条 user prompt
        包含 magic marker (= 候选人自我介绍内容)。"""
        from src import llm
        from src.orchestrator import start_session, submit_answer

        # 记录所有 LLM 调用的 (system, user)
        recorded: list[tuple[str, str]] = []
        original = llm.complete

        def recording_complete(system: str, user: str, **kwargs) -> str:
            recorded.append((system, user))
            return original(system, user, **kwargs)

        llm.complete = recording_complete  # type: ignore[assignment]
        try:
            result = start_session(self.job, self.candidate, plan=self.plan)
            # self_intro 答案带魔法字 (真实候选人不会这么答, 但模拟"独特识别串"是有效手段)
            intro_with_marker = (
                f"我是王五, 比如最近做订单 P99 优化, 我们结果把 P99 从 800ms "
                f"降到 350ms。我的项目里有个特殊标识 {self.MAGIC} 用来测试链路。"
            )
            submit_answer(result.session_id, intro_with_marker)
        finally:
            llm.complete = original  # type: ignore[assignment]

        # 项目题 lazy gen 必触发至少一次包含 marker 的 LLM 调用
        # (3 道 project 题用同一份 intro_text, 至少 1-3 次 user prompt 含 marker)
        marker_prompts = [
            (s, u) for (s, u) in recorded if self.MAGIC in u
        ]
        self.assertGreater(
            len(marker_prompts), 0,
            f"intro_text 应至少出现在一次 LLM 调用的 user prompt 里. "
            f"记录到 {len(recorded)} 次调用, 系统提示样本: "
            f"{[s[:40] for s, _ in recorded[:5]]}",
        )
        # 进一步: 包含 marker 的 system prompt 应当是项目题生成的 (含"项目"或"深挖")
        for system, _ in marker_prompts:
            self.assertTrue(
                "项目" in system or "深挖" in system,
                f"含 intro marker 的应当是项目题生成 prompt, 实际 system: {system[:80]}",
            )


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL 跑 orchestrator 状态机",
)
class CampusEndToEndTests(unittest.TestCase):
    """端到端跑 campus track: self_intro -> knowledge*3 -> project*2 -> scenario."""

    @classmethod
    def setUpClass(cls):
        # 同 StageTransitionTests: 在 setUpClass 显式 pop ASSESSOR_ENABLED 防
        # pymilvus.settings.load_dotenv() 把 .env 里的 true 加回, 让 e2e 走启发式.
        os.environ["ASSESSOR_ENABLED"] = "false"  # set, not pop, 防 pymilvus load_dotenv 再加回
        from src import db
        from src.agents import planner
        from src.schemas import (
            CandidateProfile, CompletionPolicy, JobContext, Track,
        )
        db.init_db()
        # Sprint 5.9: tech-campus plan 21 主问题, fixture 答案池 7. policy
        # max_total=7 让 walk 命中 hard cap done. test_plan_shape_matches_campus_config
        # 仍用 self.plan (有 21 题) 看结构, 不冲突。
        job = JobContext(
            title="校招后端工程师", jd="负责核心交易系统的稳定性与性能。",
            requirements=["分布式", "数据库优化"],
            track=Track.CAMPUS,
            completion_policy=CompletionPolicy(max_total_questions=7),
        )
        candidate = CandidateProfile(
            job_id=job.job_id,
            resume="李四 / 2025 届硕士 / 实习: 订单系统 P99 优化。",
            projects=["订单系统 P99 优化"],
        )
        plan = planner.plan(job, candidate)
        db.save_job(job)
        db.save_candidate(candidate)
        db.save_plan(plan, candidate.candidate_id)
        cls.job = job
        cls.candidate = candidate
        cls.plan = plan

    def test_plan_shape_matches_campus_config(self):
        """Sprint 5.9: tech-campus 21 主问题 (1 + 12 + 5 + 3)。
        断言 stage 序列正确 + 总题数符合配比。"""
        stages = [r.stage.value for r in self.plan.rounds]
        self.assertEqual(stages, ["self_intro", "knowledge", "project", "scenario"])
        total = sum(len(r.questions) for r in self.plan.rounds)
        self.assertEqual(total, 21, f"tech-campus 应当 21 题, 实际 {total}")

    def test_full_campus_walk_to_done(self):
        """campus 走完 7 题 (1 self_intro + 6 其他), finalize 拿报告。"""
        from src.orchestrator import finalize, start_session, submit_answer
        result = start_session(self.job, self.candidate, plan=self.plan)
        sid = result.session_id
        # self_intro
        result = submit_answer(sid, _short_intro())
        # 剩下 6 道 (knowledge*3 -> project*2 -> scenario*1)
        for ans in _CAMPUS_REST_ANSWERS():
            if result.done:
                break
            result = submit_answer(sid, ans)
        self.assertTrue(result.done, "campus 走完应当 done")
        report = finalize(sid)
        self.assertEqual(len(report.content_scores), 2)


if __name__ == "__main__":
    unittest.main()
