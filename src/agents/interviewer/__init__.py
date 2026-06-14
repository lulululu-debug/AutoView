"""Interviewer Agent — 提问、依据回答决定是否追问。

骨架规则: 每个 Question 至多触发 N 次 FollowUp, N 由 FollowUpPolicy 决定。
追问触发: Sprint 5.6 起首选 AnswerAssessment (Assessor 输出) + FollowUpPolicy 阈值;
没有 assessment (ASSESSOR_ENABLED=false 或 assessor 完全挂) 时回退到 Sprint 0
的 "字数 + specificity hints" 启发式 _needs_followup —— 双路径永远共存。
追问文本由 LLM 生成, 不可用时回退到通用模板; 5.6 起拼 assessment.followup_goal
让追问聚焦缺失信号 (而不是泛泛"展开一个例子")。

Sprint 5.5 task 4: self_intro 永不追问 (在 _needs_followup 硬豁免);
Sprint 5.6: FollowUpPolicy.for_stage 给 self_intro=0 max 是双保险, 即使
assessment 误判 sufficiency 低也不会追自我介绍。

返回值约定:
- Question  -> 主问题
- FollowUp  -> 针对当前问题的追问
- None      -> 本次面试已无新内容
Orchestrator 负责将返回值写入 session.history, 并补上候选人答复。
"""
from __future__ import annotations

from src import llm
from src.schemas import (
    AnswerAssessment,
    CandidateAnswer,
    FollowUp,
    FollowUpPolicy,
    InterviewPlan,
    InterviewRound,
    InterviewSession,
    InterviewStage,
    Question,
    QuestionCategory,
    TurnRole,
)

_FOLLOWUP_SYSTEM = (
    "你是一名资深面试官。候选人对当前问题的回答不够具体或不够深入。"
    "请基于其回答, 写一句聚焦、可深挖的中文追问。只输出追问文本本身。"
)

_MIN_ANSWER_CHARS = 60
_SPECIFICITY_HINTS = ("例如", "比如", "当时", "结果", "我们", "用了", "选择", "% ", "%")


def _all_questions(plan: InterviewPlan) -> list[Question]:
    return [q for r in plan.rounds for q in r.questions]


def _round_for_question(plan: InterviewPlan, qid: str) -> InterviewRound | None:
    for r in plan.rounds:
        if any(q.question_id == qid for q in r.questions):
            return r
    return None


def _needs_followup(question: Question, answer: CandidateAnswer) -> bool:
    """Sprint 0 启发式 fallback —— 当没有 AnswerAssessment 可用时走这条。
    Sprint 5.5 task 4: self_intro 题永不追问, 不管多短。"""
    if question.category is QuestionCategory.SELF_INTRO:
        return False
    text = answer.text.strip()
    if len(text) < _MIN_ANSWER_CHARS:
        return True
    return not any(hint in text for hint in _SPECIFICITY_HINTS)


def _decide_followup(
    question: Question,
    answer: CandidateAnswer,
    assessment: AnswerAssessment | None,
    policy: FollowUpPolicy,
    followups_since: int,
) -> bool:
    """Sprint 5.6 三步决策的中间步 (assess 是 orchestrator 调的, 这里只 decide)。

    顺序:
    1) max_followups_per_question 硬上限 (含 self_intro=0): 命中即停
    2) self_intro 类别二次保护 (即使 policy max 被改成 >0, 这里也兜底)
    3) 有 assessment: sufficiency 与 confidence 双阈值过才停; 否则追问
    4) 无 assessment: 退到 Sprint 0 启发式 _needs_followup
    """
    if followups_since >= policy.max_followups_per_question:
        return False
    if question.category is QuestionCategory.SELF_INTRO:
        return False
    if assessment is not None:
        if (
            assessment.sufficiency >= policy.min_sufficiency_to_stop
            and assessment.confidence >= policy.min_confidence_to_stop
        ):
            return False
        return True
    # 无 assessment: 走 Sprint 0 启发式
    return _needs_followup(question, answer)


