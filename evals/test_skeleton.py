"""第一个 eval —— 骨架结构性护栏 + 合规分区不变量。

定位:
- 这不是对 prompt 输出做语义打分(那要等 Sprint 3+ 接 RAG 后才有意义)。
- 这是结构性护栏: 改了 prompt 模板 / 改了 Planner 规则 / 不小心让软信号
  污染 overall —— 这里挂了, 说明动了 ARCHITECTURE.md / CLAUDE.md 里写过的契约。
- 用 stdlib unittest, 不引第三方依赖。

跑法:
    python -m unittest evals.test_skeleton                            # 全部
    python -m unittest discover -s evals                              # discover
    python -m unittest evals.test_skeleton.ComplianceInvariantTests   # 单类
- 不需要 PG/Redis 的 5 个 TestCase 永远会跑。
- 端到端那个 TestCase 在 POSTGRES_URL + REDIS_URL 都设置时才跑, 否则 skip。

强制 stub: 模块加载时清掉 ANTHROPIC_API_KEY, 避免本 eval 把真实 API 打飞了。
本 eval 检的是「结构」, 不是「LLM 输出语义」, 走 stub 已经足够。
"""
from __future__ import annotations

import os
import unittest

# 强制 stub 模式: 在 import agent 之前清 key, 防止 evaluator/planner 真的打 API。
os.environ.pop("ANTHROPIC_API_KEY", None)

from src.agents import analyzer, evaluator, planner  # noqa: E402
from src.schemas import (  # noqa: E402
    CandidateAnswer,
    CandidateProfile,
    EvaluationReport,
    InterviewSession,
    JobContext,
    QuestionCategory,
    SessionStatus,
    Signal,
    SignalKind,
    Turn,
    TurnRole,
)

# ---------- 固定输入 ----------
_JOB = JobContext(
    title="后端工程师",
    jd="负责核心交易系统的稳定性与性能, 熟悉分布式与数据库优化。",
    requirements=["分布式系统", "数据库优化", "沟通协作"],
)
_CANDIDATE = CandidateProfile(
    resume="张三 / 后端 / 4 年。订单 P99 优化 (800ms->350ms); 对账中台从 0 到 1。",
    projects=["订单 P99 优化", "对账中台从 0 到 1"],
)
_ANSWERS = [
    "做过一些性能优化, 主要是慢查询和缓存。",
    "去年大促前订单 P99 从 800ms 到 2s, 排查是索引失效 + 热点 key 击穿, "
    "我加回索引并改造为本地缓存 + Redis 二级缓存, P99 回到 350ms。",
    "我先用数据让对方理解我担心的点, 比如拉线上回放或 case, 再定义可灰度的中间方案。",
    "对账中台日处理 2 亿笔, 早期延迟 30 分钟+。我们改成分桶并行 + 幂等键 + Kafka 回放, "
    "延迟降到 3 分钟, 漏对率从 0.4‰ 降到 0.02‰。",
]


def _build_completed_session(plan):
    """按照 plan 的题目顺序, 用固定回答构造一个已答完的 session。"""
    session = InterviewSession(
        plan_id=plan.plan_id,
        job_id=_JOB.job_id,
        status=SessionStatus.COMPLETED,
    )
    questions = [q for r in plan.rounds for q in r.questions]
    for q, ans_text in zip(questions, _ANSWERS):
        session.history.append(
            Turn(role=TurnRole.INTERVIEWER, text=q.text, ref_id=q.question_id)
        )
        ans = CandidateAnswer(question_id=q.question_id, text=ans_text)
        session.answers.append(ans)
        session.history.append(
            Turn(role=TurnRole.CANDIDATE, text=ans_text, ref_id=ans.answer_id)
        )
    return session


# ---------- Plan 结构 ----------

class SkeletonPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = planner.plan(_JOB, _CANDIDATE)

    def test_plan_carries_job_id(self):
        self.assertEqual(self.plan.job_id, _JOB.job_id)

    def test_plan_shape_1_round_2_dims_4_questions(self):
        self.assertEqual(len(self.plan.rounds), 1, "Sprint 0 骨架: 1 轮")
        round0 = self.plan.rounds[0]
        self.assertEqual(len(round0.competencies), 2, "2 个考察维度")
        self.assertEqual(len(round0.questions), 4, "每维度 1 知识 + 1 项目深挖 = 4 题")

    def test_question_category_split(self):
        """两类题 KNOWLEDGE / PROJECT_EXPERIENCE 各占一半, 这是 CLAUDE.md 写过的契约。"""
        questions = self.plan.rounds[0].questions
        knowledge = [q for q in questions if q.category == QuestionCategory.KNOWLEDGE]
        project = [q for q in questions if q.category == QuestionCategory.PROJECT_EXPERIENCE]
        self.assertEqual(len(knowledge), 2)
        self.assertEqual(len(project), 2)

    def test_each_question_links_to_a_competency(self):
        comp_ids = {c.competency_id for c in self.plan.rounds[0].competencies}
        for q in self.plan.rounds[0].questions:
            self.assertIn(q.competency_id, comp_ids,
                         f"Question {q.question_id} 关联了未知 competency")


