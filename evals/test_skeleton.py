"""第一个 eval —— 骨架结构性护栏 + 合规分区不变量。

定位:
- 这不是对 prompt 输出做语义打分(那要等 Sprint 3+ 接 RAG 后才有意义)。
- 这是结构性护栏: 改了 prompt 模板 / 改了 Planner 规则 / 不小心让软信号
  污染 overall —— 这里挂了, 说明动了 ARCHITECTURE.md / CLAUDE.md 里写过的契约。
- 用 stdlib unittest, 不引第三方依赖。

跑法:
    python -m unittest evals.test_skeleton                            # 全部
    python -m unittest discover -s evals                              # discover
    python -m unittest evals.test_skeleton.ComplianceInvariantTests   # 单类
- 不需要 PG/Redis 的 5 个 TestCase 永远会跑。
- 端到端那个 TestCase 在 POSTGRES_URL + REDIS_URL 都设置时才跑, 否则 skip。

强制 stub: 模块加载时清掉 OPENAI_API_KEY, 避免本 eval 把真实 API 打飞了。
本 eval 检的是「结构」, 不是「LLM 输出语义」, 走 stub 已经足够。

注: 本 eval 不 import pymilvus, 所以模块顶 pop 有效 (不会被 pymilvus 的
load_dotenv() 重新加回来)。
"""
from __future__ import annotations

import os
import unittest

# Sprint 5.9 patch: swap to TEST_POSTGRES_URL 防 TRUNCATE 抹掉 dev DB.
from evals._test_db import swap_to_test_url  # noqa: E402
swap_to_test_url()

# 强制 stub 模式: 在 import agent 之前清 key, 防止 evaluator/planner 真的打 API。
os.environ.pop("OPENAI_API_KEY", None)

from src.agents import analyzer, evaluator, planner  # noqa: E402
from src.schemas import (  # noqa: E402
    CandidateAnswer,
    CandidateProfile,
    EvaluationReport,
    InterviewSession,
    InterviewStage,
    JobContext,
    QuestionCategory,
    SessionStatus,
    Signal,
    SignalKind,
    Track,
    Turn,
    TurnRole,
)

# ---------- 固定输入 ----------
# track=lateral 让 plan 出 7 题, 顺序: self_intro 1 + project 3 + scenario 2 + knowledge 1
# 选 lateral 是因为 stub 路径下 fallback 文本可控, 且 knowledge 只 1 道好凑答案。
_JOB = JobContext(
    title="后端工程师",
    jd="负责核心交易系统的稳定性与性能, 熟悉分布式与数据库优化。",
    requirements=["分布式系统", "数据库优化", "沟通协作"],
    track=Track.LATERAL,
)
_CANDIDATE = CandidateProfile(
    resume="张三 / 后端 / 4 年。订单 P99 优化 (800ms->350ms); 对账中台从 0 到 1。",
    projects=["订单 P99 优化", "对账中台从 0 到 1"],
)
# 按 stage 顺序固定答案: self_intro -> project*3 -> scenario*2 -> knowledge*1
_HINTED_ANS = (
    "比如这块我们当时的方案是 {topic}, 结果指标从 {a} 优化到 {b}, "
    "用了 {tool} 配合, 上线后 P99 / 漏对率 / 失败率都按预期下来了。"
)


def _ans(topic: str, a: str, b: str, tool: str) -> str:
    """组装一个 >60 字 + 含 hint (比如/我们/结果/%) 的 fixture 答案。"""
    return _HINTED_ANS.format(topic=topic, a=a, b=b, tool=tool)


