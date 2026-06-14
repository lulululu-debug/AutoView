"""Sprint 5.6 Assessor 端到端集成护栏。

护栏对象:
- ASSESSOR_ENABLED gate: false 时 session.assessments 恒空, 走 Sprint 0 启发式
  (与 5.5 行为完全一致); true 时每答一题就追加一条 assessment。
- self_intro 即使 Assessor 给低 sufficiency 也不追问 (FollowUpPolicy max=0 + 类别
  二次保护)。
- FollowUpPolicy.for_stage 表正确 (self_intro=0 / knowledge=1 / project=2 / scenario=2)。
- _decide_followup 在 assessment 给高 sufficiency + 高 confidence 时跳过追问;
  低 sufficiency 时触发追问。
- 启发式 fallback 双路径不可拆: 即使 Assessor 完全挂, Interviewer 依然能决策。
"""
from __future__ import annotations

import os
import unittest

# 让 .env (POSTGRES_URL / REDIS_URL) 在 skipUnless 评估之前就被读到,
# 否则 AssessorGateTests 永远跳过。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 模块级 default: 不启用 (与 Sprint 5.5 行为一致); 各 TestCase 自己按需 patch env。
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ASSESSOR_ENABLED", None)

from unittest.mock import patch  # noqa: E402

from src.agents import interviewer  # noqa: E402
from src.agents.interviewer import _decide_followup  # noqa: E402
from src.schemas import (  # noqa: E402
    AnswerAssessment,
    CandidateAnswer,
    FollowUpPolicy,
    InterviewStage,
    Question,
    QuestionCategory,
)


class FollowUpPolicyDefaultsTests(unittest.TestCase):
    """stage 默认配额表锁定 (sprint 5.6 spec)。"""

    def test_self_intro_max_zero(self):
        p = FollowUpPolicy.for_stage(InterviewStage.SELF_INTRO)
        self.assertEqual(p.max_followups_per_question, 0)

    def test_knowledge_max_one(self):
        p = FollowUpPolicy.for_stage(InterviewStage.KNOWLEDGE)
        self.assertEqual(p.max_followups_per_question, 1)

    def test_project_max_two(self):
        p = FollowUpPolicy.for_stage(InterviewStage.PROJECT)
        self.assertEqual(p.max_followups_per_question, 2)

    def test_scenario_max_two(self):
        p = FollowUpPolicy.for_stage(InterviewStage.SCENARIO)
        self.assertEqual(p.max_followups_per_question, 2)


