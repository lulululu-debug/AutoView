"""Sprint 8.5 task 2 —— 裁判团聚合护栏。零 token (monkeypatch)。

- 默认关: EVAL_PANEL_ENABLED 未设 -> 零 panel 调用, 分数 = 公式路径
- 中位数聚合; 部分失败剩余继续; 全挂退回公式分 (fallback 链不断)
- 极差 >= 阈值 -> judge_disagreement 进 session.integrity_flags
- 裁判与生成分离: 与 llm.DEFAULT_MODEL 同名的 judge 被剔除

跑法: python -m unittest evals.test_panel_aggregation
"""
from __future__ import annotations

import os
import unittest

os.environ.pop("OPENAI_API_KEY", None)

from src.schemas import (  # noqa: E402
    AnswerAssessment,
    CandidateAnswer,
    Competency,
    InterviewSession,
    Question,
    QuestionCategory,
)


def _fixture():
    comp = Competency(competency_id="c1", name="技术深度", description="x")
    q = Question(
        competency_id="c1", text="讲讲你的性能优化。",
        category=QuestionCategory.PROJECT_EXPERIENCE,
    )
    session = InterviewSession(plan_id="p", job_id="j")
    ans = CandidateAnswer(question_id=q.question_id, text="加了缓存, P99 降了。")
    session.answers.append(ans)
    session.assessments.append(AnswerAssessment(
        question_id=q.question_id, sufficiency=0.5, confidence=0.7,
    ))
    return comp, q, session


class PanelTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("EVAL_PANEL_ENABLED", None)
        os.environ.pop("EVAL_PANEL_MODELS", None)
        self.addCleanup(lambda: os.environ.pop("EVAL_PANEL_ENABLED", None))
        self.addCleanup(lambda: os.environ.pop("EVAL_PANEL_MODELS", None))
        from src import llm
        from src.agents import evaluator
        self.llm = llm
        self.evaluator = evaluator

    def _patch(self, responder):
        orig = self.llm.complete
        self.llm.complete = responder
        self.addCleanup(lambda: setattr(self.llm, "complete", orig))

    def test_default_off_no_calls(self) -> None:
        calls = []
        self._patch(lambda s, u, **kw: calls.append(1) or '{"score": 90}')
        comp, q, session = _fixture()
        ds = self.evaluator._score_for_competency(comp, [q], session)
        self.assertEqual(ds.score, 50.0)   # 公式路径
        self.assertEqual(calls, [])        # panel 关: 无任何 judge 调用

    def test_median_and_disagreement(self) -> None:
        os.environ["EVAL_PANEL_ENABLED"] = "1"
        scores = iter(["50", "80", "90"])

        def responder(s, u, **kw):
            return '{"score": %s}' % next(scores)

        self._patch(responder)
        comp, q, session = _fixture()
        out = self.evaluator._panel_score_dimension(comp, [q], session)
        self.assertIsNotNone(out)
        median, disagreement = out
        self.assertEqual(median, 80.0)
        self.assertTrue(disagreement)  # 极差 40 >= 25

    def test_partial_failure_continues(self) -> None:
        os.environ["EVAL_PANEL_ENABLED"] = "1"
        replies = iter([RuntimeError("boom"), '{"score": 60}', '{"score": 70}'])

        def responder(s, u, **kw):
            r = next(replies)
            if isinstance(r, Exception):
                raise r
            return r

        self._patch(responder)
        comp, q, session = _fixture()
        median, disagreement = self.evaluator._panel_score_dimension(
            comp, [q], session,
        )
        self.assertEqual(median, 65.0)
        self.assertFalse(disagreement)

    def test_all_fail_falls_back_to_formula(self) -> None:
        os.environ["EVAL_PANEL_ENABLED"] = "1"

        def responder(s, u, **kw):
            raise RuntimeError("all down")

        self._patch(responder)
        comp, q, session = _fixture()
        self.assertIsNone(
            self.evaluator._panel_score_dimension(comp, [q], session),
        )

    def test_stub_counts_as_failure(self) -> None:
        os.environ["EVAL_PANEL_ENABLED"] = "1"
        self._patch(lambda s, u, **kw: "[stub] x")
        comp, q, session = _fixture()
        self.assertIsNone(
            self.evaluator._panel_score_dimension(comp, [q], session),
        )

    def test_generation_model_excluded(self) -> None:
        os.environ["EVAL_PANEL_MODELS"] = (
            f"{self.llm.DEFAULT_MODEL},gpt-4.1,gpt-4o"
        )
        models = self.evaluator._panel_models()
        self.assertNotIn(self.llm.DEFAULT_MODEL, models)
        self.assertEqual(len(models), 2)


if __name__ == "__main__":
    unittest.main()
