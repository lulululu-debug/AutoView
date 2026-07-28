"""Sprint 8.5 task 1 —— per-question rubric 护栏。零 token。

- planner: stub 走分类别模板 rubric (3-6 条, 确定性); self_intro/lazy 占位
  跳过; 已有 rubric 绝不改 (随 plan 固定)
- assessor: 有 rubric 才拼条件块 (无 rubric 题 prompt 逐字节不变 —— 校准
  金标不受扰); rubric_hits 长度不匹配弃用
- 评分: 命中率按 0.7/0.3 组合; 无 hits 退纯 sufficiency (老 plan 兼容);
  hits 与分数单调

跑法: python -m unittest evals.test_rubric_scoring
"""
from __future__ import annotations

import os
import unittest

os.environ.pop("OPENAI_API_KEY", None)

from src.schemas import (  # noqa: E402
    AnswerAssessment,
    Competency,
    InterviewSession,
    JobContext,
    Question,
    QuestionCategory,
)


def _q(rubric: list[str] | None = None, cid: str = "c1") -> Question:
    return Question(
        competency_id=cid, text="讲讲你的性能优化。",
        category=QuestionCategory.PROJECT_EXPERIENCE,
        rubric=rubric or [],
    )


class PlannerRubricTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)
        from src.agents import planner
        self.planner = planner

    def test_stub_fallback_rubric_deterministic(self) -> None:
        job = JobContext(title="后端", jd="x")
        q = Question(text="MySQL 隔离级别?", category=QuestionCategory.KNOWLEDGE)
        r1 = self.planner._rubric_for_question(job, q)
        r2 = self.planner._rubric_for_question(job, q)
        self.assertEqual(r1, r2)
        self.assertTrue(3 <= len(r1) <= 6)

    def test_attach_skips_intro_lazy_and_existing(self) -> None:
        from src.schemas import InterviewPlan, InterviewRound
        comp = Competency(competency_id="c1", name="技术", description="x")
        intro = Question(text="自我介绍", category=QuestionCategory.SELF_INTRO)
        lazy = Question(
            competency_id="c1", text="", lazy=True,
            category=QuestionCategory.PROJECT_EXPERIENCE,
        )
        fixed = _q(rubric=["已有条目A", "已有条目B", "已有条目C"])
        fresh = Question(
            competency_id="c1", text="限流方案?",
            category=QuestionCategory.KNOWLEDGE,
        )
        plan = InterviewPlan(
            job_id="j",
            rounds=[InterviewRound(
                index=0, title="t", competencies=[comp],
                questions=[intro, lazy, fixed, fresh],
            )],
            competencies=[comp],
        )
        out = self.planner._attach_rubrics(plan, JobContext(title="t", jd="x"))
        qs = out.rounds[0].questions
        self.assertEqual(qs[0].rubric, [])                    # intro 不给
        self.assertEqual(qs[1].rubric, [])                    # lazy 占位跳过
        self.assertEqual(qs[2].rubric, fixed.rubric)          # 已有不改
        self.assertTrue(qs[3].rubric)                         # 新题补上


class AssessorRubricTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)
        from src import llm
        from src.agents import assessor
        self.llm = llm
        self.assessor = assessor

    def _spy(self, response: str):
        captured = {}

        def spy(system, user, **kw):
            captured["user"] = user
            return response

        orig = self.llm.complete
        self.llm.complete = spy
        self.addCleanup(lambda: setattr(self.llm, "complete", orig))
        return captured

    _JSON = (
        '{"sufficiency": 0.5, "confidence": 0.6, "missing_signals": [],'
        ' "strengths": [], "concerns": [], "followup_goal": "",'
        ' "stop_reason": "", "covered_aspects": [], "rubric_hits": %s}'
    )

    def test_rubric_block_conditional(self) -> None:
        from src.schemas import CandidateAnswer
        cap = self._spy(self._JSON % "[true, false, true]")
        q = _q(rubric=["条目甲", "条目乙", "条目丙"])
        a = CandidateAnswer(question_id=q.question_id, text="x")
        out = self.assessor._assess_via_llm(q, a, aspects=[])
        self.assertIn("checklist", cap["user"])
        self.assertIn("条目乙", cap["user"])
        self.assertEqual(out.rubric_hits, [True, False, True])

        # 无 rubric 题: prompt 不含 checklist (校准金标 prompt 不变)
        cap2 = self._spy(self._JSON % "[]")
        q2 = _q()
        out2 = self.assessor._assess_via_llm(
            q2, CandidateAnswer(question_id=q2.question_id, text="x"), aspects=[],
        )
        self.assertNotIn("checklist", cap2["user"])
        self.assertEqual(out2.rubric_hits, [])

    def test_length_mismatch_discarded(self) -> None:
        from src.schemas import CandidateAnswer
        self._spy(self._JSON % "[true]")  # rubric 3 条却只返 1 个
        q = _q(rubric=["a1", "a2", "a3"])
        out = self.assessor._assess_via_llm(
            q, CandidateAnswer(question_id=q.question_id, text="x"), aspects=[],
        )
        self.assertEqual(out.rubric_hits, [])


class ScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)
        from src.agents import evaluator
        self.evaluator = evaluator

    def _score(self, q: Question, hits: list[bool], suf: float = 0.5) -> float:
        comp = Competency(competency_id="c1", name="技术", description="x")
        session = InterviewSession(plan_id="p", job_id="j")
        session.assessments.append(AnswerAssessment(
            question_id=q.question_id, sufficiency=suf, confidence=0.7,
            rubric_hits=hits,
        ))
        ds = self.evaluator._score_for_competency(comp, [q], session)
        return ds.score

    def test_combined_weighting(self) -> None:
        q = _q(rubric=["a", "b", "c", "d"])
        # suf 0.5, 命中 2/4=0.5 -> 0.7*0.5+0.3*0.5 = 0.5 -> 50.0
        self.assertEqual(self._score(q, [True, True, False, False]), 50.0)
        # 命中 4/4 -> 0.7*0.5+0.3*1.0 = 0.65 -> 65.0
        self.assertEqual(self._score(q, [True] * 4), 65.0)

    def test_monotonic_in_hits(self) -> None:
        q = _q(rubric=["a", "b", "c", "d"])
        scores = [
            self._score(q, [True] * k + [False] * (4 - k)) for k in range(5)
        ]
        self.assertEqual(scores, sorted(scores))

    def test_no_hits_falls_back_to_sufficiency(self) -> None:
        # 有 rubric 但 hits 为空 (启发式/弃用) -> 纯 sufficiency
        q = _q(rubric=["a", "b", "c"])
        self.assertEqual(self._score(q, []), 50.0)
        # 老 plan 无 rubric -> 同样纯 sufficiency
        self.assertEqual(self._score(_q(), []), 50.0)


if __name__ == "__main__":
    unittest.main()
