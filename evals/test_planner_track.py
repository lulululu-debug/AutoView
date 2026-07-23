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
        """Sprint 6.5 F5: tech-campus 收敛到 12 主问题 (1 + 6 + 3 + 2),
        cap 15 预留 3 追问 —— 改这里必须同看 CompletionPolicy 并跑 sim 复验。"""
        counts = {r.stage: len(r.questions) for r in self.plan.rounds}
        self.assertEqual(counts[InterviewStage.SELF_INTRO], 1)
        self.assertEqual(counts[InterviewStage.KNOWLEDGE], 6)
        self.assertEqual(counts[InterviewStage.PROJECT], 3)
        self.assertEqual(counts[InterviewStage.SCENARIO], 2)
        self.assertEqual(sum(counts.values()), 12)


class LateralTrackShapeTests(unittest.TestCase):
    """Sprint 6.5 F5: tech-lateral 收敛到 12 主问题: self_intro 1 + project 6 +
    scenario 3 + knowledge 2. 项目重 + 场景重, cap 15 预留 3 追问。"""

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
        self.assertEqual(counts[InterviewStage.PROJECT], 6)
        self.assertEqual(counts[InterviewStage.SCENARIO], 3)
        self.assertEqual(counts[InterviewStage.KNOWLEDGE], 2)
        self.assertEqual(sum(counts.values()), 12)


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
        self.assertEqual(by_cat[QuestionCategory.KNOWLEDGE], 6)
        self.assertEqual(by_cat[QuestionCategory.PROJECT_EXPERIENCE], 3)
        self.assertEqual(by_cat[QuestionCategory.SCENARIO], 2)

    def test_lateral_category_distribution(self):
        p = planner.plan(_job(Track.LATERAL), _CANDIDATE)
        by_cat = Counter(q.category for r in p.rounds for q in r.questions)
        # Sprint 5.9 tech-lateral 配比
        self.assertEqual(by_cat[QuestionCategory.SELF_INTRO], 1)
        self.assertEqual(by_cat[QuestionCategory.KNOWLEDGE], 2)
        self.assertEqual(by_cat[QuestionCategory.PROJECT_EXPERIENCE], 6)
        self.assertEqual(by_cat[QuestionCategory.SCENARIO], 3)

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


class FallbackRotationTests(unittest.TestCase):
    """LLM 完全不可用时, 同 competency 多道题的 fallback 应轮换模板池而非复读
    同一句 (实战 bug: 6/23 冒烟面试 6 道 project 题全部同一句 fallback)。
    monkeypatch 强制 llm/embed 走 stub, 不依赖环境有没有 key/Milvus。"""

    @classmethod
    def setUpClass(cls):
        import src.embeddings as emb_mod
        import src.llm as llm_mod
        cls._orig_complete = llm_mod.complete
        cls._orig_embed = emb_mod.embed
        llm_mod.complete = lambda system, user, **kw: "[stub] forced"
        emb_mod.embed = lambda text, **kw: [0.0] * 1536
        cls.plan_campus = planner.plan(_job(Track.CAMPUS), _CANDIDATE)
        cls.plan_lateral = planner.resolve_lazy_questions(
            planner.plan(_job(Track.LATERAL), _CANDIDATE),
            _job(Track.LATERAL), _CANDIDATE,
        )

    @classmethod
    def tearDownClass(cls):
        import src.embeddings as emb_mod
        import src.llm as llm_mod
        llm_mod.complete = cls._orig_complete
        emb_mod.embed = cls._orig_embed

    @staticmethod
    def _groups(plan, category) -> list[tuple[str | None, int, int]]:
        """[(competency_id, 组内题数, 去重后数)], 组 = (round, competency)。"""
        out = []
        for r in plan.rounds:
            by_comp: dict[str | None, list[str]] = {}
            for q in r.questions:
                if q.category is category and q.text:
                    by_comp.setdefault(q.competency_id, []).append(q.text)
            for comp_id, texts in by_comp.items():
                out.append((comp_id, len(texts), len(set(texts))))
        return out

    @staticmethod
    def _pool_size(pools: tuple[tuple[str, ...], tuple[str, ...]], comp_id) -> int:
        tech_pool, comm_pool = pools
        return len(tech_pool if comp_id == planner.COMPETENCY_TECH_ID else comm_pool)

    def _assert_rotates(self, groups, pools, label: str):
        self.assertTrue(groups, f"{label}: 应有题目")
        for comp_id, n, distinct in groups:
            if n <= 1:
                continue
            expected = min(n, self._pool_size(pools, comp_id))
            self.assertEqual(
                distinct, expected,
                f"{label} fallback 应轮换模板池: comp={comp_id} "
                f"{n} 题只有 {distinct} 种文案 (期望 {expected})",
            )

    def test_knowledge_fallback_rotates(self):
        self._assert_rotates(
            self._groups(self.plan_campus, QuestionCategory.KNOWLEDGE),
            (planner._KNOWLEDGE_FALLBACK_TECH, planner._KNOWLEDGE_FALLBACK_COMM),
            "knowledge",
        )

    def test_project_fallback_rotates(self):
        self._assert_rotates(
            self._groups(self.plan_lateral, QuestionCategory.PROJECT_EXPERIENCE),
            (planner._PROJECT_FALLBACK_TECH, planner._PROJECT_FALLBACK_COMM),
            "project",
        )

    def test_scenario_fallback_rotates(self):
        self._assert_rotates(
            self._groups(self.plan_lateral, QuestionCategory.SCENARIO),
            (planner._SCENARIO_FALLBACK_TECH, planner._SCENARIO_FALLBACK_COMM),
            "scenario",
        )


