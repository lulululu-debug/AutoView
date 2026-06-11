"""Embedding 抽象层 eval —— Sprint 3-1。

不依赖真 OpenAI API: 用 monkey-patch 模拟 OpenAI client, 验证缓存命中。
"""
from __future__ import annotations

import os
import unittest


class StubFallbackTests(unittest.TestCase):
    """无 OPENAI_API_KEY 时走 stub, 不打 API, 不污染缓存。"""

    def setUp(self):
        os.environ.pop("OPENAI_API_KEY", None)

    def test_no_key_returns_stub_vector(self):
        from src.embeddings import EMBEDDING_DIM, embed, is_stub_vector
        v = embed("一段文本")
        self.assertEqual(len(v), EMBEDDING_DIM)
        self.assertTrue(is_stub_vector(v))

    def test_empty_text_returns_stub(self):
        from src.embeddings import embed, is_stub_vector
        self.assertTrue(is_stub_vector(embed("")))

    def test_stub_vector_detected(self):
        from src.embeddings import EMBEDDING_DIM, is_stub_vector
        self.assertTrue(is_stub_vector([0.0] * EMBEDDING_DIM))
        # 真向量(随便编, 全非零)不应被误判为 stub
        self.assertFalse(is_stub_vector([0.1] * EMBEDDING_DIM))
        # 维度不对 -> 视作 stub (安全)
        self.assertTrue(is_stub_vector([0.1, 0.2, 0.3]))


class CacheKeyTests(unittest.TestCase):
    def test_key_stability(self):
        from src.cache.embedding_cache import make_key
        k1 = make_key("hello", "text-embedding-3-small")
        k2 = make_key("hello", "text-embedding-3-small")
        self.assertEqual(k1, k2)
        self.assertTrue(k1.startswith("emb:"))
        self.assertEqual(len(k1), 4 + 64, "emb: + sha256 hex(64)")

    def test_key_changes_with_text_or_model(self):
        from src.cache.embedding_cache import make_key
        base = make_key("hello", "m1")
        self.assertNotEqual(base, make_key("hello!", "m1"))
        self.assertNotEqual(base, make_key("hello", "m2"))


@unittest.skipUnless(os.environ.get("REDIS_URL"), "需要 REDIS_URL")
class CacheRoundTripTests(unittest.TestCase):
    def test_set_get(self):
        from src.cache.embedding_cache import get, make_key, set as cset
        k = make_key("hi", "m")
        v = [0.1, 0.2, 0.3]
        cset(k, v)
        self.assertEqual(get(k), v)

    def test_empty_vector_not_set(self):
        from src.cache.embedding_cache import get, make_key, set as cset
        k = make_key("empty-test", "m")
        # 先清掉, 避免上一次测试残留
        from src.cache.base import get_redis
        get_redis().delete(k)
        cset(k, [])
        self.assertIsNone(get(k), "空向量不应入缓存")


@unittest.skipUnless(os.environ.get("REDIS_URL"), "需要 REDIS_URL")
class EmbedIntegrationTests(unittest.TestCase):
    """monkey-patch OpenAI 客户端验证: 缓存命中后不再打 API。"""

    def setUp(self):
        # 清掉测试 key 防上轮残留
        from src.cache.base import get_redis
        get_redis().delete(self._cache_key("integration-test-text"))
        get_redis().delete(self._cache_key("integration-test-different"))

    def _cache_key(self, text: str) -> str:
        from src.cache.embedding_cache import make_key
        return make_key(
            text,
            os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        )

    def test_cache_hit_avoids_api_call(self):
        import openai
        import src.embeddings as emb_mod
        from src.embeddings import EMBEDDING_DIM

        # 注入假 OpenAI client; tearDown 必须恢复, 否则后续 test_seed_questions 等
        # 跑 LLM 时会撞上"_FakeOpenAI 没有 chat 属性"
        self._original_openai_class = openai.OpenAI

        calls = {"n": 0}
        class _FakeEmbeddings:
            def create(self, **_kw):
                calls["n"] += 1
                class D:
                    embedding = [0.5] * EMBEDDING_DIM
                class R:
                    data = [D()]
                return R()
        class _FakeOpenAI:
            def __init__(self, **_kw):
                self.embeddings = _FakeEmbeddings()
        openai.OpenAI = _FakeOpenAI
        os.environ["OPENAI_API_KEY"] = "test-key"

        v1 = emb_mod.embed("integration-test-text")        # miss -> API
        v2 = emb_mod.embed("integration-test-text")        # hit
        v3 = emb_mod.embed("integration-test-different")   # miss -> API
        self.assertEqual(v1, v2)
        self.assertEqual(len(v1), EMBEDDING_DIM)
        self.assertEqual(
            calls["n"], 2,
            f"3 次 embed (2 同 + 1 不同) 应只打 2 次 API, 实际 {calls['n']}",
        )

    def tearDown(self):
        os.environ.pop("OPENAI_API_KEY", None)
        # 恢复 openai.OpenAI, 防止 monkey-patch 泄漏到其他 eval 模块
        if hasattr(self, "_original_openai_class"):
            import openai
            openai.OpenAI = self._original_openai_class


if __name__ == "__main__":
    unittest.main()
