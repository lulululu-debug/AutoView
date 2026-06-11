"""资料 ingestion eval —— Sprint 3-4。

两层:
1. chunker 行为 (无外部依赖)
2. pipeline (用 monkey-patch 的 embed 验证 PG/Milvus 写入 + filter 召回)

跑前提: setUp 起临时 Milvus DB; PG 不强制但 ingest 不依赖 PG (Milvus 是
检索副本, 原文在 jobs/candidates 表上, ingestion 自己不写 PG)。
"""
from __future__ import annotations

import os
import tempfile
import unittest


def _synthetic_vector(seed: int, dim: int = 1536) -> list[float]:
    """除 seed 位为 1 全 0; 非 stub, 可入 Milvus, 互相 COSINE 距离 = 1。"""
    v = [0.0] * dim
    v[seed % dim] = 1.0
    return v


class ChunkerBehaviorTests(unittest.TestCase):
    """chunker 边界 + 中文分句 + overlap。"""

    def test_empty_returns_empty(self):
        from src.ingestion import chunk_text
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   \n  "), [])

    def test_short_text_returns_single_chunk(self):
        from src.ingestion import chunk_text
        s = "短短一句话, 不会被切。"
        self.assertEqual(chunk_text(s, max_chars=500), [s])

    def test_long_text_split_at_chinese_sentence_end(self):
        from src.ingestion import chunk_text
        # 4 个中文短句各 ~25 字
        s = (
            "讲讲你在交易系统里做过的性能优化工作, 包括前因后果。" * 2
            + "你怎么和 SRE 协作定义 SLO? 请举一个具体例子。" * 2
        )
        chunks = chunk_text(s, max_chars=60, overlap=10)
        # 应该被切成多片
        self.assertGreater(len(chunks), 1)
        # 大部分片应该以句末符号结束 (允许最后一片不以句号收尾)
        non_last = chunks[:-1]
        with_period = sum(1 for c in non_last if c and c[-1] in "。！？.!?")
        self.assertGreaterEqual(
            with_period, len(non_last) // 2,
            "至少一半的中间片应当在句末断开",
        )

    def test_chunks_have_overlap(self):
        """相邻片应当有 overlap 字符的重叠。"""
        from src.ingestion import chunk_text
        # 无句末符号, 强制走滑动窗口分支
        s = "x" * 200
        chunks = chunk_text(s, max_chars=50, overlap=10)
        self.assertGreater(len(chunks), 1)
        for prev, cur in zip(chunks, chunks[1:]):
            # 相邻两片应有重叠 (近似检查: cur 开头出现在 prev 末尾)
            overlap_region = prev[-10:]
            self.assertEqual(
                overlap_region, cur[:10],
                f"相邻片应有 overlap: prev 末 10={prev[-10:]!r} cur 头 10={cur[:10]!r}",
            )

    def test_max_chars_respected(self):
        from src.ingestion import chunk_text
        s = "句子。" * 200
        chunks = chunk_text(s, max_chars=80, overlap=10)
        for c in chunks:
            self.assertLessEqual(len(c), 80, f"切片超长: {len(c)}")

    def test_invalid_args_raise(self):
        from src.ingestion import chunk_text
        with self.assertRaises(ValueError):
            chunk_text("x", max_chars=10, overlap=10)  # overlap == max_chars
        with self.assertRaises(ValueError):
            chunk_text("x", max_chars=10, overlap=15)  # overlap > max_chars
        with self.assertRaises(ValueError):
            chunk_text("x", max_chars=0, overlap=0)


class _PipelineTestBase(unittest.TestCase):
    """共用 Milvus 临时 DB 初始化."""

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
        # 防止 pymilvus 已 load_dotenv() 让 OPENAI_API_KEY 漏进来
        os.environ.pop("OPENAI_API_KEY", None)


class IngestStubFallbackTests(_PipelineTestBase):
    """embed stub 时: ingest 返 0, Milvus 不写。"""

    def test_ingest_resume_stub_embed_no_milvus(self):
        from src.ingestion import ingest_resume
        n = ingest_resume("cand-stub", "张三 / 后端 / 4 年。订单 P99 优化。")
        self.assertEqual(n, 0, "embed stub 时 Milvus 应当 0 (vector_store 已防护)")

    def test_ingest_empty_text_returns_zero(self):
        from src.ingestion import ingest_jd
        self.assertEqual(ingest_jd("j", ""), 0)
        self.assertEqual(ingest_jd("j", "   "), 0)


