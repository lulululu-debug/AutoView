"""API 层基础冒烟 —— Sprint 2-1。

只验骨架: app 能起来, /health 返回 200 + 预期载荷。
业务端点的 eval 在后续 sprint 子任务里逐个加。
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from api.main import API_TITLE, API_VERSION, create_app


class HealthEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app())

    def test_health_returns_200(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)

    def test_health_payload(self):
        r = self.client.get("/health")
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], API_TITLE)
        self.assertEqual(body["version"], API_VERSION)

    def test_openapi_schema_available(self):
        """开发期 /docs 与 /openapi.json 应当可访问, 没有它 API 没法被 HR 端联调。"""
        r = self.client.get("/openapi.json")
        self.assertEqual(r.status_code, 200)
        schema = r.json()
        self.assertIn("/health", schema.get("paths", {}))


if __name__ == "__main__":
    unittest.main()