class SectionAssignmentTests(unittest.TestCase):
    """Sprint F: candidate.sections 有 deep-dive 段 (project/internship/work)
    时, resolve_lazy 按段轮询定向出题 —— 一段一题, trace 记 section_title;
    embedding stub → 段保持文档顺序 (确定性)。无 sections 的老路径由
    LazyResolveTests 锁, 不受影响。"""

    @classmethod
    def setUpClass(cls):
        import src.embeddings as emb_mod
        import src.llm as llm_mod
        from src.schemas import ResumeSection
        cls._orig_complete = llm_mod.complete
        cls._orig_embed = emb_mod.embed
        # LLM 可用 (非 stub): project 题应走 resume_section 路径
        llm_mod.complete = (
            lambda system, user, **kw: "针对这段经历, 你做的关键技术决策是什么?"
        )
        emb_mod.embed = lambda text, **kw: [0.0] * 1536
        cls.cand = CandidateProfile(
            candidate_id="cand-sections",
            resume="张三 / 后端",
            sections=[
                ResumeSection(type="personal_info", title="个人信息", text="张三"),
                ResumeSection(type="project", title="项目A", text="项目A 详情"),
                ResumeSection(type="project", title="项目B", text="项目B 详情"),
                ResumeSection(type="internship", title="实习C", text="实习C 详情"),
                ResumeSection(type="skills", title="技能", text="Python"),
            ],
        )
        job = _job(Track.CAMPUS)
        cls.plan_resolved = planner.resolve_lazy_questions(
            planner.plan(job, cls.cand), job, cls.cand, intro_text="自我介绍",
        )

    @classmethod
    def tearDownClass(cls):
        import src.embeddings as emb_mod
        import src.llm as llm_mod
        llm_mod.complete = cls._orig_complete
        emb_mod.embed = cls._orig_embed

    def _project_traces(self):
        return [
            qt for qt in self.plan_resolved.trace.questions
            if qt.category == QuestionCategory.PROJECT_EXPERIENCE
        ]

    def test_round_robin_over_deepdive_sections(self):
        titles = [qt.section_title for qt in self._project_traces()]
        n = len(titles)
        self.assertGreaterEqual(n, 2)
        expected = [["项目A", "项目B", "实习C"][i % 3] for i in range(n)]
        self.assertEqual(titles, expected, "应按 deep-dive 段轮询分配")

    def test_path_is_resume_section(self):
        for qt in self._project_traces():
            self.assertEqual(qt.path, "resume_section")

    def test_non_deepdive_sections_never_assigned(self):
        for qt in self._project_traces():
            self.assertNotIn(
                qt.section_title, ("个人信息", "技能"),
                "personal_info/skills 段不该被拿来出项目题",
            )

    def test_resolved_texts_non_empty(self):
        for r in self.plan_resolved.rounds:
            for q in r.questions:
                if q.category == QuestionCategory.PROJECT_EXPERIENCE:
                    self.assertTrue(q.text.strip())


