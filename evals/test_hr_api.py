"""HR 端 API eval —— Sprint 5-2。

覆盖:
1. 鉴权: 缺 token / 错 role -> 401 / 403
2. GET /hr/jobs 列表
3. GET /hr/jobs/{id}/candidates + 状态推导四态 (plan_pending / ready /
   completed / reviewed)
4. GET /hr/reports/{id} HR 视角
5. PATCH /hr/reports/{id}/review 复核流程 + 状态切到 reviewed
6. 复核覆盖: 重复 PATCH 同 report -> 用最新一条
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret")


@unittest.skipUnless(os.environ.get("POSTGRES_URL"), "需要 POSTGRES_URL")
class _HrApiBase(unittest.TestCase):
    """共享: init_db + TestClient + 清表 + 种 HR 账号 + 拿 token。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from api.main import create_app
        from src.db import init_db
        init_db()
        cls.client = TestClient(create_app())

    def setUp(self):
        import psycopg
        url = os.environ["POSTGRES_URL"].replace("+psycopg", "")
        with psycopg.connect(url) as conn:
            conn.execute(
                "TRUNCATE users, jobs, candidates, interview_plans, "
                "interview_sessions, evaluation_reports, review_records CASCADE"
            )
        from scripts.seed_users import seed_user
        seed_user(username="hr1", password="pw", role="hr")
        self.hr_token = self._login("hr1", "pw")
        # 非 HR role 的 token 直接手签, 不走 PG (seed_users 也只允许 hr/admin)
        from src.auth import create_access_token
        self.cand_token = create_access_token(
            user_id="u-cand", role="candidate",
        )

    def _login(self, username: str, password: str) -> str:
        r = self.client.post(
            "/auth/login", json={"username": username, "password": password},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["access_token"]

    def _hr_get(self, path: str):
        return self.client.get(
            path, headers={"Authorization": f"Bearer {self.hr_token}"},
        )

    def _hr_patch(self, path: str, json_body):
        return self.client.patch(
            path, json=json_body,
            headers={"Authorization": f"Bearer {self.hr_token}"},
        )

    def _seed_job(self, title: str = "后端") -> str:
        from src.db import save_job
        from src.schemas import JobContext
        job = JobContext(title=title, jd="x")
        save_job(job)
        return job.job_id

    def _seed_candidate(self, job_id: str, resume: str = "张三 / 后端") -> str:
        from src.db import save_candidate
        from src.schemas import CandidateProfile
        cand = CandidateProfile(job_id=job_id, resume=resume)
        save_candidate(cand)
        return cand.candidate_id

    def _seed_plan_for_candidate(self, candidate_id: str, job_id: str) -> str:
        from src.db import save_plan
        from src.schemas import (
            Competency, InterviewPlan, InterviewRound, Question,
            QuestionCategory, QuestionType,
        )
        comp = Competency(name="技术深度", description="x")
        q = Question(
            competency_id=comp.competency_id, text="?", type=QuestionType.OPEN,
            category=QuestionCategory.KNOWLEDGE,
        )
        plan = InterviewPlan(
            job_id=job_id,
            rounds=[InterviewRound(
                index=0, title="主面", competencies=[comp], questions=[q],
            )],
        )
        save_plan(plan, candidate_id=candidate_id)
        return plan.plan_id

    def _seed_session_and_report(self, plan_id: str, job_id: str) -> tuple[str, str]:
        from src.db import save_report, save_session
        from src.schemas import (
            DimensionScore, EvaluationReport, InterviewSession, SessionStatus,
        )
        sess = InterviewSession(
            plan_id=plan_id, job_id=job_id, status=SessionStatus.COMPLETED,
        )
        save_session(sess)
        rep = EvaluationReport(
            session_id=sess.session_id,
            content_scores=[DimensionScore(
                competency_id="c1", score=80.0, evidence=["ev"],
            )],
            overall=80.0,
            summary="...",
        )
        save_report(rep)
        return sess.session_id, rep.report_id


class AuthGuardTests(_HrApiBase):
    def test_list_jobs_requires_token(self):
        r = self.client.get("/hr/jobs")
        self.assertEqual(r.status_code, 401)

    def test_list_jobs_rejects_candidate_role(self):
        r = self.client.get(
            "/hr/jobs", headers={"Authorization": f"Bearer {self.cand_token}"},
        )
        self.assertEqual(r.status_code, 403)

    def test_list_candidates_requires_token(self):
        r = self.client.get("/hr/jobs/x/candidates")
        self.assertEqual(r.status_code, 401)

    def test_get_report_requires_token(self):
        r = self.client.get("/hr/reports/x")
        self.assertEqual(r.status_code, 401)

    def test_patch_review_rejects_candidate_role(self):
        r = self.client.patch(
            "/hr/reports/x/review",
            json={"decision": "recommend"},
            headers={"Authorization": f"Bearer {self.cand_token}"},
        )
        self.assertEqual(r.status_code, 403)


class ListJobsTests(_HrApiBase):
    def test_empty_list(self):
        r = self._hr_get("/hr/jobs")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_returns_jobs_in_recent_first_order(self):
        import time
        j1 = self._seed_job("job 1")
        time.sleep(0.02)
        j2 = self._seed_job("job 2")
        r = self._hr_get("/hr/jobs")
        body = r.json()
        self.assertEqual(len(body), 2)
        self.assertEqual(body[0]["job_id"], j2, "最新的在前")
        self.assertEqual(body[1]["job_id"], j1)


class ListCandidatesStatusTests(_HrApiBase):
    """状态推导四态都覆盖。"""

    def test_plan_pending_when_no_plan(self):
        job = self._seed_job()
        cand = self._seed_candidate(job)
        r = self._hr_get(f"/hr/jobs/{job}/candidates")
        body = r.json()
        self.assertEqual(len(body), 1)
        item = body[0]
        self.assertEqual(item["candidate_id"], cand)
        self.assertEqual(item["status"], "plan_pending")
        self.assertIsNone(item["report_id"])
        self.assertIsNone(item["session_id"])

    def test_ready_when_plan_but_no_session(self):
        job = self._seed_job()
        cand = self._seed_candidate(job)
        self._seed_plan_for_candidate(cand, job)
        r = self._hr_get(f"/hr/jobs/{job}/candidates")
        item = r.json()[0]
        self.assertEqual(item["status"], "ready")

    def test_completed_when_session_and_report(self):
        job = self._seed_job()
        cand = self._seed_candidate(job)
        plan = self._seed_plan_for_candidate(cand, job)
        sid, rid = self._seed_session_and_report(plan, job)
        r = self._hr_get(f"/hr/jobs/{job}/candidates")
        item = r.json()[0]
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["session_id"], sid)
        self.assertEqual(item["report_id"], rid)
        self.assertIsNone(item["review_decision"])

    def test_reviewed_after_patch(self):
        job = self._seed_job()
        cand = self._seed_candidate(job)
        plan = self._seed_plan_for_candidate(cand, job)
        _, rid = self._seed_session_and_report(plan, job)
        # 提交 review
        r = self._hr_patch(
            f"/hr/reports/{rid}/review",
            {"decision": "recommend", "comments": "good match"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        # 状态切到 reviewed
        items = self._hr_get(f"/hr/jobs/{job}/candidates").json()
        self.assertEqual(items[0]["status"], "reviewed")
        self.assertEqual(items[0]["review_decision"], "recommend")

    def test_resume_excerpt_truncated_at_200(self):
        job = self._seed_job()
        long_resume = "x" * 500
        self._seed_candidate(job, resume=long_resume)
        item = self._hr_get(f"/hr/jobs/{job}/candidates").json()[0]
        self.assertTrue(item["resume_excerpt"].endswith("..."))
        self.assertEqual(len(item["resume_excerpt"]), 203)  # 200 + "..."

    def test_unknown_job_returns_empty(self):
        r = self._hr_get("/hr/jobs/no-such-job/candidates")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])


