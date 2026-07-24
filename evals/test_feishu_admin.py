"""Sprint 6.7 task 3 —— 飞书导入 admin API 护栏。零网络, connector 全 mock。

覆盖:
- status 未配置探测; preview 的 URL 校验 / 目录需授权提示 / 上游错误映射 502
- import 的参数校验 400 / URL 422 / dataset 冲突 409 / 未配置 409
- OAuth callback 的 state 校验 (mismatch -> 400)
- HR 鉴权用 dependency override 绕过 (鉴权本身由 test_auth 系列守)

跑法: python -m unittest evals.test_feishu_admin
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routes import admin_feishu  # noqa: E402
from src import auth  # noqa: E402
from src.schemas import User  # noqa: E402

_ENV = ("FEISHU_APP_ID", "FEISHU_APP_SECRET")


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.pop(k, None) for k in _ENV}
        app = create_app()
        app.dependency_overrides[auth.require_hr_user] = lambda: User(
            user_id="u-test", username="hr", role="hr",
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _configure(self) -> None:
        os.environ["FEISHU_APP_ID"] = "cli_t"
        os.environ["FEISHU_APP_SECRET"] = "s"


class StatusTests(_Base):
    def test_unconfigured(self) -> None:
        r = self.client.get("/admin/feishu/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["configured"])
        self.assertFalse(body["authorized"])
        self.assertIsNone(body["source"])
        self.assertIsNone(body["app_id_masked"])


class PreviewTests(_Base):
    def test_bad_url_422(self) -> None:
        r = self.client.post(
            "/admin/feishu/preview", json={"url": "https://evil.com/wiki/x"},
        )
        self.assertEqual(r.status_code, 422)

    def test_leaf_wiki_node(self) -> None:
        self._configure()
        with mock.patch.object(
            admin_feishu.feishu, "get_wiki_node",
            return_value={
                "title": "Redis 篇", "obj_type": "docx", "obj_token": "D1",
                "has_child": False, "space_id": "S", "node_token": "N1",
            },
        ):
            r = self.client.post(
                "/admin/feishu/preview",
                json={"url": "https://my.feishu.cn/wiki/N1"},
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["title"], "Redis 篇")
        self.assertFalse(body["has_child"])
        self.assertIsNone(body["children"])

    def test_directory_needs_authorization(self) -> None:
        """列子节点抛 FeishuNotAuthorized -> 不 5xx, 返回 needs_authorization。"""
        self._configure()
        with mock.patch.object(
            admin_feishu.feishu, "get_wiki_node",
            return_value={
                "title": "主题目录", "obj_type": "docx", "obj_token": "D1",
                "has_child": True, "space_id": "S", "node_token": "N1",
            },
        ), mock.patch.object(
            admin_feishu.feishu, "list_wiki_children",
            side_effect=admin_feishu.feishu.FeishuNotAuthorized("需授权"),
        ):
            r = self.client.post(
                "/admin/feishu/preview",
                json={"url": "https://my.feishu.cn/wiki/N1"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["needs_authorization"])

    def test_upstream_error_maps_502(self) -> None:
        self._configure()
        with mock.patch.object(
            admin_feishu.feishu, "get_wiki_node",
            side_effect=admin_feishu.feishu.FeishuApiError(99991663, "boom"),
        ):
            r = self.client.post(
                "/admin/feishu/preview",
                json={"url": "https://my.feishu.cn/wiki/N1"},
            )
        self.assertEqual(r.status_code, 502)


class ImportValidationTests(_Base):
    _BODY = {
        "url": "https://my.feishu.cn/wiki/N1",
        "dataset_id": "feishu-test-ds", "topic": "Redis",
    }

    def test_invalid_category_400(self) -> None:
        r = self.client.post(
            "/admin/feishu/import", json={**self._BODY, "category": "poetry"},
        )
        self.assertEqual(r.status_code, 400)

    def test_bad_url_422(self) -> None:
        r = self.client.post(
            "/admin/feishu/import", json={**self._BODY, "url": "nope"},
        )
        self.assertEqual(r.status_code, 422)

    def test_dataset_conflict_409(self) -> None:
        with mock.patch.object(
            admin_feishu.db, "get_dataset", return_value=object(),
        ):
            r = self.client.post("/admin/feishu/import", json=self._BODY)
        self.assertEqual(r.status_code, 409)
        self.assertIn("已存在", r.json()["detail"])

    def test_unconfigured_409(self) -> None:
        with mock.patch.object(
            admin_feishu.db, "get_dataset", return_value=None,
        ):
            r = self.client.post("/admin/feishu/import", json=self._BODY)
        self.assertEqual(r.status_code, 409)
        self.assertIn("飞书未配置", r.json()["detail"])


class ConfigApiTests(_Base):
    """Sprint 6.7 task 5: 前端凭证配置端点。"""

    def test_env_locked_409(self) -> None:
        self._configure()
        r = self.client.put(
            "/admin/feishu/config", json={"app_id": "a", "app_secret": "b"},
        )
        self.assertEqual(r.status_code, 409)
        self.assertIn("部署环境", r.json()["detail"])

    def test_invalid_credentials_422(self) -> None:
        with mock.patch.object(
            admin_feishu.feishu, "test_credentials",
            side_effect=admin_feishu.feishu.FeishuApiError(10003, "invalid app"),
        ):
            r = self.client.put(
                "/admin/feishu/config", json={"app_id": "a", "app_secret": "b"},
            )
        self.assertEqual(r.status_code, 422)
        self.assertIn("凭证无效", r.json()["detail"])

    def test_save_encrypts_and_never_echoes_secret(self) -> None:
        os.environ["JWT_SECRET"] = os.environ.get("JWT_SECRET") or "test-jwt-32ch-secret-abcdefgh"
        saved = {}
        with mock.patch.object(
            admin_feishu.feishu, "test_credentials", return_value=None,
        ), mock.patch.object(
            admin_feishu.db, "set_app_setting",
            side_effect=lambda k, v: saved.__setitem__(k, v),
        ):
            r = self.client.put(
                "/admin/feishu/config",
                json={"app_id": "cli_new", "app_secret": "topsecret"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("topsecret", r.text)               # 不回显
        self.assertEqual(saved["feishu_app_id"], "cli_new")
        self.assertNotIn("topsecret", saved["feishu_app_secret"])  # 加密入库


class OAuthCallbackTests(_Base):
    def test_state_mismatch_400(self) -> None:
        r = self.client.get(
            "/admin/feishu/oauth/callback?code=c&state=forged",
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