class SectionAssignmentStubLLMTests(unittest.TestCase):
    """sections 存在但 LLM 不可用: 走轮换 fallback, section_title 不落 trace
    (fallback 文案与段无关, 标了反而误导 HR)。"""

    @classmethod
    def setUpClass(cls):
        import src.embeddings as emb_mod
        import src.llm as llm_mod
        from src.schemas import ResumeSection
        cls._orig_complete = llm_mod.complete
        cls._orig_embed = emb_mod.embed
        llm_mod.complete = lambda system, user, **kw: "[stub] x"
        emb_mod.embed = lambda text, **kw: [0.0] * 1536
        cand = CandidateProfile(
            candidate_id="cand-sections-stub",
            resume="张三 / 后端",
            sections=[
                ResumeSection(type="project", title="项目A", text="项目A 详情"),
            ],
        )
        job = _job(Track.CAMPUS)
        cls.plan_resolved = planner.resolve_lazy_questions(
            planner.plan(job, cand), job, cand,
        )

    @classmethod
    def tearDownClass(cls):
        import src.embeddings as emb_mod
        import src.llm as llm_mod
        llm_mod.complete = cls._orig_complete
        emb_mod.embed = cls._orig_embed

    def test_fallback_path_without_section_title(self):
        traces = [
            qt for qt in self.plan_resolved.trace.questions
            if qt.category == QuestionCategory.PROJECT_EXPERIENCE
        ]
        self.assertTrue(traces)
        for qt in traces:
            self.assertEqual(qt.path, "fallback_template")
            self.assertIsNone(qt.section_title)


class LlmDirectSourceTests(unittest.TestCase):
    """Sprint H: question_source=llm_direct —— 纯 LLM 出题, 与 RAG 路径隔离。
    monkeypatch llm.complete 出真串 + skill_extraction 出固定技能, 验证:
    - knowledge/scenario 全走 llm_direct_* 路径 (不召回题库, source_id 恒 None)
    - 简历技能进 prompt (确定出题方向)
    - rag 模式完全不受影响 (仍走原路径)
    - 项目题两模式共用 (llm_direct 下仍是 lazy 占位, 不受影响)"""

    @classmethod
    def setUpClass(cls):
        import src.agents.planner.skill_extraction as se
        import src.llm as llm_mod
        cls._orig_complete = llm_mod.complete
        cls._orig_extract = se.extract_skills
        cls._seen_prompts = []

        def _fake_complete(system, user, **kw):
            cls._seen_prompts.append((system, user))
            if "基础知识" in system:
                return "针对 Redis 持久化取舍你会怎么选型?"
            if "场景题" in system:
                return "线上接口 P99 突增, 你怎么排查?"
            return "[stub] x"

        llm_mod.complete = _fake_complete
        se.extract_skills = lambda resume: ["Redis", "FastAPI", "Milvus"]

        job = JobContext(
            title="AI后端", jd="RAG 系统", role_family="backend",
            track=Track.CAMPUS, question_source="llm_direct",
        )
        cls.plan = planner.plan(job, _CANDIDATE)

    @classmethod
    def tearDownClass(cls):
        import src.agents.planner.skill_extraction as se
        import src.llm as llm_mod
        llm_mod.complete = cls._orig_complete
        se.extract_skills = cls._orig_extract

    def _traces(self, *cats):
        return [qt for qt in self.plan.trace.questions if qt.category in cats]

    def test_knowledge_all_llm_direct(self):
        ks = self._traces(QuestionCategory.KNOWLEDGE)
        self.assertTrue(ks)
        for qt in ks:
            self.assertEqual(qt.path, "llm_direct_knowledge")
            self.assertIsNone(qt.source_question_id, "纯 LLM 出题无题库溯源")

    def test_scenario_all_llm_direct(self):
        ss = self._traces(QuestionCategory.SCENARIO)
        self.assertTrue(ss)
        for qt in ss:
            self.assertEqual(qt.path, "llm_direct_scenario")

    def test_resume_skills_in_prompt(self):
        # 至少一个 knowledge prompt 含简历技能
        k_prompts = [u for s, u in self._seen_prompts if "基础知识" in s]
        self.assertTrue(k_prompts)
        self.assertTrue(
            any("Redis" in u and "简历技能" in u for u in k_prompts),
            "简历技能应进 knowledge prompt 定向出题",
        )

    def test_extracted_skills_in_trace(self):
        self.assertEqual(self.plan.trace.extracted_skills, ["Redis", "FastAPI", "Milvus"])

    def test_project_still_lazy(self):
        proj = [
            q for r in self.plan.rounds for q in r.questions
            if q.category == QuestionCategory.PROJECT_EXPERIENCE
        ]
        self.assertTrue(proj)
        self.assertTrue(all(q.lazy and not q.text for q in proj),
                        "项目题两模式共用: llm_direct 下仍是 lazy 占位")

    def test_rag_mode_unaffected(self):
        # 同 candidate 换 rag 模式, 不应出现 llm_direct_* 路径
        job = JobContext(title="t", jd="x", role_family="backend",
                         track=Track.CAMPUS, question_source="rag")
        p = planner.plan(job, _CANDIDATE)
        paths = {qt.path for qt in p.trace.questions}
        self.assertNotIn("llm_direct_knowledge", paths)
        self.assertNotIn("llm_direct_scenario", paths)


