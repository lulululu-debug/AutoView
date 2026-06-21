"""Sprint 5.5 Planner track 配比 + lazy 占位 + resolve 不动 lazy 标记的护栏。

定位:
- test_skeleton 已覆盖 lateral 路径上的"plan 形状 + competency 链接 + lazy 仅在
  project stage";本文件交叉覆盖 campus 路径, 并校验 Planner 顶层契约:
  * stage 序列与 track 配比 (campus 7 题 / lateral 7 题, 数量 + 顺序)
  * plan.competencies 顶层非空, 去重一致
  * self_intro 题 competency_id is None, 与 content_scores 解耦
  * project 题 plan 阶段 lazy=True text=""
  * resolve_lazy_questions 后 project 题 text 非空, lazy 仍 True (静态信号)
  * scenario 题 category 正确
- 全程走 stub (无 LLM_KEY / 无 Milvus 命中), 走 _knowledge_fallback /
  _scenario_fallback / _project_fallback 路径, 不烧 API 调用。

跑法:
    python -m unittest evals.test_planner_track
"""
from __future__ import annotations

import os
import unittest
from collections import Counter

os.environ.pop("OPENAI_API_KEY", None)

from src.agents import planner  # noqa: E402
from src.schemas import (  # noqa: E402
    CandidateProfile,
    InterviewStage,
    JobContext,
    QuestionCategory,
    Track,
)

_CANDIDATE = CandidateProfile(
    candidate_id="cand-track-test",
    resume="张三 / 后端 / 4 年。订单 P99 优化; 对账中台从 0 到 1。",
    projects=["订单 P99 优化", "对账中台"],
)


def _job(track: Track) -> JobContext:
    return JobContext(
        title="后端工程师",
        jd="负责核心交易系统的稳定性与性能。",
        requirements=["分布式", "数据库优化"],
        track=track,
    )


class CampusTrackShapeTests(unittest.TestCase):
    """campus 配比: self_intro 1 + knowledge 3 + project 2 + scenario 1 = 7 题。"""

    @classmethod
    def setUpClass(cls):
        cls.plan = planner.plan(_job(Track.CAMPUS), _CANDIDATE)

    def test_stage_sequence(self):
        stages = [r.stage for r in self.plan.rounds]
        self.assertEqual(stages, [
            InterviewStage.SELF_INTRO,
            InterviewStage.KNOWLEDGE,
            InterviewStage.PROJECT,
            InterviewStage.SCENARIO,
        ], "campus 顺序: 自我介绍 -> 知识 -> 项目 -> 场景")

    def test_question_counts_per_stage(self):
        """Sprint 5.9: tech-campus 升级到 21 主问题 (1 + 12 + 5 + 3)。"""
        counts = {r.stage: len(r.questions) for r in self.plan.rounds}
        self.assertEqual(counts[InterviewStage.SELF_INTRO], 1)
        self.assertEqual(counts[InterviewStage.KNOWLEDGE], 12)
        self.assertEqual(counts[InterviewStage.PROJECT], 5)
        self.assertEqual(counts[InterviewStage.SCENARIO], 3)
        self.assertEqual(sum(counts.values()), 21)


class LateralTrackShapeTests(unittest.TestCase):
    """Sprint 5.9: tech-lateral 升级到 22 主问题: self_intro 1 + project 11 +
    scenario 6 + knowledge 4. 项目重 + 场景重, knowledge 只 4 道核心知识点。"""

    @classmethod
    def setUpClass(cls):
        cls.plan = planner.plan(_job(Track.LATERAL), _CANDIDATE)

    def test_stage_sequence(self):
        stages = [r.stage for r in self.plan.rounds]
        self.assertEqual(stages, [
            InterviewStage.SELF_INTRO,
            InterviewStage.PROJECT,
            InterviewStage.SCENARIO,
            InterviewStage.KNOWLEDGE,
        ], "lateral 顺序: 自我介绍 -> 项目 -> 场景 -> 知识")

    def test_question_counts_per_stage(self):
        counts = {r.stage: len(r.questions) for r in self.plan.rounds}
        self.assertEqual(counts[InterviewStage.SELF_INTRO], 1)
        self.assertEqual(counts[InterviewStage.PROJECT], 11)
        self.assertEqual(counts[InterviewStage.SCENARIO], 6)
        self.assertEqual(counts[InterviewStage.KNOWLEDGE], 4)
        self.assertEqual(sum(counts.values()), 22)


