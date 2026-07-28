"""Sprint 8.4 task 1 —— CandidateModel 纯规则更新护栏。零 token。

- strengths/concerns -> claimed/verified/doubted 映射 (分数线 0.6)
- 幂等: 同一 assessment 落两次不产生重复条目
- 合并: 同 competency 同方向高相似 -> evidence 并集 + claimed 升 verified
- 矛盾确定性: verified vs doubted 同主题 -> 双方 contradicted + 记对,
  同输入同输出; 保留双方 evidence
- 降级铁律: 合并逻辑内部异常 -> 退化 append-only 不炸
- orchestrator 接线 + 第四类数据落库 round-trip (PG-gated)

跑法: python -m unittest evals.test_candidate_model
"""
from __future__ import annotations

import os
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src import candidate_model as cm  # noqa: E402
from src.schemas import (  # noqa: E402
    AnswerAssessment,
    CandidateAnswer,
    CandidateModel,
    ClaimStatus,
    InterviewSession,
    Question,
    QuestionCategory,
)


def _q(cid: str = "c1") -> Question:
    return Question(
        competency_id=cid, text="讲讲你的分库分表实践。",
        category=QuestionCategory.PROJECT_EXPERIENCE,
    )


def _aa(q: Question, suf: float, strengths=(), concerns=()) -> AnswerAssessment:
    return AnswerAssessment(
        question_id=q.question_id, sufficiency=suf, confidence=0.7,
        strengths=list(strengths), concerns=list(concerns),
    )


def _ans(q: Question) -> CandidateAnswer:
    return CandidateAnswer(question_id=q.question_id, text="回答原文")


class IntegrateTests(unittest.TestCase):
    def test_status_mapping(self) -> None:
        q = _q()
        a = _ans(q)
        m = cm.integrate_assessment(
            CandidateModel(), q, a,
            _aa(q, 0.7, strengths=["掌握分库分表"], concerns=["缺量化数据"]),
        )
        by_status = {c.status for c in m.claims}
        self.assertEqual(
            by_status, {ClaimStatus.VERIFIED, ClaimStatus.DOUBTED},
        )
        self.assertTrue(all(c.evidence == [a.answer_id] for c in m.claims))
        self.assertTrue(all(c.source_stage == "project_experience" for c in m.claims))

    def test_low_score_strength_is_claimed(self) -> None:
        q = _q()
        m = cm.integrate_assessment(
            CandidateModel(), q, _ans(q), _aa(q, 0.4, strengths=["提到了缓存"]),
        )
        self.assertEqual(m.claims[0].status, ClaimStatus.CLAIMED)

    def test_idempotent(self) -> None:
        q = _q()
        a = _ans(q)
        aa = _aa(q, 0.7, strengths=["掌握分库分表"])
        m1 = cm.integrate_assessment(CandidateModel(), q, a, aa)
        m2 = cm.integrate_assessment(m1, q, a, aa)
        self.assertEqual(len(m2.claims), 1)
        self.assertEqual(m2.claims[0].evidence, [a.answer_id])

    def test_merge_evidence_and_upgrade(self) -> None:
        q = _q()
        a1, a2 = _ans(q), _ans(q)
        m = cm.integrate_assessment(
            CandidateModel(), q, a1, _aa(q, 0.4, strengths=["掌握分库分表"]),
        )
        m = cm.integrate_assessment(
            m, q, a2, _aa(q, 0.8, strengths=["掌握分库分表"]),
        )
        self.assertEqual(len(m.claims), 1)
        self.assertEqual(m.claims[0].status, ClaimStatus.VERIFIED)  # 升级
        self.assertEqual(m.claims[0].evidence, [a1.answer_id, a2.answer_id])

    def test_contradiction_deterministic(self) -> None:
        q = _q()
        a1, a2 = _ans(q), _ans(q)
        base = cm.integrate_assessment(
            CandidateModel(), q, a1, _aa(q, 0.8, strengths=["掌握分库分表"]),
        )
        out1 = cm.integrate_assessment(
            base, q, a2, _aa(q, 0.3, concerns=["分库分表经验存疑"]),
        )
        out2 = cm.integrate_assessment(
            base, q, a2, _aa(q, 0.3, concerns=["分库分表经验存疑"]),
        )
        self.assertEqual(len(out1.contradictions), 1)
        statuses = {c.status for c in out1.claims}
        self.assertEqual(statuses, {ClaimStatus.CONTRADICTED})
        # 双方 evidence 保留
        evs = {tuple(c.evidence) for c in out1.claims}
        self.assertEqual(evs, {(a1.answer_id,), (a2.answer_id,)})
        # 确定性: 同输入同输出 (id 除外)
        self.assertEqual(
            [(c.claim, c.status) for c in out1.claims],
            [(c.claim, c.status) for c in out2.claims],
        )

    def test_different_topic_no_contradiction(self) -> None:
        q = _q()
        m = cm.integrate_assessment(
            CandidateModel(), q, _ans(q), _aa(q, 0.8, strengths=["掌握分库分表"]),
        )
        m = cm.integrate_assessment(
            m, q, _ans(q), _aa(q, 0.3, concerns=["沟通表达偏弱"]),
        )
        self.assertEqual(m.contradictions, [])

    def test_degrade_to_append_only(self) -> None:
        q = _q()
        orig = cm.similarity
        cm.similarity = lambda a, b: 1 / 0  # 强制内部炸
        try:
            m = cm.integrate_assessment(
                CandidateModel(), q, _ans(q), _aa(q, 0.8, strengths=["x" * 30]),
            )
        finally:
            cm.similarity = orig
        self.assertEqual(len(m.claims), 1)  # 追加成功, 不抛

    def test_doubted_claims_helper(self) -> None:
        q = _q("c1")
        m = cm.integrate_assessment(
            CandidateModel(), q, _ans(q),
            _aa(q, 0.3, strengths=["会用 Redis"], concerns=["缺个人实践细节"]),
        )
        self.assertEqual(len(cm.doubted_claims(m)), 1)
        self.assertEqual(len(cm.doubted_claims(m, competency_id="c1")), 1)
        self.assertEqual(len(cm.doubted_claims(m, competency_id="c2")), 0)


class OrchestratorWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)  # pymilvus 回填 (F9)

    def test_session_default_and_old_json_compat(self) -> None:
        s = InterviewSession.model_validate({"plan_id": "p", "job_id": "j"})
        self.assertEqual(s.candidate_model.claims, [])
        s2 = InterviewSession.model_validate(
            {"plan_id": "p", "job_id": "j", "candidate_model": {}},
        )
        self.assertEqual(s2.candidate_model.contradictions, [])


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL"), "需要 TEST_POSTGRES_URL",
)
class PersistenceTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        from src import db
        from src.db import init_db
        init_db()
        q = _q()
        a = _ans(q)
        session = InterviewSession(plan_id="p-cm", job_id="j")
        session.candidate_model = cm.integrate_assessment(
            CandidateModel(), q, a, _aa(q, 0.8, strengths=["掌握分库分表"]),
        )
        db.save_session(session)
        loaded = db.load_session(session.session_id)
        self.assertEqual(len(loaded.candidate_model.claims), 1)
        self.assertEqual(loaded.candidate_model.claims[0].claim, "掌握分库分表")
        self.assertEqual(loaded.candidate_model.claims[0].evidence, [a.answer_id])


if __name__ == "__main__":
    unittest.main()