# Sprint 5.9: tech-lateral 升到 22 主问题, 答案池: 1 self_intro + 11 project +
# 6 scenario + 4 knowledge。每条 >60 字 + 含 specificity hint 防误触发追问。
_ANSWERS_BY_STAGE: dict[InterviewStage, list[str]] = {
    InterviewStage.SELF_INTRO: [
        "我是张三, 后端 4 年, 最近在订单和对账中台上做高可用 + 性能, "
        "最大挑战是 P99 抖动定位, 结果把 P99 从 800ms 降到 350ms。",
    ],
    InterviewStage.PROJECT: [
        _ans("订单 P99 优化",      "800ms",   "350ms",  "本地缓存 + Redis 二级缓存"),
        _ans("对账中台从 0 到 1",  "30 分钟",  "3 分钟",  "分桶并行 + 幂等键 + Kafka 回放"),
        _ans("跨职能推方案",      "争议反复",  "灰度通过", "数据回放 + 5% 灰度"),
        _ans("订单写入热点",      "QPS 800",  "QPS 12k", "本地 token 桶 + Redis 滑动窗口"),
        _ans("支付链路降级",      "可用 92%", "可用 99.9%", "熔断 + fallback to 同步对账"),
        _ans("数据库扩容",        "单实例 80%","集群 30%", "分库分表 + 在线迁移"),
        _ans("索引优化",          "scan 200ms","seek 5ms", "复合索引 + EXPLAIN 复盘"),
        _ans("消息积压回放",      "积压 800万","清空 30 分钟", "横扩 + 幂等键"),
        _ans("配置推送",          "回滚 5 分钟","回滚 30s", "灰度发布 + 立刻可回滚"),
        _ans("oncall 流程优化",   "MTTR 60 分钟", "MTTR 15 分钟", "runbook + alert 收敛"),
        _ans("跨团队复盘协调",    "推动 2 周",  "1 天闭环",  "结构化文档 + 行动项 owner"),
    ],
    InterviewStage.SCENARIO: [
        "前 5 分钟先看链路 RT 和错误率分布, 比如哪个下游慢, 是不是热点 key,"
        "再决定是先扩容还是先限流, 我会优先选不破坏可观测性的动作。",
        "我会先在群里说我已经定位到根因 + ETA 15 分钟,"
        "再 1on1 跟业务 PM 同步具体影响范围, 让他对外口径统一。",
        _ans("oncall 半夜告警",   "凌晨 3 点",  "30 分钟止血", "降级 + 限流 + 回滚"),
        _ans("DB 磁盘满 5%",       "5% 剩余",   "30% 释放",  "停批 + 在线扩容"),
        _ans("灰度发布失败",      "5% 错误飙升","回滚",       "立刻 rollback + 复盘"),
        _ans("incident 沟通",     "三方追问",  "口径一致",   "结构化更新 + ETA"),
    ],
    InterviewStage.KNOWLEDGE: [
        "我对 CAP 的理解是: 实际工程里 P 是默认前提,"
        "所以选 C 还是 A 是业务取舍, 比如订单状态我们选 C (用强一致性的状态机)。",
        _ans("Redis 持久化",      "RDB",      "AOF + RDB", "混合持久化保证 RPO"),
        _ans("MySQL 事务隔离",    "RR 默认",  "RC + 业务幂等","按业务取舍"),
        _ans("分布式锁",          "DB 锁 100ms","Redis 锁 1ms", "Redis Redlock + 续期"),
    ],
}


def _answers_for_plan(plan) -> list[str]:
    """按 plan 的 stage 顺序拼出答案数组。
    每个 stage 取 _ANSWERS_BY_STAGE 里对应数量, 多 round 同 stage 时按序消费。
    Plan 的 stage 配比由 Planner 硬编码, 这里要求 stage 答案池 >= 实际题数,
    否则提示出问题(意味着 Planner 配比改了但 fixture 没跟)。"""
    pool = {k: list(v) for k, v in _ANSWERS_BY_STAGE.items()}
    answers: list[str] = []
    for r in plan.rounds:
        bucket = pool.get(r.stage, [])
        for _ in r.questions:
            if not bucket:
                raise AssertionError(
                    f"stage {r.stage} 答案池不够; 检查 _ANSWERS_BY_STAGE"
                )
            answers.append(bucket.pop(0))
    return answers


def _build_completed_session(plan):
    """按照 plan 的题目顺序, 用固定回答构造一个已答完的 session。
    需要 plan 的 lazy project 题已经 resolve (text 非空), 调用方负责。"""
    session = InterviewSession(
        plan_id=plan.plan_id,
        job_id=_JOB.job_id,
        status=SessionStatus.COMPLETED,
    )
    answers = _answers_for_plan(plan)
    questions = [q for r in plan.rounds for q in r.questions]
    for q, ans_text in zip(questions, answers):
        session.history.append(
            Turn(role=TurnRole.INTERVIEWER, text=q.text, ref_id=q.question_id)
        )
        ans = CandidateAnswer(question_id=q.question_id, text=ans_text)
        session.answers.append(ans)
        session.history.append(
            Turn(role=TurnRole.CANDIDATE, text=ans_text, ref_id=ans.answer_id)
        )
    return session


