"""Planner knowledge 题召回 eval —— Sprint 3-5。

验证三件事:
1. 题库 + 真 embed 可用时, knowledge 题 source_question_id 指向召回的种子题
2. 题库召回但 LLM stub 时, 题目文本就是种子题原文 + source 仍记录
3. 没题库 / 嵌入 stub 时, source_question_id=None, 题目走 fallback 路径

不依赖真 OpenAI API: monkey-patch embeddings.embed 让"相同含义"的文本映射到
相近向量 (这里简化为返回同一个向量), 让 Milvus search 必中。
"""
from __future__ import annotations

import os
import tempfile
import unittest

# Sprint B+D 起 plan() 依赖 PG (topic_match 读 list_question_topics +
# record_skill_backlog): 必须切 test DB, 否则 dev 库的真实 topic 会让
# fixed-vector patch 下"所有 query 匹配所有 topic", 种子题被 topic 硬过滤挡掉
from evals._test_db import swap_to_test_url

swap_to_test_url()


def _fixed_vector(dim: int = 1536) -> list[float]:
    """返回固定向量, 让所有 embed 调用 cosine 距离=0, search 必中。"""
    v = [0.0] * dim
    v[0] = 1.0
    return v


class _PlannerRagBase(unittest.TestCase):
    """临时 Milvus DB + collection 初始化, 题库 seed 在每个 test 自己控制。"""

    @classmethod
    def setUpClass(cls):
        cls._db = tempfile.mktemp(suffix=".db")
        os.environ.pop("MILVUS_URI", None)
        os.environ["MILVUS_LITE_URI"] = cls._db
        from src.vector_store import init_collections
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        init_collections()

    @classmethod
    def tearDownClass(cls):
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        for ext in ("", "-shm", "-wal", ".lock"):
            try:
                os.unlink(cls._db + ext)
            except OSError:
                pass

    def setUp(self):
        # pymilvus.settings.load_dotenv() 已经把 OPENAI 加回环境, 在这里 pop;
        # 实际 embed 由具体 test 通过 monkey-patch 控制
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        # 清掉所有 questions 防上次残留
        from src.vector_store import drop_collections, init_collections
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        drop_collections()
        init_collections()

    def _patch_embed_to_fixed(self):
        """monkey-patch embed 返固定向量, 让 Milvus search 必命中。返回 restore。"""
        from src.agents import planner as planner_mod
        from src.ingestion import pipeline as ing_pipeline
        original_planner_emb = planner_mod.embeddings.embed
        original_pipeline_emb = ing_pipeline.embeddings.embed

        def _fake(text, **_kw):
            return _fixed_vector()

        planner_mod.embeddings.embed = _fake
        ing_pipeline.embeddings.embed = _fake

        def restore():
            planner_mod.embeddings.embed = original_planner_emb
            ing_pipeline.embeddings.embed = original_pipeline_emb
        return restore

    def _seed_known_questions(self, role_family: str, comp_to_qid: dict[str, str]):
        """直接往 Milvus 写已知 question_id 的题目, 跳过 PG, 跳过真 embed。"""
        from src.vector_store import upsert_question
        for competency, qid in comp_to_qid.items():
            upsert_question(
                question_id=qid,
                role_family=role_family,
                competency=competency,
                text=f"种子题 - {qid} - {competency}",
                embedding=_fixed_vector(),
            )


