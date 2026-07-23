"""Sprint 5.7 CompletionPolicy + competency_coverage 端到端护栏。

护栏对象:
- coverage 计算: max(sufficiency) over assessments per competency, self_intro
  不进任何 competency (competency_id=None), 老 plan 顶层空时返 {}
- Interviewer 提前 done: mandatory coverage 全达标 -> 返回 None (即使还有未答题)
- Interviewer 硬上限: 已答 >= max_total_questions -> 返回 None
- Evaluator evidence_insufficient: 任一 mandatory < min_coverage 时 summary
  前缀加 "证据不充分, 建议人工面谈" + needs_human_review=True
- 老 plan (plan.competencies 空) -> CompletionPolicy 短路, 不影响"题答完就 done"

跑法:
    python -m unittest evals.test_completion_policy
"""
from __future__ import annotations

import os
import unittest

# Sprint 5.9 patch: swap to TEST_POSTGRES_URL 防 TRUNCATE 抹掉 dev DB.
from evals._test_db import swap_to_test_url  # noqa: E402
swap_to_test_url()

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
os.environ.pop("OPENAI_API_KEY", None)

from src.coverage import (  # noqa: E402
    compute_coverage,
    insufficient_competencies,
    mandatory_coverage_met,
    total_questions_asked,
)
from src.schemas import (  # noqa: E402
    AnswerAssessment,
    CandidateAnswer,
    CandidateProfile,
    Competency,
    CompletionPolicy,
    EvaluationReport,
    FollowUpPolicy,
    InterviewPlan,
    InterviewRound,
    InterviewSession,
    InterviewStage,
    JobContext,
    ProfileAspect,
    Question,
    QuestionCategory,
    SessionStatus,
    Track,
    Turn,
    TurnRole,
)


class CoverageComputeTests(unittest.TestCase):
    """compute_coverage 单元 - 单独验, 不走 PG/Redis。"""

    def setUp(self):
        self.tech = Competency(name="技术深度", description="x")
        self.comm = Competency(name="沟通协作", description="x")
        self.q_t1 = Question(
            competency_id=self.tech.competency_id, text="Q-tech-1",
            category=QuestionCategory.KNOWLEDGE,
        )
        self.q_t2 = Question(
            competency_id=self.tech.competency_id, text="Q-tech-2",
            category=QuestionCategory.PROJECT_EXPERIENCE,
        )
        self.q_c1 = Question(
            competency_id=self.comm.competency_id, text="Q-comm-1",
            category=QuestionCategory.KNOWLEDGE,
        )
        self.q_intro = Question(
            text="intro", category=QuestionCategory.SELF_INTRO,
        )
        r = InterviewRound(
            index=0, title="t",
            competencies=[self.tech, self.comm],
            questions=[self.q_intro, self.q_t1, self.q_t2, self.q_c1],
        )
        self.plan = InterviewPlan(
            job_id="j", rounds=[r], competencies=[self.tech, self.comm],
        )
        self.session = InterviewSession(plan_id=self.plan.plan_id, job_id="j")

    def test_empty_session_zero_coverage(self):
        cov = compute_coverage(self.session, self.plan)
        self.assertEqual(cov, {
            self.tech.competency_id: 0.0,
            self.comm.competency_id: 0.0,
        })

    def test_max_aggregation(self):
        """同维度多 assessment 取 max。"""
        self.session.assessments = [
            AnswerAssessment(question_id=self.q_t1.question_id, sufficiency=0.3, confidence=0.5),
            AnswerAssessment(question_id=self.q_t2.question_id, sufficiency=0.9, confidence=0.7),
            AnswerAssessment(question_id=self.q_c1.question_id, sufficiency=0.5, confidence=0.5),
        ]
        cov = compute_coverage(self.session, self.plan)
        self.assertEqual(cov[self.tech.competency_id], 0.9)
        self.assertEqual(cov[self.comm.competency_id], 0.5)

    def test_self_intro_assessment_does_not_contribute(self):
        """self_intro 题 competency_id=None, 任何 sufficiency 都不进 coverage。"""
        self.session.assessments = [
            AnswerAssessment(
                question_id=self.q_intro.question_id,
                sufficiency=0.99, confidence=0.99,
            ),
        ]
        cov = compute_coverage(self.session, self.plan)
        self.assertEqual(cov[self.tech.competency_id], 0.0)
        self.assertEqual(cov[self.comm.competency_id], 0.0)

    def test_old_plan_empty_competencies_short_circuits(self):
        """plan.competencies 顶层空 -> 返 {} 短路, 让上游退化到旧行为。"""
        r = InterviewRound(
            index=0, title="t",
            competencies=[self.tech], questions=[self.q_t1],
        )
        plan_old = InterviewPlan(job_id="j", rounds=[r])  # 顶层 competencies 空
        sess = InterviewSession(plan_id=plan_old.plan_id, job_id="j", assessments=[
            AnswerAssessment(question_id=self.q_t1.question_id, sufficiency=0.95, confidence=0.9),
        ])
        self.assertEqual(compute_coverage(sess, plan_old), {})
        # mandatory_coverage_met 老 plan 永远返 False, 让 Interviewer 不早停
        self.assertFalse(mandatory_coverage_met({}, CompletionPolicy(), plan_old))
        self.assertEqual(
            insufficient_competencies({}, CompletionPolicy(), plan_old), [],
            "老 plan 不参与 evidence_insufficient 判定",
        )

    def test_mandatory_met_requires_all_competencies(self):
        """空 mandatory 列表 = 全 plan.competencies 都 mandatory。"""
        cov = {self.tech.competency_id: 0.85, self.comm.competency_id: 0.4}
        pol = CompletionPolicy(min_competency_coverage=0.7)
        self.assertFalse(
            mandatory_coverage_met(cov, pol, self.plan),
            "comm=0.4 未达 0.7 不应通过",
        )
        self.assertEqual(
            insufficient_competencies(cov, pol, self.plan),
            [self.comm.competency_id],
        )

        cov2 = {self.tech.competency_id: 0.85, self.comm.competency_id: 0.75}
        self.assertTrue(mandatory_coverage_met(cov2, pol, self.plan))
        self.assertEqual(insufficient_competencies(cov2, pol, self.plan), [])

    def test_mandatory_subset_only(self):
        """policy.mandatory_competencies 非空时只看列出的; 其他维度低也无所谓。"""
        cov = {self.tech.competency_id: 0.85, self.comm.competency_id: 0.4}
        pol = CompletionPolicy(
            min_competency_coverage=0.7,
            mandatory_competencies=[self.tech.competency_id],
        )
        self.assertTrue(
            mandatory_coverage_met(cov, pol, self.plan),
            "只检查 tech, tech=0.85 通过即可",
        )


