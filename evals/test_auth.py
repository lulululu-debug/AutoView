"""鉴权 eval —— Sprint 5-1.

四层覆盖:
1. 密码 hash + verify (无外部依赖)
2. JWT 签发 + 解码 (无外部依赖)
3. POST /auth/login (需 PG, 用 BCRYPT_ROUNDS=4 让 bcrypt 跑快)
4. require_hr_user dependency (需 PG, 走 TestClient)
"""
from __future__ import annotations

import os
import time
import unittest

# 让 bcrypt 在 eval 里跑得快, 不影响 prod
os.environ.setdefault("BCRYPT_ROUNDS", "4")
# 给 JWT 一个固定 dev secret, 各 test 之间一致
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret")


class PasswordTests(unittest.TestCase):
    def test_hash_then_verify_ok(self):
        from src.auth import hash_password, verify_password
        h = hash_password("hunter2")
        self.assertTrue(verify_password("hunter2", h))

    def test_verify_wrong_password(self):
        from src.auth import hash_password, verify_password
        h = hash_password("hunter2")
        self.assertFalse(verify_password("hunter3", h))

    def test_hash_random_salt(self):
        """同密码两次 hash 应得不同结果 (盐随机)。"""
        from src.auth import hash_password
        h1 = hash_password("x")
        h2 = hash_password("x")
        self.assertNotEqual(h1, h2)

    def test_empty_inputs(self):
        from src.auth import hash_password, verify_password
        with self.assertRaises(ValueError):
            hash_password("")
        self.assertFalse(verify_password("", "$2b$..."))
        self.assertFalse(verify_password("x", ""))


class JwtTests(unittest.TestCase):
    def test_create_and_decode_round_trip(self):
        from src.auth import create_access_token, decode_token
        token = create_access_token(user_id="u1", role="hr")
        payload = decode_token(token)
        self.assertEqual(payload["sub"], "u1")
        self.assertEqual(payload["role"], "hr")
        self.assertIn("exp", payload)
        self.assertIn("iat", payload)

    def test_decode_expired_token(self):
        from src.auth import InvalidToken, create_access_token, decode_token
        # exp 设为过去
        token = create_access_token(user_id="u1", role="hr", expires_minutes=-1)
        # 给一秒留余地
        time.sleep(0.1)
        with self.assertRaises(InvalidToken):
            decode_token(token)

    def test_decode_tampered_token(self):
        from src.auth import InvalidToken, create_access_token, decode_token
        token = create_access_token(user_id="u1", role="hr")
        bad = token[:-3] + "AAA"
        with self.assertRaises(InvalidToken):
            decode_token(bad)

    def test_decode_with_wrong_secret_raises(self):
        from src.auth import InvalidToken, create_access_token, decode_token
        token = create_access_token(user_id="u1", role="hr")
        original = os.environ["JWT_SECRET"]
        try:
            os.environ["JWT_SECRET"] = "another-very-long-secret-string"
            with self.assertRaises(InvalidToken):
                decode_token(token)
        finally:
            os.environ["JWT_SECRET"] = original

    def test_missing_secret_raises(self):
        from src.auth import JwtNotConfigured, create_access_token
        original = os.environ.pop("JWT_SECRET", None)
        try:
            with self.assertRaises(JwtNotConfigured):
                create_access_token(user_id="u1", role="hr")
        finally:
            if original:
                os.environ["JWT_SECRET"] = original

    def test_short_secret_rejected(self):
        from src.auth import JwtNotConfigured, create_access_token
        original = os.environ["JWT_SECRET"]
        try:
            os.environ["JWT_SECRET"] = "short"
            with self.assertRaises(JwtNotConfigured):
                create_access_token(user_id="u1", role="hr")
        finally:
            os.environ["JWT_SECRET"] = original