class LazyInvariantTests(unittest.TestCase):
    """lazy 占位 + resolve 后静态信号保留, 是 Sprint 5.5 的核心数据契约。"""

    @classmethod
    def setUpClass(cls):
        cls.job = _job(Track.CAMPUS)
        cls.plan_raw = planner.plan(cls.job, _CANDIDATE)
        cls.plan_resolved = planner.resolve_lazy_questions(
            cls.plan_raw, cls.job, _CANDIDATE,
        )

    def _project_questions(self, plan):
        return [
            q for r in plan.rounds for q in r.questions
            if q.category is QuestionCategory.PROJECT_EXPERIENCE
        ]

    def test_project_questions_lazy_with_empty_text_in_raw_plan(self):
        for q in self._project_questions(self.plan_raw):
            self.assertTrue(q.lazy, "project 题应当是 lazy 占位")
            self.assertEqual(q.text, "", "lazy 占位题 text 应当为空 (动态信号)")

    def test_non_project_questions_are_not_lazy(self):
        for r in self.plan_raw.rounds:
            for q in r.questions:
                if r.stage is InterviewStage.PROJECT:
                    continue
                self.assertFalse(
                    q.lazy,
                    f"{r.stage} 的题不应是 lazy (现在: {q.category})",
                )

    def test_resolve_fills_text_but_keeps_lazy_true(self):
        for q in self._project_questions(self.plan_resolved):
            self.assertTrue(
                q.lazy,
                "lazy 是静态信号, resolve 后不被清零, 作 HR 审计",
            )
            self.assertNotEqual(
                q.text, "",
                "resolve 后 project 题 text 应当非空",
            )

    def test_resolve_preserves_competency_id(self):
        """competency 槽位在 plan 阶段就预定, resolve 只换 text + chunks,
        不应动 competency_id (会破坏 coverage 计算)。"""
        raw = self._project_questions(self.plan_raw)
        resolved = self._project_questions(self.plan_resolved)
        self.assertEqual(len(raw), len(resolved))
        for r, s in zip(raw, resolved):
            self.assertEqual(r.competency_id, s.competency_id)


class SelfIntroSemanticsTests(unittest.TestCase):
    """self_intro 题不挂 competency, 不进任何 DimensionScore。"""

    @classmethod
    def setUpClass(cls):
        cls.plan_campus = planner.plan(_job(Track.CAMPUS), _CANDIDATE)
        cls.plan_lateral = planner.plan(_job(Track.LATERAL), _CANDIDATE)

    def _intro_questions(self, plan):
        return [
            q for r in plan.rounds for q in r.questions
            if q.category is QuestionCategory.SELF_INTRO
        ]

    def test_self_intro_competency_is_none(self):
        for plan in (self.plan_campus, self.plan_lateral):
            intros = self._intro_questions(plan)
            self.assertEqual(len(intros), 1)
            self.assertIsNone(
                intros[0].competency_id,
                "self_intro 题 competency_id 必须 None (合规: 不进 content_scores)",
            )

    def test_self_intro_text_non_empty(self):
        for plan in (self.plan_campus, self.plan_lateral):
            intros = self._intro_questions(plan)
            self.assertTrue(intros[0].text.strip(), "self_intro 题应有固定提示文本")


