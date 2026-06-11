"""API 层 eval —— Sprint 2 在跑期间逐步增长。

Sprint 2-1: /health 冒烟 (3 个)
Sprint 2-3: POST /jobs + 异常映射

设计:
- 端到端类(需 POSTGRES_URL): TestClient + 真 PG 验证落库
- 异常映射类(无需 infra): 用 unittest.mock 替换 db.save_job, 验证 503 映射
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

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


@unittest.skipUnless(os.environ.get("POSTGRES_URL"), "需要 POSTGRES_URL")
class CreateJobTests(unittest.TestCase):
    """POST /jobs: 创建 + 校验 + 真的入 PG。"""

    @classmethod
    def setUpClass(cls):
        from src.db import init_db
        init_db()
        cls.client = TestClient(create_app())

    def test_create_job_returns_201_and_persists(self):
        from src.db import load_job
        r = self.client.post("/jobs", json={
            "title": "后端工程师",
            "jd": "负责交易系统",
            "requirements": ["分布式", "数据库"],
            "company_materials": "x 公司",
        })
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["title"], "后端工程师")
        self.assertEqual(body["requirements"], ["分布式", "数据库"])
        self.assertEqual(body["company_materials"], "x 公司")
        self.assertIn("job_id", body)
        # 真落 PG 了
        loaded = load_job(body["job_id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.title, "后端工程师")

    def test_defaults_apply_when_optional_fields_missing(self):
        r = self.client.post("/jobs", json={"title": "t", "jd": "j"})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["requirements"], [])
        self.assertEqual(body["company_materials"], "")

    def test_validation_missing_title(self):
        r = self.client.post("/jobs", json={"jd": "j"})
        self.assertEqual(r.status_code, 422)

    def test_validation_empty_title(self):
        r = self.client.post("/jobs", json={"title": "", "jd": "j"})
        self.assertEqual(r.status_code, 422)

    def test_validation_empty_jd(self):
        r = self.client.post("/jobs", json={"title": "t", "jd": ""})
        self.assertEqual(r.status_code, 422)

    def test_client_cannot_inject_job_id(self):
        """安全: 客户端塞 job_id 不应生效, server 永远自己生成。"""
        r = self.client.post("/jobs", json={
            "title": "t", "jd": "j", "job_id": "client-forged",
        })
        self.assertEqual(r.status_code, 201)
        self.assertNotEqual(r.json()["job_id"], "client-forged")


class ExceptionMappingTests(unittest.TestCase):
    """领域异常 -> HTTP 状态码映射 (无需真 infra, 用 mock 注入异常)。"""

    def test_database_not_configured_returns_503(self):
        from src.db import DatabaseNotConfigured
        with patch(
            "src.db.save_job",
            side_effect=DatabaseNotConfigured("无 POSTGRES_URL"),
        ):
            client = TestClient(create_app())
            r = client.post("/jobs", json={"title": "t", "jd": "j"})
        self.assertEqual(r.status_code, 503)
        body = r.json()
        self.assertIn("数据库", body["detail"])
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