class InterviewerCompletionPolicyTests(unittest.TestCase):
    """Interviewer.next_turn 跟 CompletionPolicy 的端到端: 在 plan 内驱动 session。"""

    def setUp(self):
        # F9: pymilvus load_dotenv 会把 key 塞回, setUp 必须再 pop
        os.environ.pop("OPENAI_API_KEY", None)
        from src.agents import planner
        self.job = JobContext(
            title="x", jd="x", track=Track.LATERAL,
        )
        self.candidate = CandidateProfile(
            job_id=self.job.job_id, resume="x", projects=[],
        )
        # F9: import planner 会触发 pymilvus load_dotenv 把 key 塞回,
        # pop 必须紧贴在调用前 (import 之后)
        os.environ.pop("OPENAI_API_KEY", None)
        self.plan = planner.plan(self.job, self.candidate)
        self.session = InterviewSession(
            plan_id=self.plan.plan_id, job_id=self.job.job_id,
            status=SessionStatus.IN_PROGRESS,
        )

    def _ask_all_with_high_sufficiency(self) -> None:
        """模拟全 sufficient 走完所有 plan 题, 每题 append turn + answer + assessment。
        不走真实 Interviewer.next_turn (避免 lazy resolve 等副作用), 只直接构造。"""
        all_qs = [q for r in self.plan.rounds for q in r.questions]
        for q in all_qs:
            self.session.history.append(
                Turn(role=TurnRole.INTERVIEWER, text=q.text or "x", ref_id=q.question_id),
            )
            ans = CandidateAnswer(question_id=q.question_id, text="比如我们结果...")
            self.session.answers.append(ans)
            self.session.history.append(
                Turn(role=TurnRole.CANDIDATE, text=ans.text, ref_id=ans.answer_id),
            )
            if q.competency_id is not None:
                self.session.assessments.append(AnswerAssessment(
                    question_id=q.question_id, sufficiency=0.95, confidence=0.9,
                ))

    def test_full_sufficient_walk_done_with_full_coverage(self):
        from src.agents import interviewer
        self._ask_all_with_high_sufficiency()
        # next_turn 应当返回 None (题答完 + coverage 达标 -> done)
        result = interviewer.next_turn(self.session, self.plan, job=self.job)
        self.assertIsNone(result, "全 sufficient 答完应 done")
        # coverage 应当全维度满足
        cov = compute_coverage(self.session, self.plan)
        for cid in (c.competency_id for c in self.plan.competencies):
            self.assertGreaterEqual(cov[cid], 0.7)

    def test_max_total_questions_hard_cap(self):
        """硬上限封顶: 即使 coverage 不达, 已答 >= max 直接 done。"""
        from src.agents import interviewer
        # 构造一个题 + 答 + 不充分 assessment 重复 N 次让 answers 数超 max=3
        q = [q for r in self.plan.rounds for q in r.questions][0]
        self.session.history.append(
            Turn(role=TurnRole.INTERVIEWER, text=q.text or "x", ref_id=q.question_id),
        )
        for _ in range(5):
            ans = CandidateAnswer(question_id=q.question_id, text="短答")
            self.session.answers.append(ans)
            self.session.history.append(
                Turn(role=TurnRole.CANDIDATE, text=ans.text, ref_id=ans.answer_id),
            )
        job_capped = self.job.model_copy(
            update={"completion_policy": CompletionPolicy(max_total_questions=3)},
        )
        # 已答 5 > 3, 硬上限触发 done (next_turn 走"题答完" 之前的硬上限分支)
        result = interviewer.next_turn(self.session, self.plan, job=job_capped)
        self.assertIsNone(result, "超过 max_total_questions 应 done")

    def test_old_plan_completion_policy_short_circuits(self):
        """老 plan (顶层 competencies 空) -> CompletionPolicy 短路, 走完所有题再 done。"""
        from src.agents import interviewer
        comp = Competency(name="x", description="x")
        q1 = Question(competency_id=comp.competency_id, text="Q1")
        q2 = Question(competency_id=comp.competency_id, text="Q2")
        r = InterviewRound(index=0, title="t", competencies=[comp], questions=[q1, q2])
        plan_old = InterviewPlan(job_id=self.job.job_id, rounds=[r])  # 顶层 empty
        sess = InterviewSession(
            plan_id=plan_old.plan_id, job_id=self.job.job_id,
            status=SessionStatus.IN_PROGRESS,
        )
        sess.history.append(Turn(role=TurnRole.INTERVIEWER, text="Q1", ref_id=q1.question_id))
        ans1 = CandidateAnswer(question_id=q1.question_id, text="比如我们结果分析: ...")
        sess.answers.append(ans1)
        sess.history.append(Turn(role=TurnRole.CANDIDATE, text=ans1.text, ref_id=ans1.answer_id))
        sess.assessments.append(AnswerAssessment(
            question_id=q1.question_id, sufficiency=0.95, confidence=0.95,
        ))
        # 即使 sufficiency 高 (coverage 短路返 {}, mandatory_coverage_met False),
        # 还有 q2 没答, 应继续返回 q2 而不是早停。
        result = interviewer.next_turn(sess, plan_old, job=self.job)
        self.assertEqual(result.question_id, q2.question_id,
                         "老 plan 应当继续问 q2, 不能因 sufficiency 高就早停")