class LlmDirectPromptGuardrailTests(unittest.TestCase):
    """好题 rubric prompt 的不变式护栏 (改 prompt = 改出题行为, 必须连测试一起改)。"""

    def test_rubric_shared_by_both(self):
        self.assertIn(planner._GOOD_QUESTION_RUBRIC, planner._KNOWLEDGE_LLM_SYSTEM)
        self.assertIn(planner._GOOD_QUESTION_RUBRIC, planner._SCENARIO_LLM_SYSTEM)

    def test_rubric_core_criteria(self):
        r = planner._GOOD_QUESTION_RUBRIC
        self.assertIn("可深挖", r)
        self.assertIn("单一核心考点", r)
        self.assertIn("考理解而非记忆", r)
        self.assertIn("自包含", r)

    def test_prompts_have_fewshot(self):
        # 正/反示范都在 (few-shot 定调)
        self.assertIn("示范", planner._KNOWLEDGE_LLM_SYSTEM)
        self.assertIn("示范", planner._SCENARIO_LLM_SYSTEM)


class LlmDirectTrackToneTests(unittest.TestCase):
    """Sprint H: llm_direct 出题按 track 定制基调 —— 校招重概念/基础/理解,
    社招重实战/深度/权衡。捕获出题 system prompt, 验证基调按 track 正确注入,
    且互斥 (校招 job 不含社招基调, 反之亦然)。"""

    @classmethod
    def setUpClass(cls):
        import src.agents.planner.skill_extraction as se
        import src.llm as llm_mod
        cls._orig_complete = llm_mod.complete
        cls._orig_extract = se.extract_skills
        cls._systems: dict[str, list[str]] = {"campus": [], "lateral": []}

        se.extract_skills = lambda resume: ["Redis"]

        def _run(track: Track, key: str):
            captured: list[str] = []
            llm_mod.complete = lambda system, user, **kw: (
                captured.append(system) or "针对 X 的取舍你会怎么选?"
            )
            job = JobContext(title="后端", jd="x", role_family="backend",
                             track=track, question_source="llm_direct")
            planner.plan(job, _CANDIDATE)
            # 只保留出题 system (含好题标准的), 排除 topic-match 等其他 LLM 调用
            cls._systems[key] = [s for s in captured if "好的面试题标准" in s]

        _run(Track.CAMPUS, "campus")
        _run(Track.LATERAL, "lateral")

    @classmethod
    def tearDownClass(cls):
        import src.agents.planner.skill_extraction as se
        import src.llm as llm_mod
        llm_mod.complete = cls._orig_complete
        se.extract_skills = cls._orig_extract

    def test_campus_tone_injected_exclusively(self):
        sys_list = self._systems["campus"]
        self.assertTrue(sys_list, "campus 应有出题 system")
        for s in sys_list:
            self.assertIn("【校招】", s)
            self.assertNotIn("【社招】", s)

    def test_lateral_tone_injected_exclusively(self):
        sys_list = self._systems["lateral"]
        self.assertTrue(sys_list, "lateral 应有出题 system")
        for s in sys_list:
            self.assertIn("【社招】", s)
            self.assertNotIn("【校招】", s)

    def test_campus_tone_emphasizes_concept(self):
        # 用户要求: 校招更注重概念/基础/理解
        t = planner._CAMPUS_TONE
        self.assertIn("校招", t)
        self.assertIn("基础概念", t)
        self.assertIn("理解", t)

    def test_lateral_tone_emphasizes_practice(self):
        t = planner._LATERAL_TONE
        self.assertIn("社招", t)
        self.assertIn("实战", t)
        self.assertIn("权衡", t)

    def test_track_tone_helper(self):
        self.assertEqual(planner._track_tone(Track.CAMPUS), planner._CAMPUS_TONE)
        self.assertEqual(planner._track_tone(Track.LATERAL), planner._LATERAL_TONE)


if __name__ == "__main__":
    unittest.main()
