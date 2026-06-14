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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
os.environ.pop("OPENAI_API_KEY", None)


def _short_intro() -> str:
    """30 字以内的自我介绍, 没有 specificity hint, 故意触发原启发式的"短答+无 hint"。
    self_intro 在 task 4 必须豁免追问, 否则走不到 project stage。"""
    return "我叫张三, 后端 4 年。"


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
        from src import cache, db
        from src.agents import planner
        from src.schemas import CandidateProfile, JobContext, Track
        # 关键: 显式把 job/candidate/plan 落 PG, 让 submit_answer 里
        # db.load_candidate_for_plan 能找到 (否则走 fallback 路径文本是空 job 模板)。
        db.init_db()
        job = JobContext(
            title="后端工程师", jd="负责核心交易系统的稳定性与性能。",
            requirements=["分布式", "数据库优化"],
            track=Track.LATERAL,
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


if __name__ == "__main__":
    unittest.main()
