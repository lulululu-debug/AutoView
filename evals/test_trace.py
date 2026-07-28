"""Sprint 8.2 task 1 —— 决策 Trace 收集护栏。

- 收集器: 无活动 trace 时零开销 no-op / activate 后 llm 调用被录 /
  deactivate 后停录 / span_label 归属标注
- Interviewer 决策 span: followup_decision / completion_check 带阈值实际数值,
  且埋点不改变 next_turn 返回行为
- PG+Redis-gated e2e: stub 全链路面试后, trace 里 session_start / assess /
  followup_decision / completion_check / finalize 齐全, llm_calls 全 stub

跑法: python -m unittest evals.test_trace
"""
from __future__ import annotations

import os
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src import llm, trace  # noqa: E402
from src.schemas import (  # noqa: E402
    CandidateAnswer,
    Competency,
    DecisionTrace,
    InterviewPlan,
    InterviewRound,
    InterviewSession,
    Question,
    QuestionCategory,
    Turn,
    TurnRole,
)


def _plan_fixture() -> tuple[InterviewPlan, list[Question]]:
    comp = Competency(competency_id="cid-t", name="技术深度", description="x")
    q1 = Question(
        competency_id="cid-t", text="讲讲你做过的性能优化。",
        category=QuestionCategory.PROJECT_EXPERIENCE,
    )
    q2 = Question(
        competency_id="cid-t", text="MySQL 事务隔离级别有哪些?",
        category=QuestionCategory.KNOWLEDGE,
    )
    plan = InterviewPlan(
        job_id="j",
        rounds=[InterviewRound(
            index=0, title="t", competencies=[comp], questions=[q1, q2],
        )],
        competencies=[comp],
    )
    return plan, [q1, q2]


def _answered_session(plan: InterviewPlan, q: Question, answer: str) -> InterviewSession:
    session = InterviewSession(plan_id=plan.plan_id, job_id="j")
    session.history.append(
        Turn(role=TurnRole.INTERVIEWER, text=q.text, ref_id=q.question_id),
    )
    ans = CandidateAnswer(question_id=q.question_id, text=answer)
    session.answers.append(ans)
    session.history.append(
        Turn(role=TurnRole.CANDIDATE, text=answer, ref_id=ans.answer_id),
    )
    return session


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)

    def test_no_active_trace_noop(self) -> None:
        self.assertIsNone(trace.current())
        # 无 trace 时 llm 调用 / record 都不炸
        out = llm.complete("s", "u")
        self.assertTrue(llm.is_stub(out))
        trace.record_decision("orphan", foo=1)

    def test_records_llm_calls_and_spans(self) -> None:
        t = DecisionTrace(session_id="s-test")
        token = trace.activate(t)
        try:
            llm.complete("system-x", "user-x")
            with trace.span_label("assess"):
                llm.complete("system-y", "user-y")
            trace.record_decision("assess", question_id="q1", sufficiency=0.5)
        finally:
            trace.deactivate(token)

        self.assertEqual(len(t.llm_calls), 2)
        self.assertEqual(t.llm_calls[0].path, "stub")
        self.assertEqual(t.llm_calls[0].span, "")
        self.assertEqual(t.llm_calls[1].span, "assess")
        self.assertTrue(t.llm_calls[0].request_hash)
        self.assertEqual(len(t.spans), 1)
        self.assertEqual(t.spans[0].question_id, "q1")
        self.assertEqual(t.spans[0].attributes["sufficiency"], 0.5)

        # deactivate 后停录
        llm.complete("system-z", "user-z")
        self.assertEqual(len(t.llm_calls), 2)

    def test_request_hash_deterministic(self) -> None:
        t1 = DecisionTrace(session_id="a")
        token = trace.activate(t1)
        try:
            llm.complete("s", "u")
            llm.complete("s", "u")
            llm.complete("s", "u2")
        finally:
            trace.deactivate(token)
        self.assertEqual(t1.llm_calls[0].request_hash, t1.llm_calls[1].request_hash)
        self.assertNotEqual(t1.llm_calls[0].request_hash, t1.llm_calls[2].request_hash)