# ---------- Plan 结构 ----------

class SkeletonPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = planner.plan(_JOB, _CANDIDATE)

    def test_plan_carries_job_id(self):
        self.assertEqual(self.plan.job_id, _JOB.job_id)

    def test_plan_shape_stage_sequence(self):
        """Sprint 5.9: tech-lateral 出 4 stage 序列 + 22 主问题。
        顺序: self_intro -> project -> scenario -> knowledge。"""
        self.assertEqual(len(self.plan.rounds), 4, "lateral 4 stages")
        stages = [r.stage for r in self.plan.rounds]
        self.assertEqual(stages, [
            InterviewStage.SELF_INTRO,
            InterviewStage.PROJECT,
            InterviewStage.SCENARIO,
            InterviewStage.KNOWLEDGE,
        ])
        # plan.competencies 顶层权威 (跨 stage 共享 + 去重)
        self.assertEqual(len(self.plan.competencies), 2,
                         "顶层 competencies: 技术深度 + 沟通协作")
        total = sum(len(r.questions) for r in self.plan.rounds)
        self.assertEqual(total, 22, "tech-lateral 配比 1+11+6+4")

    def test_question_category_distribution(self):
        """Sprint 5.9: 4 类题都出现, 数量与 tech-lateral 配比一致。"""
        questions = [q for r in self.plan.rounds for q in r.questions]
        from collections import Counter
        by_cat = Counter(q.category for q in questions)
        self.assertEqual(by_cat[QuestionCategory.SELF_INTRO], 1)
        self.assertEqual(by_cat[QuestionCategory.PROJECT_EXPERIENCE], 11)
        self.assertEqual(by_cat[QuestionCategory.SCENARIO], 6)
        self.assertEqual(by_cat[QuestionCategory.KNOWLEDGE], 4)

    def test_each_question_links_to_a_competency(self):
        """非 self_intro 题挂某个顶层 competency; self_intro 题 competency_id=None。"""
        comp_ids = {c.competency_id for c in self.plan.competencies}
        for q in [qq for r in self.plan.rounds for qq in r.questions]:
            if q.category is QuestionCategory.SELF_INTRO:
                self.assertIsNone(
                    q.competency_id,
                    "self_intro 题不挂 competency (合规: 不进 content_scores)",
                )
            else:
                self.assertIn(
                    q.competency_id, comp_ids,
                    f"Question {q.question_id} 关联了未知 competency",
                )

    def test_lazy_marker_only_on_project_stage(self):
        """Sprint 5.5: lazy=True 仅出现在 project stage; 其他 stage 一律 lazy=False。"""
        for r in self.plan.rounds:
            for q in r.questions:
                if r.stage is InterviewStage.PROJECT:
                    self.assertTrue(q.lazy, "project 题都是 lazy 占位")
                else:
                    self.assertFalse(q.lazy, f"{r.stage} 题不应是 lazy")


# ---------- Report 结构 ----------

class SkeletonReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = planner.resolve_lazy_questions(
            planner.plan(_JOB, _CANDIDATE), _JOB, _CANDIDATE,
        )
        cls.session = _build_completed_session(cls.plan)
        cls.report = evaluator.evaluate(cls.session, cls.plan, signals=[])

    def test_report_is_evaluation_report(self):
        self.assertIsInstance(self.report, EvaluationReport)
        self.assertEqual(self.report.session_id, self.session.session_id)

    def test_content_scores_cover_every_competency(self):
        """Sprint 5.5: 走 plan.competencies 顶层 (跨 stage 共享去重后的权威列表)。"""
        comp_ids = {c.competency_id for c in self.plan.competencies}
        scored_ids = {s.competency_id for s in self.report.content_scores}
        self.assertEqual(comp_ids, scored_ids, "每个维度都应当有一个 content score")

    def test_overall_in_valid_range(self):
        self.assertGreaterEqual(self.report.overall, 0.0)
        self.assertLessEqual(self.report.overall, 100.0)

    def test_each_content_score_has_evidence(self):
        for s in self.report.content_scores:
            self.assertGreater(
                len(s.evidence), 0, f"competency {s.competency_id} 应有证据"
            )

    def test_needs_human_review_default_true(self):
        """合规约束: AI 报告默认需人工复核 (ARCHITECTURE.md §7)。"""
        self.assertTrue(self.report.needs_human_review)

    def test_no_performance_observations_when_no_signals(self):
        """骨架阶段 analyzer 是空占位, 不该有软信号; 即便日后 analyzer 真接入,
        没传信号时 performance_observations 也必须是空。"""
        self.assertEqual(self.report.performance_observations, [])

    def test_summary_non_empty(self):
        self.assertTrue(self.report.summary.strip(), "summary 不能为空")