# ---------- Report 结构 ----------

class SkeletonReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = planner.plan(_JOB, _CANDIDATE)
        cls.session = _build_completed_session(cls.plan)
        cls.report = evaluator.evaluate(cls.session, cls.plan, signals=[])

    def test_report_is_evaluation_report(self):
        self.assertIsInstance(self.report, EvaluationReport)
        self.assertEqual(self.report.session_id, self.session.session_id)

    def test_content_scores_cover_every_competency(self):
        comp_ids = {c.competency_id for c in self.plan.rounds[0].competencies}
        scored_ids = {s.competency_id for s in self.report.content_scores}
        self.assertEqual(comp_ids, scored_ids, "每个维度都应当有一个 content score")

    def test_overall_in_valid_range(self):
        self.assertGreaterEqual(self.report.overall, 0.0)
        self.assertLessEqual(self.report.overall, 100.0)

    def test_each_content_score_has_evidence(self):
        for s in self.report.content_scores:
            self.assertGreater(
                len(s.evidence), 0, f"competency {s.competency_id} 应有证据"
            )

    def test_needs_human_review_default_true(self):
        """合规约束: AI 报告默认需人工复核 (ARCHITECTURE.md §7)。"""
        self.assertTrue(self.report.needs_human_review)

    def test_no_performance_observations_when_no_signals(self):
        """骨架阶段 analyzer 是空占位, 不该有软信号; 即便日后 analyzer 真接入,
        没传信号时 performance_observations 也必须是空。"""
        self.assertEqual(self.report.performance_observations, [])

    def test_summary_non_empty(self):
        self.assertTrue(self.report.summary.strip(), "summary 不能为空")


# ---------- 合规分区不变量(最重要的护栏) ----------

class ComplianceInvariantTests(unittest.TestCase):
    """ARCHITECTURE.md §7: overall 只能由 content_scores 加权得出,
    多模态软信号永远不进 overall。这条挂了就是合规事故。"""

    @classmethod
    def setUpClass(cls):
        cls.plan = planner.plan(_JOB, _CANDIDATE)
        cls.session = _build_completed_session(cls.plan)

    def test_overall_unchanged_by_signals(self):
        report_no = evaluator.evaluate(self.session, self.plan, signals=[])
        report_with = evaluator.evaluate(
            self.session, self.plan,
            signals=[
                Signal(kind=SignalKind.GAZE, value="眼神接触少", confidence=0.9, source="test"),
                Signal(kind=SignalKind.TONE, value="语气紧张",    confidence=0.8, source="test"),
                Signal(kind=SignalKind.LANGUAGE, value="语速偏快", confidence=0.7, source="test"),
            ],
        )
        self.assertEqual(
            report_no.overall, report_with.overall,
            "overall 不能被 performance 软信号改变 —— 合规护栏被破坏",
        )

    def test_signals_only_land_in_performance_observations(self):
        signals = [Signal(kind=SignalKind.GAZE, value="x", confidence=0.5, source="test")]
        report = evaluator.evaluate(self.session, self.plan, signals=signals)
        # 1 个软信号 -> 1 条 performance_observation
        self.assertEqual(len(report.performance_observations), 1)
        # content_scores 数量不应被信号污染
        self.assertEqual(
            len(report.content_scores), len(self.plan.rounds[0].competencies)
        )


# ---------- Analyzer 骨架阶段必须空 ----------

class AnalyzerSkeletonTest(unittest.TestCase):
    """骨架阶段 Analyzer 必须返回空。
    真正实现要等 Sprint 7, 那时还要带置信度 + 默认关闭 + 偏见审计 (ARCHITECTURE.md §7)。
    在那之前任何输出都是越权。"""

    def test_analyzer_returns_empty_list(self):
        session = InterviewSession(plan_id="p", job_id="j", status=SessionStatus.COMPLETED)
        self.assertEqual(analyzer.analyze(session), [])


# ---------- 端到端冒烟(需要 PG + Redis) ----------

@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL 才能跑 orchestrator 端到端",
)
class OrchestratorEndToEndTest(unittest.TestCase):
    """run_interview + finalize 走完一遍, 验证报告归档到 PG。
    与上面的纯函数 eval 互补: 一头是 agent 契约, 一头是 orchestrator 编排正确性。"""

    def test_run_interview_archives_report_to_postgres(self):
        from src.db import load_report
        from src.orchestrator import run_interview

        report = run_interview(_JOB, _CANDIDATE, _ANSWERS)
        self.assertIsInstance(report, EvaluationReport)
        self.assertEqual(len(report.content_scores), 2)
        self.assertTrue(report.needs_human_review)

        from_pg = load_report(report.report_id)
        self.assertIsNotNone(from_pg, "finalize 后报告应在 Postgres 查到")
        self.assertEqual(from_pg.overall, report.overall)
        self.assertEqual(from_pg.session_id, report.session_id)


if __name__ == "__main__":
    unittest.main()