class EvaluatorEvidenceInsufficientTests(unittest.TestCase):
    """Evaluator 在 coverage 不达标时把 summary 前缀加'证据不充分'。"""

    def setUp(self):
        # F9: pymilvus load_dotenv 会把 key 塞回, setUp 必须再 pop
        os.environ.pop("OPENAI_API_KEY", None)
        from src.agents import planner
        self.job = JobContext(title="x", jd="x", track=Track.LATERAL)
        self.candidate = CandidateProfile(
            job_id=self.job.job_id, resume="x", projects=[],
        )
        # F9: import planner 会触发 pymilvus load_dotenv 把 key 塞回,
        # pop 必须紧贴在调用前 (import 之后)
        os.environ.pop("OPENAI_API_KEY", None)
        self.plan = planner.plan(self.job, self.candidate)
        # 构造 session: 只对 tech 维度有高 assessment, comm 维度全空
        all_qs = [q for r in self.plan.rounds for q in r.questions]
        self.session = InterviewSession(
            plan_id=self.plan.plan_id, job_id=self.job.job_id,
            status=SessionStatus.COMPLETED,
        )
        tech_id = self.plan.competencies[0].competency_id
        for q in all_qs:
            self.session.history.append(
                Turn(role=TurnRole.INTERVIEWER, text=q.text or "x", ref_id=q.question_id),
            )
            ans = CandidateAnswer(question_id=q.question_id, text="x")
            self.session.answers.append(ans)
            self.session.history.append(
                Turn(role=TurnRole.CANDIDATE, text=ans.text, ref_id=ans.answer_id),
            )
            if q.competency_id == tech_id:
                self.session.assessments.append(AnswerAssessment(
                    question_id=q.question_id, sufficiency=0.95, confidence=0.9,
                ))

    def test_evidence_insufficient_when_some_competency_uncovered(self):
        from src.agents import evaluator
        report = evaluator.evaluate(self.session, self.plan, job=self.job)
        self.assertTrue(
            report.summary.startswith("证据不充分, 建议人工面谈:"),
            f"summary 应当以 evidence_insufficient 前缀开头, 实际: {report.summary[:60]}",
        )
        self.assertTrue(report.needs_human_review)
        self.assertIn(self.plan.competencies[1].competency_id, report.competency_coverage)
        # comm 维度 coverage 应当 = 0 (没有 assessment)
        self.assertEqual(
            report.competency_coverage[self.plan.competencies[1].competency_id], 0.0,
        )

    def test_no_warning_when_all_competencies_covered(self):
        from src.agents import evaluator
        # 给 comm 维度也加一个 high sufficiency
        comm_id = self.plan.competencies[1].competency_id
        comm_qs = [
            q for r in self.plan.rounds for q in r.questions
            if q.competency_id == comm_id
        ]
        self.session.assessments.append(AnswerAssessment(
            question_id=comm_qs[0].question_id, sufficiency=0.95, confidence=0.9,
        ))
        report = evaluator.evaluate(self.session, self.plan, job=self.job)
        self.assertFalse(
            report.summary.startswith("证据不充分"),
            f"两维度都达标, summary 不应加 evidence 警告, 实际: {report.summary[:60]}",
        )

    def test_competency_coverage_written_to_report(self):
        from src.agents import evaluator
        report = evaluator.evaluate(self.session, self.plan, job=self.job)
        # 两个 competency 都应当在 dict 里 (tech 高, comm 0)
        self.assertEqual(len(report.competency_coverage), 2)
        tech_id = self.plan.competencies[0].competency_id
        self.assertEqual(report.competency_coverage[tech_id], 0.95)


