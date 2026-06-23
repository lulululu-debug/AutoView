"""Evaluator RAG eval —— Sprint 3-7。

三件事:
1. ingest JD/公司资料后, evaluate 返的 report.rag_context_chunk_ids 非空,
   且 id 格式符合 ingestion 命名约定 ({job_id}:jd:... / {job_id}:cm:...)
2. 没 ingest 时 rag_context_chunk_ids 应当为空, summary 仍能产出 (fallback)
3. rag_context_chunk_ids 在 save_report -> load_report round-trip 中不丢

不依赖真 OpenAI: monkey-patch embed 让 search 必中。
"""
from __future__ import annotations

import os
import tempfile
import unittest

# Sprint 5.9 patch: swap to TEST_POSTGRES_URL 防 TRUNCATE 抹掉 dev DB.
from evals._test_db import swap_to_test_url  # noqa: E402
swap_to_test_url()


def _fixed_vector(dim: int = 1536) -> list[float]:
    v = [0.0] * dim
    v[0] = 1.0
    return v


class _EvaluatorRagBase(unittest.TestCase):
    """临时 Milvus DB; PG 重置只清 evaluation_reports 相关表。"""

    @classmethod
    def setUpClass(cls):
        cls._db = tempfile.mktemp(suffix=".db")
        os.environ.pop("MILVUS_URI", None)
        os.environ["MILVUS_LITE_URI"] = cls._db
        from src.vector_store import init_collections
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        init_collections()

    @classmethod
    def tearDownClass(cls):
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        for ext in ("", "-shm", "-wal", ".lock"):
            try:
                os.unlink(cls._db + ext)
            except OSError:
                pass

    def setUp(self):
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        from src.vector_store import drop_collections, init_collections
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        drop_collections()
        init_collections()

    def _patch_embed_to_fixed(self):
        """monkey-patch embed 让 ingest + evaluator RAG 都用固定向量。"""
        from src.agents import evaluator as eval_mod
        from src.ingestion import pipeline as ing_pipeline
        original_eval = eval_mod.embeddings.embed
        original_ing = ing_pipeline.embeddings.embed

        def _fake(text, **_kw):
            return _fixed_vector()

        eval_mod.embeddings.embed = _fake
        ing_pipeline.embeddings.embed = _fake

        def restore():
            eval_mod.embeddings.embed = original_eval
            ing_pipeline.embeddings.embed = original_ing
        return restore

    def _make_session_with_answers(self, plan):
        """构造一个已答完的 session, 让 evaluate 有内容可评。"""
        from src.schemas import (
            CandidateAnswer, InterviewSession, SessionStatus, Turn, TurnRole,
        )
        session = InterviewSession(
            plan_id=plan.plan_id,
            job_id=plan.job_id,
            status=SessionStatus.COMPLETED,
        )
        # 给每个 question 一条简短回答
        answers_text = [
            "去年大促订单 P99 从 800ms 到 2s, 我们加回失效索引并改本地+Redis 二级缓存, 结果回到 350ms。",
            "我会先用数据让对方理解风险, 比如拉线上回放, 再一起定义可灰度的中间方案。",
            "对账中台日处理 2 亿笔, 我们改成分桶并行+幂等键, 结果延迟从 30 分钟降到 3 分钟。",
            "风控那次产品要 24h 全量, 我担心误杀率, 拉 SRE 一起跑离线回放说服改灰度。",
        ]
        questions = [q for r in plan.rounds for q in r.questions]
        for q, txt in zip(questions, answers_text):
            session.history.append(
                Turn(role=TurnRole.INTERVIEWER, text=q.text, ref_id=q.question_id)
            )
            ans = CandidateAnswer(question_id=q.question_id, text=txt)
            session.answers.append(ans)
            session.history.append(
                Turn(role=TurnRole.CANDIDATE, text=txt, ref_id=ans.answer_id)
            )
        return session


