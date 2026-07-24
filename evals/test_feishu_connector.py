"""Sprint 6.7 task 1 —— 飞书 connector 护栏。零网络, 全 mock。

锁住的契约:
- parse_url 纯函数: 域名白名单 + /wiki|/docx 形态, 其余 None
- is_configured / authorize_url: 未配置抛 FeishuNotConfigured;
  scope **显式**出现在授权 URL(实战坑: 不写只给基础 scope)
- _call 权限重试: 131006 等权限码 + 有 user token -> 用 user 重试一次;
  无 user token -> FeishuNotAuthorized; 非权限码直接透传
- tenant token 进程缓存: 未过期不重复请求

跑法: python -m unittest evals.test_feishu_connector
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src.connectors import feishu  # noqa: E402

_ENV = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_OAUTH_REDIRECT")


class _EnvCase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.pop(k, None) for k in _ENV}
        feishu.reset_for_testing()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        feishu.reset_for_testing()

    def _configure(self) -> None:
        os.environ["FEISHU_APP_ID"] = "cli_test"
        os.environ["FEISHU_APP_SECRET"] = "sec_test"


class ParseUrlTests(unittest.TestCase):
    def test_wiki_and_docx_forms(self) -> None:
        cases = {
            "https://xx.feishu.cn/wiki/AbCd123": ("wiki", "AbCd123"),
            "https://my.feishu.cn/wiki/Tok?from=x": ("wiki", "Tok"),
            "https://a.larksuite.com/docx/DocTok": ("docx", "DocTok"),
            "https://xx.feishu.cn/docx/DocTok#h1": ("docx", "DocTok"),
        }
        for url, want in cases.items():
            self.assertEqual(feishu.parse_url(url), want, url)

    def test_rejects_garbage(self) -> None:
        for url in (
            "https://evil.com/wiki/Tok",           # 域名不在白名单
            "https://feishu.cn.evil.com/wiki/T",   # 后缀伪造
            "https://xx.feishu.cn/sheets/Tok",     # 非 wiki/docx
            "https://xx.feishu.cn/wiki/",          # 空 token
            "not a url",
            "",
        ):
            self.assertIsNone(feishu.parse_url(url), url)


class ConfigTests(_EnvCase):
    def test_unconfigured(self) -> None:
        self.assertFalse(feishu.is_configured())
        with self.assertRaises(feishu.FeishuNotConfigured):
            feishu.authorize_url()

    def test_authorize_url_has_explicit_scopes(self) -> None:
        self._configure()
        url = feishu.authorize_url(state="s1")
        self.assertIn("wiki%3Awiki%3Areadonly", url)
        self.assertIn("docx%3Adocument%3Areadonly", url)
        self.assertIn("cli_test", url)
        self.assertIn("state=s1", url)


class CallRetryTests(_EnvCase):
    """权限码重试三分支。"""

    def setUp(self) -> None:
        super().setUp()
        self._configure()

    def test_permission_code_without_user_token(self) -> None:
        with mock.patch.object(
            feishu, "_tenant_token", return_value="t",
        ), mock.patch.object(
            feishu, "_request",
            side_effect=feishu.FeishuApiError(131006, "no perm"),
        ), mock.patch.object(feishu, "_user_token", return_value=None):
            with self.assertRaises(feishu.FeishuNotAuthorized):
                feishu._call("GET", "/x")

    def test_permission_code_retries_with_user_token(self) -> None:
        calls = []

        def fake_request(method, path, *, token=None, params=None, body=None):
            calls.append(token)
            if token == "t":
                raise feishu.FeishuApiError(131006, "no perm")
            return {"code": 0, "data": {"ok": True}}

        with mock.patch.object(
            feishu, "_tenant_token", return_value="t",
        ), mock.patch.object(
            feishu, "_request", side_effect=fake_request,
        ), mock.patch.object(feishu, "_user_token", return_value="u"):
            payload = feishu._call("GET", "/x")
        self.assertEqual(calls, ["t", "u"])
        self.assertTrue(payload["data"]["ok"])

    def test_non_permission_code_passes_through(self) -> None:
        with mock.patch.object(
            feishu, "_tenant_token", return_value="t",
        ), mock.patch.object(
            feishu, "_request",
            side_effect=feishu.FeishuApiError(99999, "boom"),
        ):
            with self.assertRaises(feishu.FeishuApiError) as ctx:
                feishu._call("GET", "/x")
            self.assertEqual(ctx.exception.code, 99999)


class TenantCacheTests(_EnvCase):
    def test_cached_until_expiry(self) -> None:
        self._configure()
        with mock.patch.object(
            feishu, "_request",
            return_value={
                "code": 0, "tenant_access_token": "tok", "expire": 7200,
            },
        ) as m:
            self.assertEqual(feishu._tenant_token(), "tok")
            self.assertEqual(feishu._tenant_token(), "tok")
            self.assertEqual(m.call_count, 1, "第二次应命中进程缓存")


if __name__ == "__main__":
    unittest.main()