class RichnessUnitTests(unittest.TestCase):
    """Sprint 5.9: compute_richness 的 4 个 case + Interviewer 用 richness 提前停。"""

    def setUp(self):
        from src.coverage import compute_richness
        self._compute = compute_richness
        self.asps = [
            ProfileAspect(competency_id="c1", name=f"A{i}", description="d")
            for i in range(5)
        ]
        self.job = JobContext(
            title="t", jd="x", track=Track.LATERAL, aspects=self.asps,
        )
        self.sess = InterviewSession(plan_id="p", job_id=self.job.job_id)

    def test_richness_zero_when_no_assessments(self):
        self.assertEqual(self._compute(self.sess, self.job), 0.0)

    def test_richness_full_when_all_aspects_covered(self):
        all_ids = [a.aspect_id for a in self.asps]
        self.sess.assessments = [
            AnswerAssessment(
                question_id="q", sufficiency=0.5, confidence=0.5,
                covered_aspects=all_ids,
            )
        ]
        self.assertEqual(self._compute(self.sess, self.job), 1.0)

    def test_richness_unioned_across_assessments(self):
        """多回答覆盖的 aspect 取并集 (同一 aspect 不重复计数)。"""
        self.sess.assessments = [
            AnswerAssessment(
                question_id="q1", sufficiency=0.5, confidence=0.5,
                covered_aspects=[self.asps[0].aspect_id, self.asps[1].aspect_id],
            ),
            AnswerAssessment(
                question_id="q2", sufficiency=0.5, confidence=0.5,
                covered_aspects=[self.asps[1].aspect_id, self.asps[2].aspect_id],
            ),
        ]
        # 0,1,2 covered → 3/5 = 0.6
        self.assertAlmostEqual(self._compute(self.sess, self.job), 0.6)

    def test_richness_falls_back_to_default_aspects(self):
        """job.aspects 空 + default_aspects 给定 -> 走 default."""
        job_no_asp = JobContext(title="t", jd="x")
        self.sess.assessments = [
            AnswerAssessment(
                question_id="q", sufficiency=0.5, confidence=0.5,
                covered_aspects=[self.asps[0].aspect_id],
            ),
        ]
        r = self._compute(self.sess, job_no_asp, default_aspects=self.asps)
        self.assertAlmostEqual(r, 0.2)  # 1/5


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 PG + Redis 测 Interviewer 端到端",
)
class InterviewerRichnessStopTests(unittest.TestCase):
    """Sprint 5.9: Interviewer 答足 min_total + richness 达标 -> 提前 done。"""

    def setUp(self):
        # pymilvus dotenv 把 ASSESSOR 加回, eval 走启发式
        os.environ["ASSESSOR_ENABLED"] = "false"
        from src import db
        db.init_db()
        from src.agents import planner
        # 用最小 plan 让计数走 hard cap; 用 aspects 控制 richness 阈值
        self.asps = [
            ProfileAspect(competency_id="c1", name=f"A{i}", description="d")
            for i in range(4)
        ]
        self.job = JobContext(
            title="t", jd="x", track=Track.LATERAL, role_family="backend",
            aspects=self.asps,
            completion_policy=CompletionPolicy(
                min_total_questions=3,
                max_total_questions=30,
                min_profile_richness=0.5,
            ),
        )
        from src.schemas import CandidateProfile
        self.cand = CandidateProfile(
            job_id=self.job.job_id, resume="r", projects=[],
        )
        self.plan = planner.plan(self.job, self.cand)
        db.save_job(self.job)
        db.save_candidate(self.cand)
        db.save_plan(self.plan, self.cand.candidate_id)

    def test_richness_above_threshold_after_min_total_stops_early(self):
        """3 道回答 + 50% aspect coverage → Interviewer next_turn 返 None。"""
        from src.agents import interviewer
        # 手工构造 session.history + answers + assessments 模拟 3 turn 后状态
        all_qs = [q for r in self.plan.rounds for q in r.questions]
        # 第 0 题答完, assessment cover asp[0],[1] (50% richness)
        q = all_qs[0]
        self.sess = InterviewSession(
            plan_id=self.plan.plan_id, job_id=self.job.job_id,
            status=SessionStatus.IN_PROGRESS,
        )
        for i in range(3):
            q = all_qs[i]
            self.sess.history.append(Turn(
                role=TurnRole.INTERVIEWER, text=q.text or "x", ref_id=q.question_id,
            ))
            ans = CandidateAnswer(question_id=q.question_id, text="比如我们的方案: ...")
            self.sess.answers.append(ans)
            self.sess.history.append(Turn(
                role=TurnRole.CANDIDATE, text=ans.text, ref_id=ans.answer_id,
            ))
            # 第 0/1 题 cover asp[0]/asp[1] (50%); 第 2 题不 cover
            covered = [self.asps[i].aspect_id] if i < 2 else []
            self.sess.assessments.append(AnswerAssessment(
                question_id=q.question_id, sufficiency=0.9, confidence=0.9,
                covered_aspects=covered,
            ))
        # next_turn: asked=3 >= min_total=3, richness=0.5 >= min_richness=0.5
        # → 应 None (提前 done)
        result = interviewer.next_turn(self.sess, self.plan, job=self.job)
        self.assertIsNone(result, "richness 达标应当提前 done")

    def test_richness_below_threshold_continues(self):
        """3 道回答 + 25% aspect coverage (richness < 0.5) → 不提前停。"""
        from src.agents import interviewer
        all_qs = [q for r in self.plan.rounds for q in r.questions]
        self.sess = InterviewSession(
            plan_id=self.plan.plan_id, job_id=self.job.job_id,
            status=SessionStatus.IN_PROGRESS,
        )
        for i in range(3):
            q = all_qs[i]
            self.sess.history.append(Turn(
                role=TurnRole.INTERVIEWER, text=q.text or "x", ref_id=q.question_id,
            ))
            ans = CandidateAnswer(question_id=q.question_id, text="比如我们的方案: ...")
            self.sess.answers.append(ans)
            self.sess.history.append(Turn(
                role=TurnRole.CANDIDATE, text=ans.text, ref_id=ans.answer_id,
            ))
            # 只有第 0 题 cover asp[0] (25%)
            covered = [self.asps[0].aspect_id] if i == 0 else []
            self.sess.assessments.append(AnswerAssessment(
                question_id=q.question_id, sufficiency=0.9, confidence=0.9,
                covered_aspects=covered,
            ))
        result = interviewer.next_turn(self.sess, self.plan, job=self.job)
        # richness=0.25 < 0.5 → 不提前停, 应返下一题 (Question)
        self.assertIsNotNone(result)
        from src.schemas import Question as Q
        self.assertIsInstance(result, Q)


if __name__ == "__main__":
    unittest.main()
