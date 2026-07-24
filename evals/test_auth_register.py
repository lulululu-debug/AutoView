"""Sprint 6.8 task 1 —— HR 自助注册护栏。

- 参数校验 (422): 短用户名 / 非法字符 / 弱密码 —— pydantic 层, 无需 DB
- 邀请码三态: 未配置=开放 / 配置且错=403 / 配置且对=放行
  (邀请码校验在 DB 查询之前, 错码路径无需 DB)
- PG-gated 端到端: 注册 201 + set cookie + 注册即登录 (/auth/me 通) +
  重名 409 + 注册后用密码正常 login

跑法: python -m unittest evals.test_auth_register
"""
from __future__ import annotations

import os
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)
# bcrypt cost 降到 4 让 eval 快 (与 test_auth 系列同款)
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("JWT_SECRET", "eval-jwt-secret-32chars-abcdefgh")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._invite_saved = os.environ.pop("REGISTER_INVITE_CODE", None)
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        if self._invite_saved is not None:
            os.environ["REGISTER_INVITE_CODE"] = self._invite_saved
        else:
            os.environ.pop("REGISTER_INVITE_CODE", None)


class ValidationTests(_Base):
    def test_short_username_422(self) -> None:
        r = self.client.post("/auth/register", json={
            "username": "ab", "password": "longenough8",
        })
        self.assertEqual(r.status_code, 422)

    def test_illegal_username_chars_422(self) -> None:
        r = self.client.post("/auth/register", json={
            "username": "bad name!", "password": "longenough8",
        })
        self.assertEqual(r.status_code, 422)

    def test_weak_password_422(self) -> None:
        r = self.client.post("/auth/register", json={
            "username": "gooduser", "password": "short",
        })
        self.assertEqual(r.status_code, 422)


class InviteCodeTests(_Base):
    def test_wrong_invite_403(self) -> None:
        os.environ["REGISTER_INVITE_CODE"] = "sesame"
        r = self.client.post("/auth/register", json={
            "username": "gooduser", "password": "longenough8",
            "invite_code": "wrong",
        })
        self.assertEqual(r.status_code, 403)

    def test_missing_invite_403(self) -> None:
        os.environ["REGISTER_INVITE_CODE"] = "sesame"
        r = self.client.post("/auth/register", json={
            "username": "gooduser", "password": "longenough8",
        })
        self.assertEqual(r.status_code, 403)


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL"), "需要 TEST_POSTGRES_URL 跑端到端注册",
)
class RegisterE2ETests(_Base):
    _U = "reg-e2e-user"

    def setUp(self) -> None:
        super().setUp()
        from src.db import init_db
        init_db()
        self._cleanup()

    def tearDown(self) -> None:
        self._cleanup()
        super().tearDown()

    def _cleanup(self) -> None:
        import psycopg
        url = os.environ["POSTGRES_URL"].replace("+psycopg", "")
        with psycopg.connect(url) as conn:
            conn.execute(
                "DELETE FROM users WHERE username = %s", (self._U,),
            )

    def test_register_login_flow(self) -> None:
        # 注册 201 + cookie + role=hr
        r = self.client.post("/auth/register", json={
            "username": self._U, "password": "longenough8",
        })
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["role"], "hr")
        self.assertTrue(r.cookies or r.headers.get("set-cookie"))

        # 注册即登录: cookie 已在 client 会话里, /auth/me 应通
        me = self.client.get("/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], self._U)

        # 重名 409
        dup = self.client.post("/auth/register", json={
            "username": self._U, "password": "longenough8",
        })
        self.assertEqual(dup.status_code, 409)

        # 注册后的密码可正常 login
        login = self.client.post("/auth/login", json={
            "username": self._U, "password": "longenough8",
        })
        self.assertEqual(login.status_code, 200)


if __name__ == "__main__":
    unittest.main()