class RagHitTests(_PlannerRagBase):
    """题库有题 + embed 可用时, knowledge 题应当带 source_question_id。"""

    def test_knowledge_questions_carry_source_id_from_seed(self):
        from src.agents.planner import plan
        from src.schemas import (
            CandidateProfile, JobContext, QuestionCategory,
        )

        # 1) 灌入两道已知 id 的题: 各自维度一道
        self._seed_known_questions(
            role_family="backend",
            comp_to_qid={
                "技术深度": "seed-tech-001",
                "沟通协作": "seed-comm-001",
            },
        )

        # 2) monkey-patch embed 让 search 必中
        restore = self._patch_embed_to_fixed()
        try:
            job = JobContext(title="后端工程师", jd="负责交易系统", role_family="backend")
            cand = CandidateProfile(resume="张三 / 后端", projects=["P99 优化"])
            p = plan(job, cand)
        finally:
            restore()

        # Sprint 5.5: lateral track 1 knowledge (tech), 3 project (lazy 占位)
        questions = [q for r in p.rounds for q in r.questions]
        knowledge_q = [q for q in questions if q.category == QuestionCategory.KNOWLEDGE]
        self.assertEqual(len(knowledge_q), 4, "Sprint 5.9: tech-lateral 4 道 knowledge")
        # tech 维度的种子题应当被命中
        self.assertEqual(knowledge_q[0].source_question_id, "seed-tech-001")

        # project 题在 plan 阶段是 lazy 占位, source_question_id 不取自 knowledge 题库
        project_q = [
            q for q in questions if q.category == QuestionCategory.PROJECT_EXPERIENCE
        ]
        self.assertEqual(len(project_q), 11, "Sprint 5.9: tech-lateral 11 道 project")
        self.assertTrue(all(q.source_question_id is None for q in project_q))
        self.assertTrue(all(q.lazy for q in project_q), "project 题在 plan 阶段都是 lazy")

    def test_llm_stub_falls_back_to_seed_text_but_keeps_source(self):
        """题库有题 + embed 可用 + LLM stub 时, 命中的题应当用种子题原文且仍记
        source; 同一道 seed 在整个 plan 里**至多用一次** (used_source_ids 排除,
        取代 rank 轮转), seed 耗尽后的题走 fallback (source=None), 不复读种子。"""
        from src.agents.planner import plan
        from src.schemas import (
            CandidateProfile, JobContext, QuestionCategory,
        )

        self._seed_known_questions(
            role_family="backend",
            comp_to_qid={"技术深度": "seed-T", "沟通协作": "seed-C"},
        )
        # LLM stub: 模块顶 pop 已生效, 但 ingestion 也用 embed, 一并 patch
        restore = self._patch_embed_to_fixed()
        try:
            job = JobContext(title="t", jd="x", role_family="backend")
            cand = CandidateProfile(resume="r", projects=[])
            p = plan(job, cand)
        finally:
            restore()

        knowledge_q = [
            q for r in p.rounds for q in r.questions
            if q.category == QuestionCategory.KNOWLEDGE
        ]
        self.assertTrue(len(knowledge_q) >= 1)

        # 命中 seed 的题: 原文 + source; 每道 seed 全 plan 至多出现一次
        sourced = [q for q in knowledge_q if q.source_question_id is not None]
        self.assertTrue(len(sourced) >= 1, "至少第一道题应当命中题库 seed")
        for q in sourced:
            self.assertTrue(q.source_question_id in {"seed-T", "seed-C"})
            self.assertIn("种子题", q.text, "LLM stub 时应当用种子原文")
        source_ids = [q.source_question_id for q in sourced]
        self.assertEqual(
            len(source_ids), len(set(source_ids)),
            f"同一道 seed 不允许复用: {source_ids}",
        )

        # seed 耗尽后的题: fallback 模板 (LLM 也 stub), 不该再是种子原文
        unsourced = [q for q in knowledge_q if q.source_question_id is None]
        for q in unsourced:
            self.assertNotIn(
                "种子题", q.text,
                "seed 耗尽后应走 fallback 模板, 不能复读种子原文",
            )


