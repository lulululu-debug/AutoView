"""Sprint 5.8 task 2 鉴权升级护栏: cookie httpOnly + Bearer 双路径 + /me + /logout。

护栏覆盖:
- /auth/login: Set-Cookie 含 HttpOnly + SameSite=Strict + Path=/; body 仍含
  access_token (转期 evals + 脚本仍走 Bearer 路径不变)
- require_hr_user: cookie 单独命中 / Bearer 单独命中 / 两者都没 -> 401
- /auth/me: 200 返 {user_id, username, role}; 401 无认证
- /auth/logout: 204 + Set-Cookie Max-Age=0 清掉 token
- CORS preflight: Access-Control-Allow-Credentials true; allow_origins 不是 *
"""
from __future__ import annotations

import os
import unittest

# 让 bcrypt 在 eval 里跑得快; 给固定 JWT secret
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-cookie-eval")
os.environ.pop("OPENAI_API_KEY", None)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 重新覆盖 (load_dotenv 可能把 .env 的 JWT_SECRET 加回来覆盖, 但 OK)
os.environ.setdefault("BCRYPT_ROUNDS", "4")


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL"),
    "需要 POSTGRES_URL 跑 /auth/login (查 user 表)",
)
class CookieLoginTests(unittest.TestCase):
    """登录响应 Set-Cookie 的属性 + body 兼容 Bearer 路径。"""

    USERNAME = "cookie-eval-hr"
    PASSWORD = "testpw"

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from api.main import create_app
        from scripts.seed_users import seed_user
        from src import db
        db.init_db()
        seed_user(username=cls.USERNAME, password=cls.PASSWORD, role="hr")
        cls.app = create_app()
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        from src.db.base import session_scope
        from src.db.models import UserORM
        with session_scope() as s:
            s.query(UserORM).filter(UserORM.username == cls.USERNAME).delete()

    def _login(self):
        return self.client.post(
            "/auth/login",
            json={"username": self.USERNAME, "password": self.PASSWORD},
        )

    def test_login_sets_httponly_strict_cookie(self):
        r = self._login()
        self.assertEqual(r.status_code, 200)
        sc = r.headers.get("set-cookie", "")
        self.assertIn("auth_token=", sc, "Set-Cookie 应当含 auth_token 名")
        # httpx case-insensitive, 但 Starlette set_cookie 的输出 "HttpOnly" /
        # "SameSite=strict" 是固定的; 用 lower() 防大小写变动
        self.assertIn("httponly", sc.lower())
        self.assertIn("samesite=strict", sc.lower())
        self.assertIn("path=/", sc.lower())

    def test_login_body_still_has_bearer(self):
        """转期 evals + 脚本仍用 access_token; 不能因为加了 cookie 就拿掉。"""
        r = self._login()
        body = r.json()
        self.assertIn("access_token", body)
        self.assertIn("role", body)
        self.assertEqual(body["role"], "hr")


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL"),
    "需要 POSTGRES_URL 跑端到端 auth",
)
class AuthDualPathTests(unittest.TestCase):
    """require_hr_user 双路径: cookie 或 Bearer 任一命中即通过。"""

    USERNAME = "dualpath-eval-hr"
    PASSWORD = "testpw"

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from api.main import create_app
        from scripts.seed_users import seed_user
        from src import db
        db.init_db()
        seed_user(username=cls.USERNAME, password=cls.PASSWORD, role="hr")
        cls.app = create_app()

        # 拿一份 token (登录一次, 拿到 body access_token 用于 Bearer test)
        client = TestClient(cls.app)
        r = client.post(
            "/auth/login",
            json={"username": cls.USERNAME, "password": cls.PASSWORD},
        )
        assert r.status_code == 200, r.text
        cls.token = r.json()["access_token"]
        # 把同一个 client 留作 cookie 测试 (TestClient 自动持 cookie)
        cls.client_with_cookie = client

    @classmethod
    def tearDownClass(cls):
        from src.db.base import session_scope
        from src.db.models import UserORM
        with session_scope() as s:
            s.query(UserORM).filter(UserORM.username == cls.USERNAME).delete()

    def test_me_via_cookie(self):
        r = self.client_with_cookie.get("/auth/me")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["username"], self.USERNAME)
        self.assertEqual(body["role"], "hr")

    def test_me_via_bearer_only(self):
        """新 client 无 cookie, 只用 Bearer 头, 也应该 200。"""
        from fastapi.testclient import TestClient
        fresh = TestClient(self.app)
        r = fresh.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["role"], "hr")

    def test_me_no_token_returns_401(self):
        from fastapi.testclient import TestClient
        fresh = TestClient(self.app)
        r = fresh.get("/auth/me")
        self.assertEqual(r.status_code, 401)

    def test_me_invalid_token_returns_401(self):
        from fastapi.testclient import TestClient
        fresh = TestClient(self.app)
        r = fresh.get(
            "/auth/me",
            headers={"Authorization": "Bearer not.a.jwt"},
        )
        self.assertEqual(r.status_code, 401)


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL"),
    "需要 POSTGRES_URL 跑 /auth/logout 端到端",
)
class LogoutTests(unittest.TestCase):

    USERNAME = "logout-eval-hr"
    PASSWORD = "testpw"

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from api.main import create_app
        from scripts.seed_users import seed_user
        from src import db
        db.init_db()
        seed_user(username=cls.USERNAME, password=cls.PASSWORD, role="hr")
        cls.app = create_app()
        cls.client = TestClient(cls.app)
        r = cls.client.post(
            "/auth/login",
            json={"username": cls.USERNAME, "password": cls.PASSWORD},
        )
        assert r.status_code == 200, r.text

    @classmethod
    def tearDownClass(cls):
        from src.db.base import session_scope
        from src.db.models import UserORM
        with session_scope() as s:
            s.query(UserORM).filter(UserORM.username == cls.USERNAME).delete()

    def test_logout_clears_cookie(self):
        r = self.client.post("/auth/logout")
        self.assertEqual(r.status_code, 204)
        sc = r.headers.get("set-cookie", "")
        self.assertIn("auth_token=", sc)
        # Max-Age=0 表示立刻过期
        self.assertIn("max-age=0", sc.lower())

    def test_me_after_logout_returns_401(self):
        # 重新 login 一次 (上一条 test 已经 logout 了)
        r = self.client.post(
            "/auth/login",
            json={"username": self.USERNAME, "password": self.PASSWORD},
        )
        self.assertEqual(r.status_code, 200)
        # 现在 logout
        r = self.client.post("/auth/logout")
        self.assertEqual(r.status_code, 204)
        # /me 应该 401 (cookie 被清, 没 Bearer)
        r = self.client.get("/auth/me")
        self.assertEqual(r.status_code, 401)


class CorsConfigTests(unittest.TestCase):
    """CORS 配置: allow_origins 非 *, allow_credentials true。
    不需要 PG: 走 OPTIONS preflight, CORS 中间件在路由前就响应。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from api.main import create_app
        cls.app = create_app()
        cls.client = TestClient(cls.app)

    def test_cors_preflight_allows_credentials(self):
        r = self.client.options(
            "/auth/me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(r.status_code, 200)
        # 允许 credentials = true 才能让浏览器送 cookie 跨域
        self.assertEqual(
            r.headers.get("access-control-allow-credentials", "").lower(),
            "true",
        )
        # allow_origin 必须是显式 origin, 不是 "*" (浏览器规则: * 与 credentials 互斥)
        allow_origin = r.headers.get("access-control-allow-origin", "")
        self.assertNotEqual(allow_origin, "*")
        self.assertEqual(allow_origin, "http://localhost:3000")


if __name__ == "__main__":
    unittest.main()
