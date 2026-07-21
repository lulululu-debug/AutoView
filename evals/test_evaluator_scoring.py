"""Sprint 6.5 —— Evaluator 维度分双路径护栏。

背景 (sim 冒烟结案): 旧启发式在真实长答案下饱和于 95, 强弱同分。
修复: assessment 驱动 (score = 100 × mean(每题 best sufficiency)),
启发式降级为 assessments 缺失时的保底 —— 双路径并存, 本文件锁住:

- 映射规则: 对**实际被问过的题** (有 assessment) 求均值 / 同题取 max;
  没被问到的题不记 0 (CompletionPolicy 提前结束是系统行为, 不许反罚候选人,
  覆盖缺口由 coverage + evidence_insufficient 表达, 不双重计罚)
- 回退规则: assessments 空 -> 启发式 (行为与 Sprint 0 完全一致)
- 边界: 该维度无题 -> 0 分老路径; self_intro (competency_id=None) 不进任何维度
- 区分性: 高 sufficiency 会话得分显著高于低 sufficiency 会话 (同样的答案文本!)
  —— 这正是启发式做不到、也是本次修复的意义

跑法: python -m unittest evals.test_evaluator_scoring
无需任何 infra; 全部走内存构造。
"""
from __future__ import annotations

import os
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src.agents.evaluator import (  # noqa: E402
    _assessment_score,
    _heuristic_score,
    _score_for_competency,
)
from src.schemas import (  # noqa: E402
    AnswerAssessment,
    CandidateAnswer,
    Competency,
    InterviewSession,
    Question,
)

_COMP = Competency(
    competency_id="comp:tech", name="技术深度",
    description="技术方案与工程判断", weight=2.0,
)


def _q(qid: str, cid: str | None = "comp:tech") -> Question:
    return Question(question_id=qid, competency_id=cid, text=f"题目 {qid}")


def _ans(qid: str, text: str) -> CandidateAnswer:
    return CandidateAnswer(question_id=qid, text=text)


def _assess(qid: str, suff: float) -> AnswerAssessment:
    return AnswerAssessment(question_id=qid, sufficiency=suff, confidence=0.8)


def _session(answers=(), assessments=()) -> InterviewSession:
    s = InterviewSession(plan_id="p", job_id="j")
    s.answers = list(answers)
    s.assessments = list(assessments)
    return s


class AssessmentScoreTests(unittest.TestCase):
    """assessment 驱动路径的映射规则。"""

    def test_mean_of_per_question_sufficiency(self) -> None:
        qs = [_q("q1"), _q("q2")]
        sess = _session(
            answers=[_ans("q1", "回答一"), _ans("q2", "回答二")],
            assessments=[_assess("q1", 0.9), _assess("q2", 0.5)],
        )
        ds = _assessment_score(_COMP, qs, sess)
        assert ds is not None
        self.assertEqual(ds.score, 70.0)  # (0.9 + 0.5) / 2 × 100

    def test_unasked_question_excluded_not_zeroed(self) -> None:
        """plan 里有但没被问到的题**不进均值**: 提前结束是系统行为,
        不许记 0 反罚候选人 (覆盖缺口由 coverage 表达)。"""
        qs = [_q("q1"), _q("q2"), _q("q3")]
        sess = _session(
            answers=[_ans("q1", "只被问了一题")],
            assessments=[_assess("q1", 0.9)],
        )
        ds = _assessment_score(_COMP, qs, sess)
        assert ds is not None
        self.assertEqual(ds.score, 90.0)  # 只对被问过的 q1 求均值

    def test_no_asked_questions_scores_zero(self) -> None:
        """该维度有题但一道没被问到 -> 0.0 (无证据, 与 coverage=0 一致)。"""
        qs = [_q("q1")]
        sess = _session(
            assessments=[_assess("q-other-comp", 0.9)],  # 全局有评估, 本维度没有
        )
        ds = _assessment_score(_COMP, qs, sess)
        assert ds is not None
        self.assertEqual(ds.score, 0.0)
        self.assertEqual(ds.evidence, ["未收集到该维度的有效回答"])

    def test_same_question_takes_max(self) -> None:
        """原答 + 追问后再评, 取 max —— 与 coverage 同口径。"""
        qs = [_q("q1")]
        sess = _session(
            answers=[_ans("q1", "第一次"), _ans("q1", "追问后补充")],
            assessments=[_assess("q1", 0.4), _assess("q1", 0.8)],
        )
        ds = _assessment_score(_COMP, qs, sess)
        assert ds is not None
        self.assertEqual(ds.score, 80.0)

    def test_other_competency_assessment_ignored(self) -> None:
        qs = [_q("q1")]
        sess = _session(
            answers=[_ans("q1", "答")],
            assessments=[_assess("q1", 0.6), _assess("q-other", 1.0)],
        )
        ds = _assessment_score(_COMP, qs, sess)
        assert ds is not None
        self.assertEqual(ds.score, 60.0)

    def test_no_assessments_returns_none(self) -> None:
        qs = [_q("q1")]
        sess = _session(answers=[_ans("q1", "答")])
        self.assertIsNone(_assessment_score(_COMP, qs, sess))

    def test_no_questions_for_comp_returns_none(self) -> None:
        sess = _session(assessments=[_assess("q1", 0.9)])
        self.assertIsNone(_assessment_score(_COMP, [_q("q1", cid=None)], sess))