class IngestWriteTests(_PipelineTestBase):
    """monkey-patch embed 返合成向量: PG/Milvus 双写 + filter 召回都过。"""

    def _patch_embed(self):
        """换 embeddings.embed 为返回独立合成向量的版本; 返回 restore 闭包。"""
        from src import ingestion
        from src.ingestion import pipeline
        counter = {"n": 0}

        def _fake_embed(text, **_kw):
            counter["n"] += 1
            return _synthetic_vector(counter["n"])

        original = pipeline.embeddings.embed
        pipeline.embeddings.embed = _fake_embed

        def restore():
            pipeline.embeddings.embed = original
        return restore

    def test_ingest_resume_writes_to_milvus(self):
        from src.ingestion import ingest_resume
        from src.vector_store import (
            DOC_KIND_RESUME,
            search_documents,
        )
        restore = self._patch_embed()
        try:
            n = ingest_resume(
                "cand-write",
                "张三 / 后端 / 4 年。" * 50,  # 长文本会被切成多片
            )
        finally:
            restore()
        self.assertGreater(n, 0, "应当至少入库一片")
        # 用同种子向量搜回来
        hits = search_documents(
            embedding=_synthetic_vector(1),
            top_k=10,
            kind=DOC_KIND_RESUME,
            source_id="cand-write",
        )
        self.assertGreater(len(hits), 0)
        self.assertTrue(
            all(h["kind"] == "resume" for h in hits),
            f"kind filter 失效: {hits}",
        )
        self.assertTrue(
            all(h["source_id"] == "cand-write" for h in hits),
            f"source_id filter 失效: {hits}",
        )

    def test_ingest_jd_and_company_material_dont_collide(self):
        """同一 job 的 JD 与公司资料 document_id 用不同前缀, 不冲突。"""
        from src.ingestion import ingest_company_material, ingest_jd
        from src.vector_store import (
            DOC_KIND_COMPANY_MATERIAL,
            DOC_KIND_JD,
            search_documents,
        )
        restore = self._patch_embed()
        try:
            n_jd = ingest_jd("job-x", "负责交易系统的稳定性。需要熟悉分布式。" * 20)
            n_cm = ingest_company_material("job-x", "一家以交易系统为核心的公司。" * 20)
        finally:
            restore()
        self.assertGreater(n_jd, 0)
        self.assertGreater(n_cm, 0)
        # 同 source_id, 不同 kind 应当能各自单独召回
        jd_hits = search_documents(
            embedding=_synthetic_vector(1), top_k=10,
            source_id="job-x", kind=DOC_KIND_JD,
        )
        cm_hits = search_documents(
            embedding=_synthetic_vector(1), top_k=10,
            source_id="job-x", kind=DOC_KIND_COMPANY_MATERIAL,
        )
        self.assertGreater(len(jd_hits), 0)
        self.assertGreater(len(cm_hits), 0)
        # document_id 前缀区分: jd 走 "job-x:jd:", cm 走 "job-x:cm:"
        for h in jd_hits:
            self.assertIn(":jd:", h["document_id"])
        for h in cm_hits:
            self.assertIn(":cm:", h["document_id"])

    def test_ingest_idempotent_rerun(self):
        """重跑 ingest 不应在 Milvus 产生重复 (document_id 由 source_id + idx 决定)。"""
        from src.ingestion import ingest_resume
        from src.vector_store import (
            DOC_KIND_RESUME,
            search_documents,
        )
        restore = self._patch_embed()
        try:
            ingest_resume("cand-idem", "短的 resume。")
            ingest_resume("cand-idem", "短的 resume。")  # 同输入
        finally:
            restore()
        hits = search_documents(
            embedding=_synthetic_vector(1), top_k=50,
            kind=DOC_KIND_RESUME, source_id="cand-idem",
        )
        self.assertEqual(len(hits), 1, "重跑同输入不应产生重复切片")


if __name__ == "__main__":
    unittest.main()