@unittest.skipUnless(os.environ.get("POSTGRES_URL"), "需要 POSTGRES_URL")
class LoginEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from api.main import create_app
        from src.db import init_db
        init_db()
        cls.client = TestClient(create_app())

    def setUp(self):
        # 清表 + 种一个 HR 账号
        import psycopg
        url = os.environ["POSTGRES_URL"].replace("+psycopg", "")
        with psycopg.connect(url) as conn:
            conn.execute("TRUNCATE users")
        from scripts.seed_users import seed_user
        seed_user(username="hr1", password="pw-hr1", role="hr")
        seed_user(username="boss", password="pw-boss", role="admin")

    def test_login_success(self):
        r = self.client.post("/auth/login", json={"username": "hr1", "password": "pw-hr1"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertEqual(body["role"], "hr")
        self.assertGreater(len(body["access_token"]), 20)
        self.assertGreater(body["expires_in"], 0)

    def test_login_wrong_password_401(self):
        r = self.client.post("/auth/login", json={"username": "hr1", "password": "WRONG"})
        self.assertEqual(r.status_code, 401)

    def test_login_unknown_user_401(self):
        # 关键: 同样的 401, 不应区分"不存在"与"密码错"
        r = self.client.post("/auth/login", json={"username": "ghost", "password": "x"})
        self.assertEqual(r.status_code, 401)
        # 文案也得一样 (防用户枚举)
        body = r.json()
        wrong_pw = self.client.post(
            "/auth/login", json={"username": "hr1", "password": "WRONG"},
        ).json()
        self.assertEqual(body["detail"], wrong_pw["detail"])

    def test_login_empty_credentials_422(self):
        r = self.client.post("/auth/login", json={"username": "", "password": "x"})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/auth/login", json={"username": "hr1", "password": ""})
        self.assertEqual(r.status_code, 422)

    def test_login_admin_returns_admin_role(self):
        r = self.client.post("/auth/login", json={"username": "boss", "password": "pw-boss"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["role"], "admin")


@unittest.skipUnless(os.environ.get("POSTGRES_URL"), "需要 POSTGRES_URL")
class RequireHrUserDependencyTests(unittest.TestCase):
    """挂一个临时端点 /test-hr-only, 验 require_hr_user 在真请求里行为正确。"""

    @classmethod
    def setUpClass(cls):
        from fastapi import Depends
        from fastapi.testclient import TestClient

        from api.main import create_app
        from src.auth import require_hr_user
        from src.db import init_db
        from src.schemas import User
        init_db()

        app = create_app()

        @app.get("/test-hr-only")
        def hr_only(user: User = Depends(require_hr_user)):
            return {"user_id": user.user_id, "role": user.role}

        cls.client = TestClient(app)

    def setUp(self):
        import psycopg
        url = os.environ["POSTGRES_URL"].replace("+psycopg", "")
        with psycopg.connect(url) as conn:
            conn.execute("TRUNCATE users")

    def _login(self, username: str, password: str, role: str = "hr") -> str:
        from scripts.seed_users import seed_user
        seed_user(username=username, password=password, role=role)
        r = self.client.post(
            "/auth/login", json={"username": username, "password": password},
        )
        self.assertEqual(r.status_code, 200)
        return r.json()["access_token"]

    def test_no_token_401(self):
        r = self.client.get("/test-hr-only")
        self.assertEqual(r.status_code, 401)
        # 401 应当带 WWW-Authenticate 头 (RFC 6750)
        self.assertIn("WWW-Authenticate", r.headers)

    def test_malformed_authorization_header_401(self):
        r = self.client.get(
            "/test-hr-only", headers={"Authorization": "NotBearer x"},
        )
        self.assertEqual(r.status_code, 401)

    def test_bad_token_401(self):
        r = self.client.get(
            "/test-hr-only", headers={"Authorization": "Bearer x.y.z"},
        )
        self.assertEqual(r.status_code, 401)

    def test_hr_token_passes(self):
        token = self._login("hr1", "pw-hr1", role="hr")
        r = self.client.get(
            "/test-hr-only", headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["role"], "hr")

    def test_admin_token_passes(self):
        token = self._login("boss", "pw-boss", role="admin")
        r = self.client.get(
            "/test-hr-only", headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 200)

    def test_unknown_role_forbidden_403(self):
        """role 是合法值但既不是 hr 也不是 admin -> 403, 而非 401。
        通过手工构造 token 验证 (DB 不允许其他 role 进, 但 token 可能从外部来)。"""
        from src.auth import create_access_token
        token = create_access_token(user_id="u-rogue", role="candidate")
        r = self.client.get(
            "/test-hr-only", headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