class GetSingleCandidateTests(_HrApiBase):
    """GET /hr/jobs/{j}/candidates/{c}: 单候选人 + 状态. Sprint 5-5 详情页用。"""

    def test_get_returns_status(self):
        job = self._seed_job()
        cand = self._seed_candidate(job)
        plan = self._seed_plan_for_candidate(cand, job)
        _, rid = self._seed_session_and_report(plan, job)
        r = self._hr_get(f"/hr/jobs/{job}/candidates/{cand}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["candidate_id"], cand)
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["report_id"], rid)

    def test_unknown_candidate_404(self):
        r = self._hr_get("/hr/jobs/x/candidates/ghost")
        self.assertEqual(r.status_code, 404)

    def test_cross_job_404(self):
        """安全: 用 job-A 的 path 偷看 job-B 的 candidate 应 404。"""
        job_a = self._seed_job("A")
        job_b = self._seed_job("B")
        cand_b = self._seed_candidate(job_b)
        r = self._hr_get(f"/hr/jobs/{job_a}/candidates/{cand_b}")
        self.assertEqual(r.status_code, 404)


class GetReportTests(_HrApiBase):
    def test_get_report_404(self):
        r = self._hr_get("/hr/reports/no-such")
        self.assertEqual(r.status_code, 404)

    def test_get_report_returns_full_eval_report(self):
        job = self._seed_job()
        cand = self._seed_candidate(job)
        plan = self._seed_plan_for_candidate(cand, job)
        _, rid = self._seed_session_and_report(plan, job)
        r = self._hr_get(f"/hr/reports/{rid}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["report_id"], rid)
        self.assertIn("content_scores", body)
        self.assertIn("performance_observations", body)
        self.assertIn("rag_context_chunk_ids", body)
        self.assertEqual(body["overall"], 80.0)


class SubmitReviewTests(_HrApiBase):
    def setUp(self):
        super().setUp()
        job = self._seed_job()
        cand = self._seed_candidate(job)
        plan = self._seed_plan_for_candidate(cand, job)
        _, self.report_id = self._seed_session_and_report(plan, job)

    def test_submit_review_persists(self):
        r = self._hr_patch(
            f"/hr/reports/{self.report_id}/review",
            {
                "decision": "borderline",
                "comments": "P99 改造能力强, 沟通待观察",
                "dimension_overrides": [
                    {"competency_id": "c1", "score": 75.0, "note": "略微下调"},
                ],
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["decision"], "borderline")
        self.assertEqual(body["report_id"], self.report_id)
        self.assertEqual(len(body["dimension_overrides"]), 1)

        # GET 也能拿回来
        r = self._hr_get(f"/hr/reports/{self.report_id}/review")
        self.assertEqual(r.status_code, 200)
        loaded = r.json()
        self.assertEqual(loaded["decision"], "borderline")

    def test_invalid_decision_422(self):
        r = self._hr_patch(
            f"/hr/reports/{self.report_id}/review",
            {"decision": "fire-the-cannon"},
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("decision", r.json()["detail"])

    def test_review_for_unknown_report_404(self):
        r = self._hr_patch(
            "/hr/reports/no-such/review", {"decision": "recommend"},
        )
        self.assertEqual(r.status_code, 404)

    def test_reviewer_id_taken_from_jwt(self):
        from scripts.seed_users import seed_user
        seed_user(username="hr2", password="pw", role="hr")
        token2 = self._login("hr2", "pw")
        r = self.client.patch(
            f"/hr/reports/{self.report_id}/review",
            json={"decision": "recommend"},
            headers={"Authorization": f"Bearer {token2}"},
        )
        self.assertEqual(r.status_code, 200)
        # reviewer_id 应当是 hr2 的 user_id, 不是 hr1
        from src.db import load_user_by_username
        hr2 = load_user_by_username("hr2")
        self.assertEqual(r.json()["reviewer_id"], hr2[0].user_id)

    def test_no_get_review_returns_null_before_first_patch(self):
        r = self._hr_get(f"/hr/reports/{self.report_id}/review")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json())


if __name__ == "__main__":
    unittest.main()
