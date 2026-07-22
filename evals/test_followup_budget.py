"""Sprint 6.5 F1 —— 追问预算守卫 + 阈值重校护栏。

背景 (F1 复验批次首场翻车实录): Assessor 量表重锚后 sufficiency 从虚高 0.9+
回归 0.65-0.85 → 旧阈值 0.7 让好回答也触发追问 → 追问挤掉尾部正题撞 hard cap
→ 该维度 0 分 → strong persona overall 90.9 崩到 52。两层修复, 本文件锁住:

1. 预算守卫: 剩余答题预算 <= 未问正题数时**跳过追问直接推进** —— 正题优先,
   追问只花盈余预算 (对候选人的公平: 系统爱追问不能变成候选人的 0 分维度)。
2. 阈值重校: FollowUpPolicy.min_sufficiency_to_stop 0.7 -> 0.6
   (新锚点 0.4-0.6 = 缺深度该追, >= 0.65 = 有效证据不该追)。

跑法: python -m unittest evals.test_followup_budget  (纯内存, LLM 走 stub)
"""
from __future__ import annotations

import os
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src.agents.interviewer import next_turn  # noqa: E402
from src.schemas import (  # noqa: E402
    AnswerAssessment,
    CandidateAnswer,
    Competency,
    CompletionPolicy,
    FollowUp,
    FollowUpPolicy,
    InterviewPlan,
    InterviewRound,
    InterviewSession,
    JobContext,
    Question,
    QuestionCategory,
    Turn,
    TurnRole,
)


def _job(max_total: int) -> JobContext:
    return JobContext(
        title="后端", jd="x",
        completion_policy=CompletionPolicy(max_total_questions=max_total),
    )


class _Base(unittest.TestCase):
    """3 题 plan (2 tech + 1 comm), q1 已问已答, 答案的 assessment 触发追问意愿。"""

    def setUp(self) -> None:
        self.tech = Competency(name="技术深度", description="x")
        self.comm = Competency(name="沟通协作", description="x")
        self.q1 = Question(
            competency_id=self.tech.competency_id, text="Q1",
            category=QuestionCategory.PROJECT_EXPERIENCE,
        )
        self.q2 = Question(
            competency_id=self.tech.competency_id, text="Q2",
            category=QuestionCategory.KNOWLEDGE,
        )
        self.q3 = Question(
            competency_id=self.comm.competency_id, text="Q3",
            category=QuestionCategory.SCENARIO,
        )
        r = InterviewRound(
            index=0, title="t",
            competencies=[self.tech, self.comm],
            questions=[self.q1, self.q2, self.q3],
        )
        self.plan = InterviewPlan(
            job_id="j", rounds=[r], competencies=[self.tech, self.comm],
        )
        self.session = InterviewSession(plan_id=self.plan.plan_id, job_id="j")
        ans = CandidateAnswer(question_id=self.q1.question_id, text="做过一些。")
        self.session.history = [
            Turn(role=TurnRole.INTERVIEWER, text="Q1", ref_id=self.q1.question_id),
            Turn(role=TurnRole.CANDIDATE, text=ans.text, ref_id=ans.answer_id),
        ]
        self.session.answers = [ans]
        # 低 sufficiency + 高 confidence: 决策器一定"想"追问
        self.session.assessments = [
            AnswerAssessment(
                question_id=self.q1.question_id, sufficiency=0.3, confidence=0.9,
            ),
        ]


class BudgetGuardTests(_Base):
    def test_tight_budget_skips_followup(self) -> None:
        """预算恰好只够问完剩余正题 (cap=3, 已答 1, 剩 2 题) -> 不追问, 推进 Q2。"""
        nxt = next_turn(self.session, self.plan, job=_job(max_total=3))
        self.assertIsInstance(nxt, Question)
        assert isinstance(nxt, Question)
        self.assertEqual(nxt.question_id, self.q2.question_id)

    def test_surplus_budget_allows_followup(self) -> None:
        """预算有盈余 (cap=10) -> 正常追问。"""
        nxt = next_turn(self.session, self.plan, job=_job(max_total=10))
        self.assertIsInstance(nxt, FollowUp)

    def test_followup_allowed_on_last_question(self) -> None:
        """正题全问完后 (剩 0 题), 只要预算未尽仍可追问 —— 守卫不误伤收尾深挖。"""
        # 把 q2/q3 也标记为已问已答 (低分, coverage 不达标不会提前 done)
        for q in (self.q2, self.q3):
            ans = CandidateAnswer(question_id=q.question_id, text="嗯。")
            self.session.history += [
                Turn(role=TurnRole.INTERVIEWER, text=q.text, ref_id=q.question_id),
                Turn(role=TurnRole.CANDIDATE, text=ans.text, ref_id=ans.answer_id),
            ]
            self.session.answers.append(ans)
            self.session.assessments.append(
                AnswerAssessment(
                    question_id=q.question_id, sufficiency=0.3, confidence=0.9,
                ),
            )
        nxt = next_turn(self.session, self.plan, job=_job(max_total=10))
        self.assertIsInstance(nxt, FollowUp)


class ThresholdRecalibrationTests(_Base):
    def test_default_threshold_is_recalibrated(self) -> None:
        """量表重锚后的新默认: 0.6。改这个值必须同步 Assessor 锚点 + sim 复验。"""
        self.assertEqual(FollowUpPolicy().min_sufficiency_to_stop, 0.6)

    def test_valid_evidence_no_followup_under_new_scale(self) -> None:
        """新量表下 0.65 = 有效证据: 预算充足也不追问, 直接推进。
        (旧阈值 0.7 会在这里误触发追问 —— F1 级联崩塌的起点。)"""
        self.session.assessments = [
            AnswerAssessment(
                question_id=self.q1.question_id, sufficiency=0.65, confidence=0.9,
            ),
        ]
        nxt = next_turn(self.session, self.plan, job=_job(max_total=10))
        self.assertIsInstance(nxt, Question)


if __name__ == "__main__":
    unittest.main()
