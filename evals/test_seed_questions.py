"""种子题库填充 eval —— Sprint 3-3。

不需要真 ANTHROPIC_API_KEY: 强制 stub 让脚本走 fallback 模板分支。
不需要真 OPENAI_API_KEY: monkey-patch embed 返回合成向量, 验证 Milvus 双写。

需要 POSTGRES_URL: 真的写 PG, 验证读回。
"""
from __future__ import annotations

import os
import tempfile
import unittest

# Sprint 5.9 patch: swap to TEST_POSTGRES_URL 防 TRUNCATE 抹掉 dev DB.
from evals._test_db import swap_to_test_url  # noqa: E402
swap_to_test_url()

# 关键: pymilvus 在 import 时会 load_dotenv() 自动把 .env 加回 os.environ。
# 所以模块顶 pop 太早 —— pymilvus 后续被 import 时又把 key 加回来了。
# 必须在每个 test 的 setUp 里 pop, 或在测试逻辑里防御性地处理。
# 这里只 pop ANTHROPIC_API_KEY (anthropic SDK 不会 load_dotenv), 是双保险;
# OPENAI_API_KEY 在 setUp 里 pop。
os.environ.pop("ANTHROPIC_API_KEY", None)


def _synthetic_vector(seed: int, dim: int = 1536) -> list[float]:
    """除 seed 位为 1 全 0 单位向量。非 stub, 可入 Milvus。"""
    v = [0.0] * dim
    v[seed % dim] = 1.0
    return v


class QuestionIdDeterminismTests(unittest.TestCase):
    def test_same_content_same_id(self):
        from scripts.seed_questions import _question_id
        a = _question_id("backend", "技术深度", "讲一个性能优化的例子")
        b = _question_id("backend", "技术深度", "讲一个性能优化的例子")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16, "16 hex 字符")

    def test_different_text_different_id(self):
        from scripts.seed_questions import _question_id
        self.assertNotEqual(
            _question_id("backend", "技术深度", "题 1"),
            _question_id("backend", "技术深度", "题 2"),
        )

    def test_different_competency_different_id(self):
        from scripts.seed_questions import _question_id
        self.assertNotEqual(
            _question_id("backend", "技术深度", "x"),
            _question_id("backend", "沟通协作", "x"),
        )


class CleanLineTests(unittest.TestCase):
    def test_strip_numbered_prefix(self):
        from scripts.seed_questions import _clean_line
        self.assertEqual(_clean_line("1. 讲一下"), "讲一下")
        self.assertEqual(_clean_line("12) 讲一下"), "讲一下")
        self.assertEqual(_clean_line("- 讲一下"), "讲一下")
        self.assertEqual(_clean_line("* 讲一下"), "讲一下")
        self.assertEqual(_clean_line("   讲一下   "), "讲一下")


@unittest.skipUnless(os.environ.get("POSTGRES_URL"), "需要 POSTGRES_URL")
class SeedPgOnlyTests(unittest.TestCase):
    """LLM stub + embed stub: 仅写 PG, Milvus 应当 0 行 (stub 向量被跳过)。"""

    @classmethod
    def setUpClass(cls):
        cls._db = tempfile.mktemp(suffix=".db")
        os.environ.pop("MILVUS_URI", None)
        os.environ["MILVUS_LITE_URI"] = cls._db
        from src.db import init_db
        from src.vector_store import init_collections
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        init_db()
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
        # 每个测试独立干净
        # 必须 pop 在 setUp: pymilvus 的 import 钩子会 load_dotenv() 把 OPENAI 加回来
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        import psycopg
        url = os.environ["POSTGRES_URL"].replace("+psycopg", "")
        with psycopg.connect(url) as conn:
            conn.execute("TRUNCATE seed_questions")

    def test_seed_runs_fallback_when_llm_stub(self):
        from scripts.seed_questions import (
            PER_COMPETENCY_DEFAULT, seed_backend_questions,
        )
        r = seed_backend_questions(per_competency=5)
        self.assertEqual(r.pg_written, 10, "2 维 × 5 = 10 道")
        self.assertEqual(r.milvus_written, 0, "embed stub 时 Milvus 应当 0 (跳过)")
        # 所有题目都标记 fallback_template
        self.assertTrue(all(q.source == "fallback_template" for q in r.questions))

    def test_seed_persists_to_pg(self):
        from scripts.seed_questions import seed_backend_questions
        from src.db import list_seed_questions
        seed_backend_questions(per_competency=3)
        all_q = list_seed_questions(role_family="backend")
        self.assertEqual(len(all_q), 6, "2 维 × 3 = 6 道")
        # 维度过滤
        tech = list_seed_questions(role_family="backend", competency="技术深度")
        self.assertEqual(len(tech), 3)

    def test_seed_idempotent_rerun(self):
        """同 PER_COMPETENCY 重跑应当不增加题数 (content hash id)。"""
        from scripts.seed_questions import seed_backend_questions
        from src.db import list_seed_questions
        seed_backend_questions(per_competency=5)
        seed_backend_questions(per_competency=5)
        all_q = list_seed_questions(role_family="backend")
        self.assertEqual(len(all_q), 10, "重跑不应当产生重复题")

    def test_dry_run_writes_nothing(self):
        from scripts.seed_questions import seed_backend_questions
        from src.db import list_seed_questions
        r = seed_backend_questions(per_competency=5, dry_run=True)
        self.assertEqual(r.pg_written, 0)
        self.assertEqual(r.milvus_written, 0)
        self.assertEqual(len(r.questions), 10, "题目仍要返回")
        self.assertEqual(len(list_seed_questions(role_family="backend")), 0)


@unittest.skipUnless(os.environ.get("POSTGRES_URL"), "需要 POSTGRES_URL")
class SeedDualWriteTests(unittest.TestCase):
    """monkey-patch embed 返合成向量, 验证 PG + Milvus 双写都生效。"""

    @classmethod
    def setUpClass(cls):
        cls._db = tempfile.mktemp(suffix=".db")
        os.environ.pop("MILVUS_URI", None)
        os.environ["MILVUS_LITE_URI"] = cls._db
        from src.db import init_db
        from src.vector_store import init_collections
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        init_db()
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
        import psycopg
        url = os.environ["POSTGRES_URL"].replace("+psycopg", "")
        with psycopg.connect(url) as conn:
            conn.execute("TRUNCATE seed_questions")

    def test_dual_write_to_pg_and_milvus(self):
        # monkey-patch embed: 每次返回一个独立的合成向量
        import scripts.seed_questions as seed_mod
        counter = {"n": 0}
        original_embed = seed_mod.embeddings.embed

        def _fake_embed(text, **_kw):
            counter["n"] += 1
            return _synthetic_vector(counter["n"])

        seed_mod.embeddings.embed = _fake_embed
        try:
            r = seed_mod.seed_backend_questions(per_competency=4)
        finally:
            seed_mod.embeddings.embed = original_embed

        self.assertEqual(r.pg_written, 8, "2 维 × 4 = 8")
        self.assertEqual(r.milvus_written, 8, "embed 真出向量 -> 全入 Milvus")

        # 抽 1 道题验证 Milvus 真能搜回
        from src.vector_store import search_questions
        any_q = r.questions[0]
        # 用同种子向量搜
        hits = search_questions(
            embedding=_synthetic_vector(1),
            top_k=5, role_family="backend",
        )
        self.assertGreater(len(hits), 0)
        self.assertTrue(
            all(h["role_family"] == "backend" for h in hits),
            f"filter 失效: {hits}",
        )


if __name__ == "__main__":
    unittest.main()