class InterviewerSpanTests(unittest.TestCase):
    """决策 span 带实际数值, 且埋点不改变 next_turn 行为。"""

    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)
        from src.agents import interviewer
        self.interviewer = interviewer

    def _run(self, answer: str):
        plan, (q1, _q2) = _plan_fixture()
        session = _answered_session(plan, q1, answer)
        t = DecisionTrace(session_id="s")
        token = trace.activate(t)
        try:
            nxt = self.interviewer.next_turn(session, plan, job=None)
        finally:
            trace.deactivate(token)
        return nxt, t

    def test_short_answer_followup_span(self) -> None:
        from src.schemas import FollowUp
        nxt, t = self._run("加了缓存。")
        self.assertIsInstance(nxt, FollowUp)
        fu = [s for s in t.spans if s.name == "followup_decision"]
        self.assertEqual(len(fu), 1)
        attrs = fu[0].attributes
        self.assertTrue(attrs["decided"])
        self.assertEqual(attrs["via"], "heuristic")  # 无 assessment
        self.assertIn("min_sufficiency_to_stop", attrs)
        self.assertIn("budget_left", attrs)
        # 追问文本生成的 stub 调用带归属标签
        self.assertTrue(
            any(c.span == "followup_text" for c in t.llm_calls),
        )

    def test_good_answer_continue_span(self) -> None:
        nxt, t = self._run(
            "比如订单服务我们用了两级令牌桶, 当时结果 P99 大促稳定在 80ms, "
            "我们选择了 lazy refill 减少 Redis 调用, 用了滑动窗口避免突刺。",
        )
        self.assertIsInstance(nxt, Question)
        fu = [s for s in t.spans if s.name == "followup_decision"]
        self.assertEqual(len(fu), 1)
        self.assertFalse(fu[0].attributes["decided"])
        cc = [s for s in t.spans if s.name == "completion_check"]
        self.assertEqual(len(cc), 1)
        self.assertEqual(cc[0].attributes["reason"], "continue")
        self.assertEqual(cc[0].attributes["next_index"], 1)


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 PG + Redis 跑 e2e trace",
)
class TraceE2ETests(unittest.TestCase):
    """stub 全链路面试 -> trace 里决策序列齐全 (完成标准的机器可验部分)。"""

    def setUp(self) -> None:
        # orchestrator -> planner -> pymilvus 会 load_dotenv 回填 key, 必须再 pop
        from src import cache, orchestrator
        from src.db import init_db
        os.environ.pop("OPENAI_API_KEY", None)
        # discover 顺序下 test_api 模块级设了 ASSESSOR_ENABLED=false 不回收;
        # 本测试要验 assess span, 显式开并在 tearDown 恢复原值
        self._assessor_env = os.environ.get("ASSESSOR_ENABLED")
        os.environ["ASSESSOR_ENABLED"] = "true"
        init_db()
        self.cache = cache
        self.orchestrator = orchestrator

    def tearDown(self) -> None:
        if self._assessor_env is None:
            os.environ.pop("ASSESSOR_ENABLED", None)
        else:
            os.environ["ASSESSOR_ENABLED"] = self._assessor_env

    def test_full_interview_trace(self) -> None:
        from src.schemas import CandidateProfile, JobContext
        job = JobContext(title="后端工程师", jd="高并发服务", track="campus")
        candidate = CandidateProfile(resume="张三, 做过订单系统优化。" * 10)
        answers = ["我是张三, 做后端。"] + ["加了缓存, 效果还行。"] * 20

        result = self.orchestrator.start_session(job, candidate)
        session_id = result.session_id
        while not result.done and answers:
            result = self.orchestrator.submit_answer(session_id, answers.pop(0))

        t = self.cache.load_trace(session_id)
        self.assertIsNotNone(t, "trace 应与 session 同在 Redis")
        names = {s.name for s in t.spans}
        self.assertIn("session_start", names)
        self.assertIn("followup_decision", names)
        self.assertIn("completion_check", names)
        self.assertIn("assess", names)  # ASSESSOR_ENABLED 默认 true
        # 每个 assess span 都有依据字段
        for s in t.spans:
            if s.name == "assess":
                self.assertIn("sufficiency", s.attributes)
                self.assertIn("via", s.attributes)
                self.assertEqual(s.attributes["via"], "heuristic")  # stub 环境
        # stub 环境所有 llm 调用 path=stub
        self.assertTrue(all(c.path == "stub" for c in t.llm_calls))

        report = self.orchestrator.finalize(session_id)
        t2 = self.cache.load_trace(session_id)
        self.assertIsNotNone(t2)
        self.assertIn("finalize", {s.name for s in t2.spans})
        self.assertEqual(report.session_id, session_id)
        self.cache.delete_trace(session_id)


if __name__ == "__main__":
    unittest.main()
