"""Milvus Lite collection round-trip + filter eval —— Sprint 3-2。

每个 TestCase 起一个临时 .db, 隔离避免互相污染。
不依赖 OpenAI: 用固定方向的合成向量验语义(同方向相似度=0)。
"""
from __future__ import annotations

import os
import tempfile
import unittest


def _orthogonal_vector(seed: int, dim: int = 1536) -> list[float]:
    """构造一个除 seed 位为 1 外全 0 的单位向量, 互相 COSINE 距离=1。
    不是 stub (全零), 能正常入库。"""
    v = [0.0] * dim
    v[seed % dim] = 1.0
    return v


class VectorStoreLazinessTests(unittest.TestCase):
    """未配置 MILVUS_URI 时, import 不连, 调用才抛。"""

    def test_import_without_env_var_ok(self):
        os.environ.pop("MILVUS_LITE_URI", None)
        os.environ.pop("MILVUS_URI", None)
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        # 仅 import 不应该炸
        import src.vector_store  # noqa: F401

    def test_call_raises_when_missing(self):
        os.environ.pop("MILVUS_LITE_URI", None)
        os.environ.pop("MILVUS_URI", None)
        from src.vector_store import MilvusNotConfigured, init_collections
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        with self.assertRaises(MilvusNotConfigured):
            init_collections()


class _MilvusTestCaseBase(unittest.TestCase):
    """复用: setUpClass 起临时 DB + init collections, tearDownClass 清。"""

    @classmethod
    def setUpClass(cls):
        cls._db = tempfile.mktemp(suffix=".db")
        os.environ.pop("MILVUS_LITE_URI", None)
        os.environ.pop("MILVUS_URI", None)  # 防止撞 pymilvus ORM 的 import-time 解析
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
            p = cls._db + ext
            try:
                os.unlink(p)
            except OSError:
                pass


class CollectionLifecycleTests(_MilvusTestCaseBase):
    def test_init_creates_collections(self):
        from src.vector_store import (
            COLLECTION_DOCUMENTS,
            COLLECTION_QUESTIONS,
            get_client,
        )
        c = get_client()
        self.assertTrue(c.has_collection(COLLECTION_QUESTIONS))
        self.assertTrue(c.has_collection(COLLECTION_DOCUMENTS))

    def test_init_is_idempotent(self):
        from src.vector_store import init_collections
        # 第二次调用不应该报错
        init_collections()
        init_collections()


class QuestionRoundTripTests(_MilvusTestCaseBase):
    def test_upsert_and_search(self):
        from src.vector_store import search_questions, upsert_question
        v = _orthogonal_vector(0)
        ok = upsert_question(
            question_id="q1",
            role_family="backend",
            competency="技术深度",
            text="讲一次你做过最有难度的性能优化",
            embedding=v,
        )
        self.assertTrue(ok)

        hits = search_questions(embedding=v, top_k=5)
        self.assertGreaterEqual(len(hits), 1)
        ids = [h["question_id"] for h in hits]
        self.assertIn("q1", ids)
        match = next(h for h in hits if h["question_id"] == "q1")
        self.assertEqual(match["role_family"], "backend")
        self.assertEqual(match["competency"], "技术深度")
        self.assertIn("distance", match)

    def test_stub_vector_skipped(self):
        from src.embeddings import EMBEDDING_DIM
        from src.vector_store import search_questions, upsert_question

        ok = upsert_question(
            question_id="q-stub",
            role_family="x",
            competency="y",
            text="stub",
            embedding=[0.0] * EMBEDDING_DIM,
        )
        self.assertFalse(ok, "stub 向量入库应被跳过")
        # 搜索本身也用 stub 向量 -> 直接空列表
        hits = search_questions(embedding=[0.0] * EMBEDDING_DIM, top_k=5)
        self.assertEqual(hits, [])

    def test_filter_by_role_family(self):
        from src.vector_store import search_questions, upsert_question
        upsert_question(
            question_id="q-be", role_family="backend",
            competency="技术深度", text="be q",
            embedding=_orthogonal_vector(1),
        )
        upsert_question(
            question_id="q-fe", role_family="frontend",
            competency="技术深度", text="fe q",
            embedding=_orthogonal_vector(2),
        )
        be = search_questions(
            embedding=_orthogonal_vector(1), top_k=10, role_family="backend",
        )
        self.assertGreaterEqual(len(be), 1)
        self.assertTrue(
            all(h["role_family"] == "backend" for h in be),
            f"filter 失效, 返回了非 backend 项: {be}",
        )

    def test_filter_by_competency(self):
        from src.vector_store import search_questions, upsert_question
        upsert_question(
            question_id="q-tech", role_family="backend",
            competency="技术深度", text="t",
            embedding=_orthogonal_vector(3),
        )
        upsert_question(
            question_id="q-comm", role_family="backend",
            competency="沟通协作", text="c",
            embedding=_orthogonal_vector(4),
        )
        tech = search_questions(
            embedding=_orthogonal_vector(3), top_k=10, competency="技术深度",
        )
        self.assertTrue(
            all(h["competency"] == "技术深度" for h in tech),
            f"filter 失效: {tech}",
        )


class DocumentRoundTripTests(_MilvusTestCaseBase):
    def test_upsert_and_search_by_kind(self):
        from src.vector_store import (
            DOC_KIND_RESUME,
            search_documents,
            upsert_document,
        )
        v = _orthogonal_vector(10)
        upsert_document(
            document_id="cand-1:0",
            kind=DOC_KIND_RESUME,
            source_id="cand-1",
            chunk_index=0,
            text="张三 / 后端 / 4 年, 订单 P99 优化",
            embedding=v,
        )
        hits = search_documents(embedding=v, top_k=3, kind=DOC_KIND_RESUME)
        self.assertGreaterEqual(len(hits), 1)
        ids = [h["document_id"] for h in hits]
        self.assertIn("cand-1:0", ids)
        # 用错 kind 过滤应该过滤掉
        misses = search_documents(embedding=v, top_k=3, kind="jd")
        self.assertEqual(misses, [], "JD kind 不应当返回 resume 项")

    def test_filter_by_source_id(self):
        from src.vector_store import (
            DOC_KIND_RESUME,
            search_documents,
            upsert_document,
        )
        upsert_document(
            document_id="a:0", kind=DOC_KIND_RESUME, source_id="a", chunk_index=0,
            text="x", embedding=_orthogonal_vector(20),
        )
        upsert_document(
            document_id="b:0", kind=DOC_KIND_RESUME, source_id="b", chunk_index=0,
            text="y", embedding=_orthogonal_vector(21),
        )
        only_a = search_documents(
            embedding=_orthogonal_vector(20), top_k=10, source_id="a",
        )
        self.assertTrue(
            all(h["source_id"] == "a" for h in only_a),
            f"source_id filter 失效: {only_a}",
        )


if __name__ == "__main__":
    unittest.main()