class DualPathTests(unittest.TestCase):
    """入口的双路径选择 + 回退行为与 Sprint 0 完全一致。"""

    def test_fallback_to_heuristic_when_no_assessments(self) -> None:
        qs = [_q("q1")]
        long_answer = "我们当时用了分库分表, 比如按用户维度拆, 结果 P99 降了。" * 10
        sess = _session(answers=[_ans("q1", long_answer)])
        via_entry = _score_for_competency(_COMP, qs, sess)
        via_heuristic = _heuristic_score(_COMP, qs, sess)
        self.assertEqual(via_entry.score, via_heuristic.score)
        self.assertEqual(via_entry.evidence, via_heuristic.evidence)

    def test_entry_prefers_assessments(self) -> None:
        qs = [_q("q1")]
        long_answer = "很长的回答" * 100  # 启发式会给 95
        sess = _session(
            answers=[_ans("q1", long_answer)],
            assessments=[_assess("q1", 0.3)],
        )
        ds = _score_for_competency(_COMP, qs, sess)
        self.assertEqual(ds.score, 30.0)  # 走 assessment, 不受答案长度蛊惑

    def test_heuristic_saturation_documented(self) -> None:
        """把启发式的饱和行为钉在这里: 130+ 字 + 5 词命中 = 恒 95。
        这条挂了说明有人动了保底公式 —— 动之前先看 sim 报告的区分度指标。"""
        qs = [_q("q1")]
        text = "比如我们当时用了选择结果" + "字" * 130
        sess = _session(answers=[_ans("q1", text)])
        self.assertEqual(_heuristic_score(_COMP, qs, sess).score, 95.0)


class DiscriminationTests(unittest.TestCase):
    """修复的意义: 同样的答案文本, 不同 sufficiency 必须拉开分差。"""

    def test_same_text_different_sufficiency_separates(self) -> None:
        qs = [_q("q1"), _q("q2")]
        answers = [_ans("q1", "回答" * 100), _ans("q2", "回答" * 100)]
        strong = _session(
            answers=answers,
            assessments=[_assess("q1", 0.9), _assess("q2", 0.85)],
        )
        weak = _session(
            answers=answers,
            assessments=[_assess("q1", 0.3), _assess("q2", 0.2)],
        )
        s = _score_for_competency(_COMP, qs, strong).score
        w = _score_for_competency(_COMP, qs, weak).score
        self.assertGreater(s - w, 15.0, f"区分度不足: strong={s} weak={w}")


if __name__ == "__main__":
    unittest.main()