class PlanCompetenciesTopLevelTests(unittest.TestCase):
    """plan.competencies 顶层非空, 去重, 与 round.competencies 一致。"""

    @classmethod
    def setUpClass(cls):
        cls.plan = planner.plan(_job(Track.LATERAL), _CANDIDATE)

    def test_top_level_competencies_present(self):
        self.assertGreater(len(self.plan.competencies), 0)

    def test_top_level_competencies_deduplicated(self):
        ids = [c.competency_id for c in self.plan.competencies]
        self.assertEqual(len(ids), len(set(ids)), "顶层 competencies 必须去重")

    def test_all_question_competencies_in_top_level(self):
        """每道非 self_intro 题的 competency_id 必须在 plan.competencies 中能找到。"""
        top_ids = {c.competency_id for c in self.plan.competencies}
        for r in self.plan.rounds:
            for q in r.questions:
                if q.category is QuestionCategory.SELF_INTRO:
                    continue
                self.assertIn(
                    q.competency_id, top_ids,
                    f"Question {q.question_id} ({q.category}) 挂的 competency "
                    "不在 plan.competencies 顶层",
                )


class ScenarioCategoryTests(unittest.TestCase):
    """scenario 题应该带 SCENARIO category, 跟 knowledge / project 区分。"""

    def test_scenario_questions_have_scenario_category(self):
        for track in (Track.CAMPUS, Track.LATERAL):
            p = planner.plan(_job(track), _CANDIDATE)
            scenario_round = next(
                (r for r in p.rounds if r.stage is InterviewStage.SCENARIO),
                None,
            )
            self.assertIsNotNone(scenario_round, f"{track} 应有 scenario stage")
            cats = {q.category for q in scenario_round.questions}
            self.assertEqual(
                cats, {QuestionCategory.SCENARIO},
                f"{track} scenario stage 的所有题 category 必须是 SCENARIO",
            )


class CategoryDistributionTests(unittest.TestCase):
    """4 类 category 数量与 track 配比一致 (跨 stage 计数)。"""

    def test_campus_category_distribution(self):
        p = planner.plan(_job(Track.CAMPUS), _CANDIDATE)
        by_cat = Counter(q.category for r in p.rounds for q in r.questions)
        # Sprint 5.9 tech-campus 配比
        self.assertEqual(by_cat[QuestionCategory.SELF_INTRO], 1)
        self.assertEqual(by_cat[QuestionCategory.KNOWLEDGE], 12)
        self.assertEqual(by_cat[QuestionCategory.PROJECT_EXPERIENCE], 5)
        self.assertEqual(by_cat[QuestionCategory.SCENARIO], 3)

    def test_lateral_category_distribution(self):
        p = planner.plan(_job(Track.LATERAL), _CANDIDATE)
        by_cat = Counter(q.category for r in p.rounds for q in r.questions)
        # Sprint 5.9 tech-lateral 配比
        self.assertEqual(by_cat[QuestionCategory.SELF_INTRO], 1)
        self.assertEqual(by_cat[QuestionCategory.KNOWLEDGE], 4)
        self.assertEqual(by_cat[QuestionCategory.PROJECT_EXPERIENCE], 11)
        self.assertEqual(by_cat[QuestionCategory.SCENARIO], 6)

    def test_lateral_knowledge_less_than_project(self):
        """sprint 5.5 核心设计意图: lateral (社招) project 重于 knowledge,
        与 campus (校招) knowledge 重于 project 形成对照。
        knowledge 题数严格少于 project 题数。"""
        p = planner.plan(_job(Track.LATERAL), _CANDIDATE)
        by_cat = Counter(q.category for r in p.rounds for q in r.questions)
        self.assertLess(
            by_cat[QuestionCategory.KNOWLEDGE],
            by_cat[QuestionCategory.PROJECT_EXPERIENCE],
            "lateral: knowledge 应严格少于 project (社招重项目)",
        )

    def test_campus_knowledge_more_than_project(self):
        """对照: campus knowledge 重于 project (校招重基础知识)。"""
        p = planner.plan(_job(Track.CAMPUS), _CANDIDATE)
        by_cat = Counter(q.category for r in p.rounds for q in r.questions)
        self.assertGreater(
            by_cat[QuestionCategory.KNOWLEDGE],
            by_cat[QuestionCategory.PROJECT_EXPERIENCE],
            "campus: knowledge 应严格多于 project (校招重知识)",
        )


if __name__ == "__main__":
    unittest.main()
