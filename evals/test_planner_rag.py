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

        # 3) knowledge 题 (前两道) 应当都有 source_question_id 指向种子
        questions = p.rounds[0].questions
        knowledge_q = [q for q in questions if q.category == QuestionCategory.KNOWLEDGE]
        self.assertEqual(len(knowledge_q), 2)
        srcs = {q.source_question_id for q in knowledge_q}
        self.assertEqual(srcs, {"seed-tech-001", "seed-comm-001"})

        # 4) project 题 (后两道) source 应当是 None (Sprint 3-5 还没接 Resume RAG)
        project_q = [
            q for q in questions if q.category == QuestionCategory.PROJECT_EXPERIENCE
        ]
        self.assertEqual(len(project_q), 2)
        self.assertTrue(all(q.source_question_id is None for q in project_q))

    def test_llm_stub_falls_back_to_seed_text_but_keeps_source(self):
        """题库有题 + embed 可用 + LLM stub 时, 应当用种子题原文且仍记 source。"""
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
            q for q in p.rounds[0].questions
            if q.category == QuestionCategory.KNOWLEDGE
        ]
        for q in knowledge_q:
            self.assertTrue(q.source_question_id in {"seed-T", "seed-C"})
            self.assertIn("种子题", q.text, "LLM stub 时应当用种子原文")


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


if __name__ == "__main__":
    unittest.main()
