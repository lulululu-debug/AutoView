"""Sprint 8.3 task 2 —— 信念驱动追问调度护栏。零 token。

- 预算流向高方差维度: 同样低分, 高方差 competency 追问 / 低方差不追
- 全局预算耗尽 -> 不追
- 两个门**只作用于校准路径**: 无 calibrated 的 assessment (启发式) 行为
  与 8.3 之前逐字节一致 (belief/预算传了也不消费)
- orchestrator 信念更新接线: 校准 assessment 落地 -> beliefs 变化 + trace
  span; 无校准 -> 不更新
- next_turn 级: session.beliefs + 全局预算被真实消费

跑法: python -m unittest evals.test_followup_scheduling
"""
from __future__ import annotations

import os
import unittest

os.environ.pop("OPENAI_API_KEY", None)

from src.beliefs import initial_belief, update_belief  # noqa: E402
from src.schemas import (  # noqa: E402
    AnswerAssessment,
    CandidateAnswer,
    Competency,
    CompetencyBelief,
    DecisionTrace,
    FollowUpPolicy,
    InterviewPlan,
    InterviewRound,
    InterviewSession,
    Question,
    QuestionCategory,
    Turn,
    TurnRole,
)


def _q(cid: str = "c1") -> Question:
    return Question(
        competency_id=cid, text="讲讲你的性能优化。",
        category=QuestionCategory.PROJECT_EXPERIENCE,
    )


def _aa(q: Question, cal: float | None, raw: float = 0.4) -> AnswerAssessment:
    return AnswerAssessment(
        question_id=q.question_id, sufficiency=raw, confidence=0.9,
        calibrated_sufficiency=cal,
    )


def _high_var(cid: str) -> CompetencyBelief:
    return initial_belief(cid)  # variance 0.25


def _low_var_high_mean(cid: str) -> CompetencyBelief:
    """证据足且已确立为佳: variance ~0.021 < 0.03, mean ~0.87 >= 0.8。"""
    b = None
    for _ in range(4):
        b = update_belief(b, cid, 0.92)
    return b


def _low_var_low_mean(cid: str) -> CompetencyBelief:
    """证据足但未达标: variance ~0.021 < 0.03, mean ~0.5 < 0.8。"""
    b = None
    for _ in range(4):
        b = update_belief(b, cid, 0.5)
    return b


class SchedulingDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        from src.agents.interviewer import _decide_followup
        self.decide = _decide_followup
        self.q = _q()
        self.ans = CandidateAnswer(question_id=self.q.question_id, text="x")
        self.policy = FollowUpPolicy(max_followups_per_question=2)

    def test_budget_flows_to_high_variance(self) -> None:
        """同样的低校准分: 高方差维度追问; 低方差且已确立为佳的维度不追。"""
        low_cal = _aa(self.q, 0.5)
        self.assertTrue(self.decide(
            self.q, self.ans, low_cal, self.policy, 0,
            belief=_high_var("c1"), total_followups=0,
        ))
        self.assertFalse(self.decide(
            self.q, self.ans, low_cal, self.policy, 0,
            belief=_low_var_high_mean("c1"), total_followups=0,
        ))

    def test_low_variance_low_mean_still_probes(self) -> None:
        """s83 复验教训: 证据足但未达标 (mean<0.8) 必须保留追问 ——
        缺口证据 (missing_signals) 靠追问暴露, 不能只因'问过几次'就放弃。
        注: 追问回答不进评分, 本门只关证据完整性与预算流向。"""
        low_cal = _aa(self.q, 0.5)
        self.assertTrue(self.decide(
            self.q, self.ans, low_cal, self.policy, 0,
            belief=_low_var_low_mean("c1"), total_followups=0,
        ))

    def test_global_budget_exhausted_stops(self) -> None:
        low_cal = _aa(self.q, 0.5)
        budget = self.policy.total_followup_budget
        self.assertFalse(self.decide(
            self.q, self.ans, low_cal, self.policy, 0,
            belief=_high_var("c1"), total_followups=budget,
        ))

    def test_high_calibrated_stops_before_gates(self) -> None:
        """分数达标直接停, 不看预算/方差 (门只挡'想追问'的)。"""
        good = _aa(self.q, 0.95)
        self.assertFalse(self.decide(
            self.q, self.ans, good, self.policy, 0,
            belief=_high_var("c1"), total_followups=0,
        ))

    def test_uncalibrated_path_ignores_gates(self) -> None:
        """启发式路径 (cal=None): 预算耗尽/低方差都不影响 —— 行为与 8.3 前一致。"""
        raw_low = _aa(self.q, None, raw=0.4)
        self.assertTrue(self.decide(
            self.q, self.ans, raw_low, self.policy, 0,
            belief=_low_var_high_mean("c1"), total_followups=99,
        ))
        raw_high = _aa(self.q, None, raw=0.7)
        self.assertFalse(self.decide(
            self.q, self.ans, raw_high, self.policy, 0,
            belief=_high_var("c1"), total_followups=0,
        ))


class OrchestratorBeliefWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)  # orchestrator import 回填 (F9)
        from src.orchestrator import _update_belief_for
        self.update_for = _update_belief_for

    def _session(self) -> InterviewSession:
        return InterviewSession(plan_id="p", job_id="j")

    def test_calibrated_assessment_updates_belief_and_trace(self) -> None:
        from src import trace
        q = _q("c-tech")
        session = self._session()
        t = DecisionTrace(session_id="s")
        token = trace.activate(t)
        try:
            self.update_for(session, q, _aa(q, 0.8))
        finally:
            trace.deactivate(token)
        self.assertIn("c-tech", session.beliefs)
        b = session.beliefs["c-tech"]
        self.assertEqual(b.n_observations, 1)
        self.assertGreater(b.mean, 0.5)
        spans = [s for s in t.spans if s.name == "belief_update"]
        self.assertEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0].attributes["observation"], 0.8)

    def test_uncalibrated_assessment_no_update(self) -> None:
        q = _q("c-tech")
        session = self._session()
        self.update_for(session, q, _aa(q, None))
        self.assertEqual(session.beliefs, {})

    def test_self_intro_no_competency_no_update(self) -> None:
        q = Question(competency_id=None, text="自我介绍",
                     category=QuestionCategory.SELF_INTRO)
        session = self._session()
        self.update_for(session, q, _aa(q, 0.9))
        self.assertEqual(session.beliefs, {})


class NextTurnIntegrationTests(unittest.TestCase):
    """next_turn 真实消费 session.beliefs 与全局预算计数。"""

    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)
        from src.agents import interviewer
        self.interviewer = interviewer

    def _fixture(self, belief: CompetencyBelief | None, prior_followups: int = 0):
        comp = Competency(competency_id="c1", name="技术深度", description="x")
        q1, q2 = _q("c1"), _q("c1")
        plan = InterviewPlan(
            job_id="j",
            rounds=[InterviewRound(index=0, title="t",
                                   competencies=[comp], questions=[q1, q2])],
            competencies=[comp],
        )
        session = InterviewSession(plan_id=plan.plan_id, job_id="j")
        # 预热 prior_followups 个已问过的追问 (挂在 q1 名下, ref 不在题库)
        session.history.append(
            Turn(role=TurnRole.INTERVIEWER, text=q1.text, ref_id=q1.question_id))
        for i in range(prior_followups):
            aid = f"fu-{i}"
            session.history.append(
                Turn(role=TurnRole.CANDIDATE, text="嗯", ref_id=f"a{i}"))
            session.history.append(
                Turn(role=TurnRole.INTERVIEWER, text="能展开吗", ref_id=aid))
        ans = CandidateAnswer(question_id=q1.question_id, text="加了缓存。")
        session.answers.append(ans)
        session.history.append(
            Turn(role=TurnRole.CANDIDATE, text=ans.text, ref_id=ans.answer_id))
        session.assessments.append(_aa(q1, 0.5))
        if belief is not None:
            session.beliefs["c1"] = belief
        return session, plan

    def test_low_variance_belief_skips_followup(self) -> None:
        from src.schemas import FollowUp
        s_high, plan = self._fixture(_high_var("c1"))
        self.assertIsInstance(
            self.interviewer.next_turn(s_high, plan, job=None), FollowUp,
        )
        s_low, plan2 = self._fixture(_low_var_high_mean("c1"))
        nxt = self.interviewer.next_turn(s_low, plan2, job=None)
        self.assertIsInstance(nxt, Question)  # 不追问, 推进下一题


if __name__ == "__main__":
    unittest.main()