class ProjectRagHitTests(_PlannerRagBase):
    """Resume 已 ingest 时, project 题应当带 source_chunk_ids。"""

    def test_project_questions_carry_chunk_ids_when_resume_ingested(self):
        from src.agents.planner import plan, resolve_lazy_questions
        from src.ingestion import ingest_resume
        from src.schemas import (
            CandidateProfile, JobContext, QuestionCategory,
        )

        # 1) monkey-patch embed 让 ingest + RAG 都用同一固定向量
        restore = self._patch_embed_to_fixed()
        try:
            cand_id = "cand-proj-rag"
            ingest_resume(
                cand_id,
                "张三 / 后端 / 4 年。" * 30  # 长足以切多片
                + "对账中台日处理 2 亿笔。" * 30
                + "P99 优化从 800ms 降到 350ms。" * 30,
            )

            job = JobContext(title="后端工程师", jd="负责交易系统", role_family="backend")
            cand = CandidateProfile(
                candidate_id=cand_id,
                resume="dummy resume",  # planner 内部读切片不读这个
                projects=[],
            )
            # Sprint 5.5: project 题 plan 阶段只占位, resolve_lazy 时才走 Resume RAG
            p = resolve_lazy_questions(plan(job, cand), job, cand)
        finally:
            restore()

        # 2) project 题应当都有 source_chunk_ids (lateral 3 道)
        project_q = [
            q for r in p.rounds for q in r.questions
            if q.category == QuestionCategory.PROJECT_EXPERIENCE
        ]
        self.assertEqual(len(project_q), 11, "Sprint 5.9: tech-lateral 11 道 project")
        for q in project_q:
            self.assertTrue(q.lazy, "lazy 静态信号生成后仍 True 作审计")
            self.assertGreater(
                len(q.source_chunk_ids), 0,
                f"project 题应当带 source_chunk_ids; 实际题目: {q.text}",
            )
            for cid in q.source_chunk_ids:
                self.assertTrue(
                    cid.startswith(f"{cand_id}:resume:"),
                    f"chunk_id 格式不对: {cid}",
                )

        # 3) knowledge 题不应该有 source_chunk_ids (那是 project 路径的溯源)
        knowledge_q = [
            q for r in p.rounds for q in r.questions
            if q.category == QuestionCategory.KNOWLEDGE
        ]
        for q in knowledge_q:
            self.assertEqual(
                q.source_chunk_ids, [],
                "knowledge 题不应携带 chunk 溯源",
            )


class ProjectRagMissTests(_PlannerRagBase):
    """没 ingest resume 时, project 题 source_chunk_ids 应当为空。"""

    def test_no_resume_ingest_chunks_empty(self):
        from src.agents.planner import plan
        from src.schemas import (
            CandidateProfile, JobContext, QuestionCategory,
        )

        # 不 ingest_resume; Milvus documents 是空
        restore = self._patch_embed_to_fixed()
        try:
            job = JobContext(title="t", jd="x", role_family="backend")
            cand = CandidateProfile(resume="r", projects=[])
            p = plan(job, cand)
        finally:
            restore()

        project_q = [
            q for q in p.rounds[0].questions
            if q.category == QuestionCategory.PROJECT_EXPERIENCE
        ]
        for q in project_q:
            self.assertEqual(
                q.source_chunk_ids, [],
                "无 resume 切片时不应有 source_chunk_ids",
            )

    def test_other_candidates_chunks_not_used(self):
        """source_id 过滤: A 的 chunks 不能被 B 的 plan 召回。"""
        from src.agents.planner import plan
        from src.ingestion import ingest_resume
        from src.schemas import (
            CandidateProfile, JobContext, QuestionCategory,
        )

        restore = self._patch_embed_to_fixed()
        try:
            # 给 A 灌 chunks
            ingest_resume("cand-A", "A 的简历内容。" * 30)
            # plan B
            job = JobContext(title="t", jd="x", role_family="backend")
            cand_b = CandidateProfile(
                candidate_id="cand-B", resume="B 的简历", projects=[],
            )
            p = plan(job, cand_b)
        finally:
            restore()

        project_q = [
            q for q in p.rounds[0].questions
            if q.category == QuestionCategory.PROJECT_EXPERIENCE
        ]
        for q in project_q:
            # 即使 RAG 走通了, source 也应只有 cand-B 的 (空, 因为 B 没 ingest)
            for cid in q.source_chunk_ids:
                self.assertFalse(
                    cid.startswith("cand-A:"),
                    f"不应召回到其他候选人的 chunks: {cid}",
                )