class EvaluatorRagHitTests(_EvaluatorRagBase):
    """ingest JD + 公司资料后, report.rag_context_chunk_ids 应当带 id。"""

    def test_report_carries_rag_chunk_ids_when_ingested(self):
        from src.agents.evaluator import evaluate
        from src.agents.planner import plan
        from src.ingestion import ingest_company_material, ingest_jd
        from src.schemas import CandidateProfile, JobContext

        restore = self._patch_embed_to_fixed()
        try:
            job = JobContext(
                title="后端工程师",
                jd="负责交易系统稳定性, 需要分布式与数据库优化经验。" * 20,
                company_materials="一家以交易系统为核心的公司, 强调稳定性。" * 20,
                role_family="backend",
            )
            ingest_jd(job.job_id, job.jd)
            ingest_company_material(job.job_id, job.company_materials)

            cand = CandidateProfile(resume="r", projects=[])
            p = plan(job, cand)
            session = self._make_session_with_answers(p)

            report = evaluate(session, p)
        finally:
            restore()

        self.assertGreater(
            len(report.rag_context_chunk_ids), 0,
            "ingest 完后 evaluator 应该召回到 JD/公司资料 chunks",
        )
        # 至少一个 id 应该来自 JD 或公司资料 (不应该是 resume)
        for cid in report.rag_context_chunk_ids:
            self.assertTrue(
                ":jd:" in cid or ":cm:" in cid,
                f"chunk_id 应来自 JD 或公司资料, 实际: {cid}",
            )
            self.assertTrue(
                cid.startswith(job.job_id),
                f"chunk_id 应当属于本 job, 实际: {cid}",
            )

    def test_compliance_overall_not_affected_by_rag(self):
        """合规护栏: rag 召回不应影响 overall 分数, 只能影响 summary。"""
        from src.agents.evaluator import evaluate
        from src.agents.planner import plan
        from src.ingestion import ingest_jd
        from src.schemas import CandidateProfile, JobContext

        restore = self._patch_embed_to_fixed()
        try:
            job = JobContext(
                title="t", jd="x" * 1000, role_family="backend",
            )
            cand = CandidateProfile(resume="r", projects=[])
            p = plan(job, cand)
            session = self._make_session_with_answers(p)

            # 不灌 JD: 评 1 次
            report_no_rag = evaluate(session, p)

            # 灌 JD: 评 2 次
            ingest_jd(job.job_id, job.jd)
            report_with_rag = evaluate(session, p)
        finally:
            restore()

        self.assertEqual(
            report_no_rag.overall, report_with_rag.overall,
            "overall 不应被 RAG 召回影响 (只能受 content_scores 加权决定)",
        )


class EvaluatorRagMissTests(_EvaluatorRagBase):
    """没 ingest 时, rag_context_chunk_ids 应当为空, 但 summary 仍要出。"""

    def test_no_ingest_chunk_ids_empty(self):
        from src.agents.evaluator import evaluate
        from src.agents.planner import plan
        from src.schemas import CandidateProfile, JobContext

        restore = self._patch_embed_to_fixed()
        try:
            job = JobContext(title="t", jd="x", role_family="backend")
            cand = CandidateProfile(resume="r", projects=[])
            p = plan(job, cand)
            session = self._make_session_with_answers(p)
            report = evaluate(session, p)
        finally:
            restore()

        self.assertEqual(report.rag_context_chunk_ids, [])
        self.assertTrue(report.summary.strip(), "无 RAG 时也应有 fallback summary")

    def test_other_jobs_chunks_not_pulled_in(self):
        """source_id 过滤: job-A 的 JD 不能被 job-B 的 evaluation 召回。"""
        from src.agents.evaluator import evaluate
        from src.agents.planner import plan
        from src.ingestion import ingest_jd
        from src.schemas import CandidateProfile, JobContext

        restore = self._patch_embed_to_fixed()
        try:
            # job A 灌 JD
            ingest_jd("job-A", "完全不相关的内容。" * 50)

            # job B 评估 (没有自己的 JD ingest)
            job_b = JobContext(
                job_id="job-B", title="t", jd="x", role_family="backend",
            )
            cand = CandidateProfile(resume="r", projects=[])
            p = plan(job_b, cand)
            session = self._make_session_with_answers(p)
            report = evaluate(session, p)
        finally:
            restore()

        # job-A 的 chunks 不应当出现
        for cid in report.rag_context_chunk_ids:
            self.assertFalse(
                cid.startswith("job-A"),
                f"不应召回到其他 job 的 chunks: {cid}",
            )


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL"),
    "需要 POSTGRES_URL 验证 rag_context_chunk_ids 的 PG 持久化",
)
class EvaluatorRagPersistenceTests(_EvaluatorRagBase):
    """rag_context_chunk_ids 通过 save_report / load_report round-trip 不丢。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 触发增量迁移把 rag_context_chunk_ids 列加上 (老 dev DB 没这列)
        from src.db import init_db
        init_db()

    def test_round_trip_preserves_chunk_ids(self):
        from src.db import load_report, save_report, save_session
        from src.schemas import (
            DimensionScore, EvaluationReport, InterviewSession, SessionStatus,
        )
        sess = InterviewSession(
            plan_id="plan-rag", job_id="job-rag",
            status=SessionStatus.COMPLETED,
        )
        save_session(sess)

        rpt = EvaluationReport(
            session_id=sess.session_id,
            content_scores=[
                DimensionScore(competency_id="c1", score=80.0, evidence=["ev"])
            ],
            overall=80.0,
            summary="...",
            rag_context_chunk_ids=["job-rag:jd:0", "job-rag:cm:1"],
        )
        save_report(rpt)

        loaded = load_report(rpt.report_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(
            loaded.rag_context_chunk_ids,
            ["job-rag:jd:0", "job-rag:cm:1"],
        )


if __name__ == "__main__":
    unittest.main()
