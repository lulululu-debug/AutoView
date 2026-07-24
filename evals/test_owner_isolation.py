"""Sprint 6.8 task 2 —— 按 owner 数据隔离护栏。PG-gated。

- A 创建的 job: A 列表可见 / B 列表不见; B 直查 plan 端点 404;
  B 列候选人返空 (不泄存在性)
- session / report 经 job 派生归属: B 访问 A 的 session/report -> 404
- admin 全量可见
- POST /jobs 未登录 -> 401 (顺手封掉的存量洞)

跑法: python -m unittest evals.test_owner_isolation
"""
from __future__ import annotations

import os
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("JWT_SECRET", "eval-jwt-secret-32chars-abcdefgh")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL"), "需要 TEST_POSTGRES_URL",
)
class OwnerIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from scripts.seed_users import seed_user
        from src.db import init_db

        init_db()
        cls.client = TestClient(create_app())
        seed_user(username="iso-a", password="pw123456", role="hr")
        seed_user(username="iso-b", password="pw123456", role="hr")
        seed_user(username="iso-admin", password="pw123456", role="admin")
        cls.tok_a = cls._login("iso-a")
        cls.tok_b = cls._login("iso-b")
        cls.tok_admin = cls._login("iso-admin")
        # 鉴权是 cookie 优先 Bearer 兜底 (5.8 设计); 清掉登录残留的 cookie,
        # 让后续请求的身份完全由显式 Bearer 决定
        cls.client.cookies.clear()

        # A 创建 job (经 API, 归属自动落 A)
        r = cls.client.post(
            "/jobs", json={"title": "隔离测试岗", "jd": "x"},
            headers=cls._h(cls.tok_a),
        )
        assert r.status_code == 201, r.text
        cls.job_id = r.json()["job_id"]

        # 直接种 A 的 session + report (经 job 派生归属)
        from src.db import save_report, save_session
        from src.schemas import EvaluationReport, InterviewSession

        sess = InterviewSession(plan_id="p-iso", job_id=cls.job_id)
        save_session(sess)
        cls.session_id = sess.session_id
        report = EvaluationReport(
            session_id=cls.session_id, content_scores=[],
            performance_observations=[], overall=50.0, summary="x",
        )
        save_report(report)
        cls.report_id = report.report_id

    @classmethod
    def _login(cls, username: str) -> str:
        r = cls.client.post(
            "/auth/login", json={"username": username, "password": "pw123456"},
        )
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    @staticmethod
    def _h(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def test_create_job_requires_auth(self) -> None:
        """存量洞已封: 未登录不能建岗。"""
        c = TestClient(create_app())  # 干净 client, 无 cookie
        r = c.post("/jobs", json={"title": "x", "jd": "x"})
        self.assertEqual(r.status_code, 401)

    def test_list_jobs_isolated(self) -> None:
        ids_a = {j["job_id"] for j in self.client.get(
            "/hr/jobs", headers=self._h(self.tok_a)).json()}
        ids_b = {j["job_id"] for j in self.client.get(
            "/hr/jobs", headers=self._h(self.tok_b)).json()}
        self.assertIn(self.job_id, ids_a)
        self.assertNotIn(self.job_id, ids_b)

    def test_admin_sees_all(self) -> None:
        ids = {j["job_id"] for j in self.client.get(
            "/hr/jobs", headers=self._h(self.tok_admin)).json()}
        self.assertIn(self.job_id, ids)

    def test_cross_candidates_list_empty(self) -> None:
        r = self.client.get(
            f"/hr/jobs/{self.job_id}/candidates", headers=self._h(self.tok_b),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_cross_plan_404(self) -> None:
        r = self.client.get(
            f"/hr/jobs/{self.job_id}/candidates/whoever/plan",
            headers=self._h(self.tok_b),
        )
        self.assertEqual(r.status_code, 404)

    def test_cross_session_404_own_200(self) -> None:
        own = self.client.get(
            f"/hr/sessions/{self.session_id}", headers=self._h(self.tok_a),
        )
        self.assertEqual(own.status_code, 200)
        cross = self.client.get(
            f"/hr/sessions/{self.session_id}", headers=self._h(self.tok_b),
        )
        self.assertEqual(cross.status_code, 404)

    def test_cross_report_404_own_200_admin_200(self) -> None:
        self.assertEqual(self.client.get(
            f"/hr/reports/{self.report_id}", headers=self._h(self.tok_a),
        ).status_code, 200)
        self.assertEqual(self.client.get(
            f"/hr/reports/{self.report_id}", headers=self._h(self.tok_b),
        ).status_code, 404)
        self.assertEqual(self.client.get(
            f"/hr/reports/{self.report_id}", headers=self._h(self.tok_admin),
        ).status_code, 200)

    def test_cross_review_patch_404(self) -> None:
        r = self.client.patch(
            f"/hr/reports/{self.report_id}/review",
            json={"decision": "recommend"},
            headers=self._h(self.tok_b),
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