class RagMissTests(_PlannerRagBase):
    """题库为空 / 嵌入 stub 时, source 应当 None。"""

    def test_no_seed_in_milvus_source_is_none(self):
        """Milvus 空 + embed 可用 + LLM stub: 走 fallback 模板, source=None。"""
        from src.agents.planner import plan
        from src.schemas import CandidateProfile, JobContext

        # 不灌任何题
        restore = self._patch_embed_to_fixed()
        try:
            job = JobContext(title="t", jd="x", role_family="backend")
            cand = CandidateProfile(resume="r", projects=[])
            p = plan(job, cand)
        finally:
            restore()
        # 所有 knowledge 题应当 source=None
        from src.schemas import QuestionCategory
        knowledge_q = [
            q for q in p.rounds[0].questions
            if q.category == QuestionCategory.KNOWLEDGE
        ]
        self.assertTrue(all(q.source_question_id is None for q in knowledge_q))

    def test_role_family_mismatch_source_is_none(self):
        """Milvus 有题但 role_family 不匹配, source 应当 None。"""
        from src.agents.planner import plan
        from src.schemas import CandidateProfile, JobContext, QuestionCategory

        self._seed_known_questions(
            role_family="frontend",  # 注意: 灌 frontend 的题
            comp_to_qid={"技术深度": "seed-fe"},
        )
        restore = self._patch_embed_to_fixed()
        try:
            job = JobContext(title="t", jd="x", role_family="backend")  # 但 plan backend
            cand = CandidateProfile(resume="r", projects=[])
            p = plan(job, cand)
        finally:
            restore()
        knowledge_q = [
            q for q in p.rounds[0].questions
            if q.category == QuestionCategory.KNOWLEDGE
        ]
        self.assertTrue(
            all(q.source_question_id is None for q in knowledge_q),
            f"frontend 题不应当被 backend plan 召回到, 实际 source ids: "
            f"{[q.source_question_id for q in knowledge_q]}",
        )

    def test_stub_embed_skips_rag(self):
        """embed 返 stub (无 key 或被 pop) 时, 不调 Milvus, source=None。
        监控 Milvus 是否被调用: 通过事先检查 collection 内容 (灌一条题然后看是否被召回)。"""
        from src.agents.planner import plan
        from src.schemas import (
            CandidateProfile, JobContext, QuestionCategory,
        )

        self._seed_known_questions(
            role_family="backend",
            comp_to_qid={"技术深度": "seed-stub"},
        )
        # 不 patch embed: 走默认, 因为没有 OPENAI_API_KEY 会出 stub
        job = JobContext(title="t", jd="x", role_family="backend")
        cand = CandidateProfile(resume="r", projects=[])
        p = plan(job, cand)

        knowledge_q = [
            q for q in p.rounds[0].questions
            if q.category == QuestionCategory.KNOWLEDGE
        ]
        # embed stub -> 不查 Milvus -> 即使有种子也不召回
        self.assertTrue(
            all(q.source_question_id is None for q in knowledge_q),
            "embed stub 时不应调 Milvus 召回",
        )


