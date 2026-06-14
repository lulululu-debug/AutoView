"""Sprint 5.8 patch: Milvus search 在 'released' 类错误时自动 load + 重试。

定位:
- 跨进程 drop+reseed 会让持有 stale client 的进程 search 抛
  "Collection 'X' is in state 'released'; call load() before search"。
- 修复: _search_with_load_retry 在 catch 到 reloadable 标志时, 调
  client.load_collection() 然后重试一次。
- 不算 Sprint 5.9, 是 Sprint 3 起 Milvus 路径的可靠性补丁。

测试方式:
- 直接 mock pymilvus client 触发 reloadable 异常一次, 验证 load+retry 后返结果。
- 同时验证非 reloadable 异常 (如 schema mismatch) 不重试, 直接上抛。
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.pop("OPENAI_API_KEY", None)


class MilvusRetryUnitTests(unittest.TestCase):
    """直接测 _search_with_load_retry 的重试语义, 不依赖真实 Milvus。"""

    def test_reloadable_error_triggers_load_and_retry(self):
        from src.vector_store.operations import _search_with_load_retry

        class FakeClient:
            def __init__(self):
                self.search_calls = 0
                self.load_calls = 0

            def search(self, **kwargs):
                self.search_calls += 1
                if self.search_calls == 1:
                    # 第一次抛 reloadable 错
                    raise RuntimeError(
                        "MilvusException: Collection 'questions' is in state "
                        "'released'; call load() before search"
                    )
                # 重试时返成功
                return [[{"id": "q1", "distance": 0.1, "entity": {"text": "Q"}}]]

            def load_collection(self, name):
                self.load_calls += 1

        fake = FakeClient()
        with patch(
            "src.vector_store.operations.get_client", return_value=fake,
        ):
            result = _search_with_load_retry(
                collection_name="questions",
                embedding=[0.1, 0.2],
                top_k=3,
                expr="",
                output_fields=["text"],
            )
        self.assertEqual(fake.search_calls, 2, "应当重试一次")
        self.assertEqual(fake.load_calls, 1, "应当调一次 load_collection")
        self.assertTrue(result)

    def test_collection_not_found_also_retries(self):
        """drop 后 client 看不到 collection, 错误消息是 'collection not found'。"""
        from src.vector_store.operations import _search_with_load_retry

        class FakeClient:
            def __init__(self):
                self.search_calls = 0
                self.load_calls = 0

            def search(self, **kwargs):
                self.search_calls += 1
                if self.search_calls == 1:
                    raise RuntimeError("Collection not found: documents")
                return []

            def load_collection(self, name):
                self.load_calls += 1

        fake = FakeClient()
        with patch(
            "src.vector_store.operations.get_client", return_value=fake,
        ):
            _search_with_load_retry(
                collection_name="documents",
                embedding=[0.1],
                top_k=3,
                expr="",
                output_fields=["text"],
            )
        self.assertEqual(fake.load_calls, 1)
        self.assertEqual(fake.search_calls, 2)

    def test_non_reloadable_error_propagates_without_retry(self):
        """schema mismatch 等结构性错误 load 救不了, 不该重试, 直接上抛。"""
        from src.vector_store.operations import _search_with_load_retry

        class FakeClient:
            def __init__(self):
                self.search_calls = 0
                self.load_calls = 0

            def search(self, **kwargs):
                self.search_calls += 1
                raise RuntimeError("schema mismatch: field 'embedding' dim 768 vs 1536")

            def load_collection(self, name):
                self.load_calls += 1

        fake = FakeClient()
        with patch(
            "src.vector_store.operations.get_client", return_value=fake,
        ):
            with self.assertRaises(RuntimeError) as cm:
                _search_with_load_retry(
                    collection_name="questions",
                    embedding=[0.1],
                    top_k=3,
                    expr="",
                    output_fields=["text"],
                )
        self.assertIn("schema mismatch", str(cm.exception))
        self.assertEqual(fake.search_calls, 1, "不该重试")
        self.assertEqual(fake.load_calls, 0, "也不该 load")

    def test_load_failure_does_not_mask_subsequent_search_error(self):
        """load 自己抛 + 重试又抛 -> 上抛 (但 load 的失败不影响重试本身的语义)。"""
        from src.vector_store.operations import _search_with_load_retry

        class FakeClient:
            def __init__(self):
                self.search_calls = 0

            def search(self, **kwargs):
                self.search_calls += 1
                # 第一次和第二次都抛 reloadable
                raise RuntimeError("Collection in state 'released'")

            def load_collection(self, name):
                raise RuntimeError("milvus-lite server died")

        fake = FakeClient()
        with patch(
            "src.vector_store.operations.get_client", return_value=fake,
        ):
            with self.assertRaises(RuntimeError):
                _search_with_load_retry(
                    collection_name="questions",
                    embedding=[0.1],
                    top_k=3,
                    expr="",
                    output_fields=["text"],
                )
        # 应当尝试一次 search → load(失败) → 重试 search → 最终抛
        self.assertEqual(fake.search_calls, 2)


if __name__ == "__main__":
    unittest.main()