class DecideFollowupTests(unittest.TestCase):
    """_decide_followup 是 Interviewer 的决策核心, 单独单元测试。"""

    def setUp(self):
        self.q_know = Question(
            competency_id="c1", text="缓存怎么做?", category=QuestionCategory.KNOWLEDGE,
        )
        self.q_intro = Question(
            text="请自我介绍", category=QuestionCategory.SELF_INTRO,
        )
        self.ans = CandidateAnswer(
            question_id=self.q_know.question_id, text="比如 Redis", # 短但含 hint
        )
        self.policy_know = FollowUpPolicy.for_stage(InterviewStage.KNOWLEDGE)
        self.policy_intro = FollowUpPolicy.for_stage(InterviewStage.SELF_INTRO)

    def _assessment(self, sufficiency: float, confidence: float) -> AnswerAssessment:
        return AnswerAssessment(
            question_id=self.q_know.question_id,
            sufficiency=sufficiency, confidence=confidence,
        )

    def test_high_sufficiency_high_confidence_stops(self):
        a = self._assessment(0.85, 0.8)
        self.assertFalse(_decide_followup(
            self.q_know, self.ans, a, self.policy_know, followups_since=0,
        ))

    def test_low_sufficiency_triggers(self):
        a = self._assessment(0.3, 0.8)
        self.assertTrue(_decide_followup(
            self.q_know, self.ans, a, self.policy_know, followups_since=0,
        ))

    def test_high_sufficiency_low_confidence_triggers(self):
        """sufficiency 够但 confidence 不够仍追问 —— 双阈值都得过。"""
        a = self._assessment(0.85, 0.3)
        self.assertTrue(_decide_followup(
            self.q_know, self.ans, a, self.policy_know, followups_since=0,
        ))

    def test_max_followups_hard_cap(self):
        a = self._assessment(0.1, 0.9)  # 很不充分
        self.assertFalse(_decide_followup(
            self.q_know, self.ans, a, self.policy_know, followups_since=1,
        ), "knowledge max=1, 已追问 1 次后即使 sufficiency 极低也停")

    def test_self_intro_never_followup_even_with_low_sufficiency(self):
        """self_intro 双保险: policy.max=0 + 类别硬豁免。"""
        a = AnswerAssessment(
            question_id=self.q_intro.question_id,
            sufficiency=0.1, confidence=0.9,  # 极低 sufficiency
        )
        self.assertFalse(_decide_followup(
            self.q_intro, self.ans, a, self.policy_intro, followups_since=0,
        ))

    def test_no_assessment_falls_back_to_heuristic(self):
        """assessment=None 时退到 Sprint 0 _needs_followup —— 双路径共存。"""
        short_ans = CandidateAnswer(
            question_id=self.q_know.question_id, text="加个 Redis",  # <60 字
        )
        self.assertTrue(_decide_followup(
            self.q_know, short_ans, None, self.policy_know, followups_since=0,
        ), "无 assessment 时短答应触发启发式追问")

        long_hint_ans = CandidateAnswer(
            question_id=self.q_know.question_id,
            text=(
                "比如订单系统我们用 Redis 二级缓存 + 本地热点缓存, 结果 P99 从 800ms "
                "降到 350ms, 我们走 write-through 一致性, 用了 Cache Aside 兜底。"
            ),
        )
        self.assertFalse(_decide_followup(
            self.q_know, long_hint_ans, None, self.policy_know, followups_since=0,
        ), "无 assessment 时长答+hint 应跳过启发式追问")


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL 跑 orchestrator 集成",
)
class AssessorGateTests(unittest.TestCase):
    """ASSESSOR_ENABLED 开关行为 —— production gate 决定 session.assessments 是否
    被填充, 不影响其他链路。"""

    @classmethod
    def setUpClass(cls):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        os.environ.pop("OPENAI_API_KEY", None)
        from src import db
        from src.agents import planner
        from src.schemas import CandidateProfile, JobContext, Track
        db.init_db()
        cls.job = JobContext(
            title="后端工程师", jd="负责核心交易系统", track=Track.LATERAL,
        )
        cls.candidate = CandidateProfile(
            job_id=cls.job.job_id,
            resume="张三 / 后端 / 4 年。订单 P99 优化。",
            projects=["订单优化"],
        )
        cls.plan = planner.plan(cls.job, cls.candidate)
        db.save_job(cls.job)
        db.save_candidate(cls.candidate)
        db.save_plan(cls.plan, cls.candidate.candidate_id)
        cls._planner = planner

    def setUp(self):
        # pymilvus.settings.load_dotenv 在 import vector_store 时把 OPENAI_API_KEY
        # 重新注入, 必须在每个 test 入口再 pop 一次, 否则 Assessor 走真 LLM 路径
        # 不可控 (LLM 输出 sufficiency 不稳定, 也烧 token)。
        # CLAUDE.md 第 "坑提醒" 节有说明。
        os.environ.pop("OPENAI_API_KEY", None)

    def _make_session_and_answer(self, intro_text: str) -> str:
        from src.orchestrator import start_session, submit_answer
        # 每个 test 创建独立 session 避免污染
        result = start_session(self.job, self.candidate, plan=self.plan)
        sid = result.session_id
        submit_answer(sid, intro_text)
        return sid

    def test_gate_off_assessments_stay_empty(self):
        """ASSESSOR_ENABLED 未设置 (默认) 时, session.assessments 始终空。"""
        from src import cache
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASSESSOR_ENABLED", None)
            sid = self._make_session_and_answer(
                "我叫张三, 比如最近做订单优化, 我们结果 P99 从 800ms 降到 350ms。",
            )
            session = cache.load_session(sid)
            self.assertEqual(
                session.assessments, [],
                "gate off 时 session.assessments 应保持空",
            )

    def test_gate_on_assessments_appended(self):
        """ASSESSOR_ENABLED=true 时, 每答一题 session.assessments 加一条。"""
        from src import cache
        with patch.dict(os.environ, {"ASSESSOR_ENABLED": "true"}):
            sid = self._make_session_and_answer(
                "我叫张三, 比如最近做订单优化, 我们结果 P99 从 800ms 降到 350ms。",
            )
            session = cache.load_session(sid)
            self.assertEqual(
                len(session.assessments), 1,
                f"gate on 时答 1 题应有 1 条 assessment, 实际: {len(session.assessments)}",
            )
            a = session.assessments[0]
            # self_intro 题, 启发式 fallback 给 floor 0.9
            self.assertGreaterEqual(a.sufficiency, 0.9)

    def test_gate_on_self_intro_still_no_followup(self):
        """即使 Assessor 启用, self_intro 仍不被追问 —— 双保险生效。
        验证下一题是 project 第一题, 而不是 followup。"""
        from src import cache
        with patch.dict(os.environ, {"ASSESSOR_ENABLED": "true"}):
            from src.orchestrator import start_session, submit_answer
            result = start_session(self.job, self.candidate, plan=self.plan)
            sid = result.session_id
            # self_intro 答得极短 + 无 hint, 故意挑衅启发式 fallback
            result = submit_answer(sid, "我是张三")
        plan = cache.load_plan(self.plan.plan_id)
        project_round = next(r for r in plan.rounds if r.stage.value == "project")
        expected = project_round.questions[0]
        self.assertEqual(
            result.ref_id, expected.question_id,
            "self_intro 不应触发 followup, 下一题应是 project 第一题",
        )


if __name__ == "__main__":
    unittest.main()