def _followup_text(
    question: Question,
    answer: CandidateAnswer,
    assessment: AnswerAssessment | None,
) -> str:
    """生成追问文本。
    Sprint 5.6 起 assessment.followup_goal 拼进 prompt 让 LLM 聚焦缺什么;
    LLM stub / 失败时退到通用模板 (有 followup_goal 也拼进模板)。"""
    fallback_generic = (
        "能再展开一个具体的例子吗? 比如当时面对的约束、你做的取舍, 以及最终结果。"
    )

    goal_hint = ""
    if assessment is not None and assessment.followup_goal.strip():
        goal_hint = f"追问应聚焦: {assessment.followup_goal.strip()}\n"
    missing_hint = ""
    if assessment is not None and assessment.missing_signals:
        missing_hint = (
            "候选人当前回答缺失: " + "; ".join(assessment.missing_signals) + "\n"
        )

    text = llm.complete(
        _FOLLOWUP_SYSTEM,
        f"问题: {question.text}\n候选人回答: {answer.text}\n"
        f"{missing_hint}{goal_hint}请输出追问:",
        max_tokens=160,
    )
    if not text or llm.is_stub(text):
        # LLM 不可用; fallback 模板 + goal 一起拼, 即使没 LLM 也比泛泛模板聚焦
        if assessment is not None and assessment.followup_goal.strip():
            return f"能聚焦讲一下: {assessment.followup_goal.strip()}"
        return fallback_generic
    return text


def _policy_for_question(plan: InterviewPlan, question: Question) -> FollowUpPolicy:
    """按当前题所在 stage 取 FollowUpPolicy。
    Sprint 5.6 阶段写死 stage 默认; 5.7 让 JobContext.followup_policy 覆盖。"""
    rnd = _round_for_question(plan, question.question_id)
    stage = rnd.stage if rnd is not None else InterviewStage.KNOWLEDGE
    return FollowUpPolicy.for_stage(stage)


def next_turn(
    session: InterviewSession, plan: InterviewPlan
) -> Question | FollowUp | None:
    """Interviewer 入口。返回下一个 Question / FollowUp, 或 None 结束。

    Sprint 5.6: orchestrator 在调本函数前已经把最新 AnswerAssessment 追加到
    session.assessments (如果 ASSESSOR_ENABLED + assess 没崩); 本函数只是
    decide_followup + generate_followup, 不再自己调 Assessor。"""
    questions = _all_questions(plan)
    if not questions:
        return None

    if not session.history:
        return questions[0]

    last = session.history[-1]
    if last.role != TurnRole.CANDIDATE:
        return None

    # 找到"当前问题"以及其后已经发过的追问数
    question_ids = {q.question_id for q in questions}
    current_q: Question | None = None
    followups_since = 0
    for turn in reversed(session.history):
        if turn.role != TurnRole.INTERVIEWER:
            continue
        if turn.ref_id in question_ids:
            current_q = next(q for q in questions if q.question_id == turn.ref_id)
            break
        followups_since += 1

    # 决策追问
    if current_q is not None:
        latest = session.answers[-1] if session.answers else None
        # 取当前题对应的最新 assessment (orchestrator 刚追加的)
        assessment = _latest_assessment_for(session, current_q.question_id)
        policy = _policy_for_question(plan, current_q)
        if latest is not None and _decide_followup(
            current_q, latest, assessment, policy, followups_since,
        ):
            return FollowUp(
                parent_question_id=current_q.question_id,
                text=_followup_text(current_q, latest, assessment),
                reason=_followup_reason(assessment),
            )

    # 进入下一题
    asked = [
        t.ref_id for t in session.history
        if t.role == TurnRole.INTERVIEWER and t.ref_id in question_ids
    ]
    next_idx = len(asked)
    if next_idx < len(questions):
        return questions[next_idx]
    return None


def _latest_assessment_for(
    session: InterviewSession, question_id: str,
) -> AnswerAssessment | None:
    """从 session.assessments 反向找对应 question_id 的最新一条 assessment。
    Assessor 顺序 append, 反向找命中即返。"""
    for a in reversed(session.assessments):
        if a.question_id == question_id:
            return a
    return None


def _followup_reason(assessment: AnswerAssessment | None) -> str:
    if assessment is not None and assessment.missing_signals:
        return (
            "AnswerAssessment 指出缺失信号: "
            + "; ".join(assessment.missing_signals)
        )
    if assessment is not None:
        return f"AnswerAssessment 判 sufficiency 不足 ({assessment.sufficiency:.2f})"
    return "回答较短或缺少具体例子, 需要进一步深挖 (启发式 fallback)。"