class BigPoolDistanceFilterTests(unittest.TestCase):
    """Sprint E: _retrieve_seed_question 的 max_distance 硬过滤 (大池才用)。
    直接 mock search_questions + embed, 不依赖真 Milvus。"""

    def setUp(self):
        os.environ.pop("OPENAI_API_KEY", None)

    def _call(self, hits, max_distance):
        from unittest.mock import patch
        from src.agents.planner import _retrieve_seed_question
        from src.schemas import Competency, QuestionCategory
        comp = Competency(competency_id="comp:tech", name="技术深度", description="d")
        with patch(
            "src.agents.planner.embeddings.embed", return_value=_fixed_vector(),
        ), patch(
            "src.agents.planner.embeddings.is_stub_vector", return_value=False,
        ), patch(
            "src.agents.planner.vector_store.search_questions", return_value=hits,
        ):
            return _retrieve_seed_question(
                "backend", comp, "jd",
                category=QuestionCategory.KNOWLEDGE,
                max_distance=max_distance,
            )

    def test_nearest_within_threshold_returned(self):
        hits = [{"question_id": "a", "text": "近", "distance": 0.45}]
        got = self._call(hits, max_distance=0.49)
        self.assertEqual(got["question_id"], "a")

    def test_nearest_over_threshold_returns_none(self):
        # 最近题就超阈值 -> 退纯 LLM (返 None), 不看后面更远的
        hits = [
            {"question_id": "a", "text": "远", "distance": 0.53},
            {"question_id": "b", "text": "更远", "distance": 0.60},
        ]
        self.assertIsNone(self._call(hits, max_distance=0.49))

    def test_none_threshold_disables_filter(self):
        # max_distance=None -> 旧行为, 距离多远都返回最近的
        hits = [{"question_id": "a", "text": "远", "distance": 0.90}]
        got = self._call(hits, max_distance=None)
        self.assertEqual(got["question_id"], "a")

    def test_excluded_then_threshold(self):
        # 最近的被排除, 下一个非排除题超阈值 -> None
        from unittest.mock import patch
        from src.agents.planner import _retrieve_seed_question
        from src.schemas import Competency, QuestionCategory
        comp = Competency(competency_id="comp:tech", name="技术深度", description="d")
        hits = [
            {"question_id": "used", "text": "近但已用", "distance": 0.40},
            {"question_id": "far", "text": "远", "distance": 0.55},
        ]
        with patch(
            "src.agents.planner.embeddings.embed", return_value=_fixed_vector(),
        ), patch(
            "src.agents.planner.embeddings.is_stub_vector", return_value=False,
        ), patch(
            "src.agents.planner.vector_store.search_questions", return_value=hits,
        ):
            got = _retrieve_seed_question(
                "backend", comp, "jd",
                category=QuestionCategory.KNOWLEDGE,
                exclude_ids={"used"}, max_distance=0.49,
            )
        self.assertIsNone(got)

    def test_bigpool_config_parsing(self):
        from src.agents.planner import _bigpool_max_distance
        import src.agents.planner as P
        orig = os.environ.get("KNOWLEDGE_BIGPOOL_MAX_DISTANCE")
        try:
            os.environ["KNOWLEDGE_BIGPOOL_MAX_DISTANCE"] = "0.6"
            self.assertEqual(_bigpool_max_distance(), 0.6)
            os.environ["KNOWLEDGE_BIGPOOL_MAX_DISTANCE"] = ""     # 显式关闭
            self.assertIsNone(_bigpool_max_distance())
            os.environ["KNOWLEDGE_BIGPOOL_MAX_DISTANCE"] = "abc"  # 非法 -> 默认
            self.assertEqual(_bigpool_max_distance(), P._DEFAULT_BIGPOOL_MAX_DISTANCE)
            os.environ.pop("KNOWLEDGE_BIGPOOL_MAX_DISTANCE")      # 未设 -> 默认
            self.assertEqual(_bigpool_max_distance(), P._DEFAULT_BIGPOOL_MAX_DISTANCE)
        finally:
            if orig is None:
                os.environ.pop("KNOWLEDGE_BIGPOOL_MAX_DISTANCE", None)
            else:
                os.environ["KNOWLEDGE_BIGPOOL_MAX_DISTANCE"] = orig


class RefinePromptGuardrailTests(unittest.TestCase):
    """精修 prompt 的不变式护栏。evals 强制 LLM stub, 无法测精修语义质量,
    但能锁住 prompt 模板的关键约束不被后续改动悄悄删掉:
    1. 考点不变 (防"精修换题"失真 bug 复发)
    2. 自包含 (题库题源自文档切片, 常残留 "Agent A"/"该协议" 这类只有读过
       原文才懂的悬空指代; 精修必须消除, 让候选人零上下文能看懂题目)
    3. 禁塞岗位关键词 (防"贴合 JD"把无关技术名词硬塞进题干)
    改这些约束 = 改精修行为, 必须连同本测试一起改并人工复核精修输出。"""

    def test_knowledge_rag_prompt_keeps_core_invariants(self):
        from src.agents.planner import _KNOWLEDGE_RAG_SYSTEM as p
        self.assertIn("知识对象/主题完全不变", p)
        self.assertIn("自包含", p)
        self.assertIn("悬空指代", p)
        self.assertIn("不得把答案写进题干", p)
        self.assertIn("硬塞进题目", p)
        # 主题域消歧: prompt 端与 _knowledge_question 的 topic_line 配套
        self.assertIn("题目主题域", p)

    def test_scenario_rag_prompt_keeps_core_invariants(self):
        from src.agents.planner import _SCENARIO_RAG_SYSTEM as p
        self.assertIn("核心问题不变", p)
        self.assertIn("硬塞进情境", p)
        # 与 knowledge 同步 (用户要求): 自包含 + 主题域消歧
        self.assertIn("自包含", p)
        self.assertIn("悬空指代", p)
        self.assertIn("题目主题域", p)
        self.assertIn("不得把期望的处理方案写进题干", p)

    def test_project_section_prompt_keeps_core_invariants(self):
        # Sprint F: 单段定向深挖 —— 必须点名经历 (题干自包含) + 不跨段串题
        from src.agents.planner import _PROJECT_SECTION_SYSTEM as p
        self.assertIn("点名该经历", p)
        self.assertIn("这段经历之外", p)


if __name__ == "__main__":
    unittest.main()