# ---------- 合规分区不变量(最重要的护栏) ----------

class ComplianceInvariantTests(unittest.TestCase):
    """ARCHITECTURE.md §7: overall 只能由 content_scores 加权得出,
    多模态软信号永远不进 overall。这条挂了就是合规事故。"""

    @classmethod
    def setUpClass(cls):
        cls.plan = planner.resolve_lazy_questions(
            planner.plan(_JOB, _CANDIDATE), _JOB, _CANDIDATE,
        )
        cls.session = _build_completed_session(cls.plan)

    def test_overall_unchanged_by_signals(self):
        report_no = evaluator.evaluate(self.session, self.plan, signals=[])
        report_with = evaluator.evaluate(
            self.session, self.plan,
            signals=[
                Signal(kind=SignalKind.GAZE, value="眼神接触少", confidence=0.9, source="test"),
                Signal(kind=SignalKind.TONE, value="语气紧张",    confidence=0.8, source="test"),
                Signal(kind=SignalKind.LANGUAGE, value="语速偏快", confidence=0.7, source="test"),
            ],
        )
        self.assertEqual(
            report_no.overall, report_with.overall,
            "overall 不能被 performance 软信号改变 —— 合规护栏被破坏",
        )

    def test_signals_only_land_in_performance_observations(self):
        signals = [Signal(kind=SignalKind.GAZE, value="x", confidence=0.5, source="test")]
        report = evaluator.evaluate(self.session, self.plan, signals=signals)
        # 1 个软信号 -> 1 条 performance_observation
        self.assertEqual(len(report.performance_observations), 1)
        # content_scores 数量与 plan.competencies (顶层) 一致, 不被软信号污染
        self.assertEqual(
            len(report.content_scores), len(self.plan.competencies)
        )


# ---------- Analyzer 骨架阶段必须空 ----------

class AnalyzerSkeletonTest(unittest.TestCase):
    """骨架阶段 Analyzer 必须返回空。
    真正实现要等 Sprint 7, 那时还要带置信度 + 默认关闭 + 偏见审计 (ARCHITECTURE.md §7)。
    在那之前任何输出都是越权。"""

    def test_analyzer_returns_empty_list(self):
        session = InterviewSession(plan_id="p", job_id="j", status=SessionStatus.COMPLETED)
        self.assertEqual(analyzer.analyze(session), [])


# ---------- 端到端冒烟(需要 PG + Redis) ----------

@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL 才能跑 orchestrator 端到端",
)
class OrchestratorEndToEndTest(unittest.TestCase):
    """run_interview + finalize 走完一遍, 验证报告归档到 PG。
    与上面的纯函数 eval 互补: 一头是 agent 契约, 一头是 orchestrator 编排正确性。"""

    def test_run_interview_archives_report_to_postgres(self):
        from src.db import load_report
        from src.orchestrator import run_interview

        # 走 Planner -> resolve_lazy -> _answers_for_plan 拿到按 stage 顺序的答案
        pl = planner.resolve_lazy_questions(
            planner.plan(_JOB, _CANDIDATE), _JOB, _CANDIDATE,
        )
        answers = _answers_for_plan(pl)
        report = run_interview(_JOB, _CANDIDATE, answers)
        self.assertIsInstance(report, EvaluationReport)
        self.assertEqual(len(report.content_scores), 2)
        self.assertTrue(report.needs_human_review)

        from_pg = load_report(report.report_id)
        self.assertIsNotNone(from_pg, "finalize 后报告应在 Postgres 查到")
        self.assertEqual(from_pg.overall, report.overall)
        self.assertEqual(from_pg.session_id, report.session_id)


if __name__ == "__main__":
    unittest.main()
