"""Interviewer Agent — 提问、依据回答决定是否追问一次。

骨架规则: 每个 Question 至多触发一次 FollowUp。
追问触发: 回答过短(<60 字符), 或缺少具体例子线索。
追问文本由 LLM 生成, 不可用时回退到通用模板。

返回值约定:
- Question  -> 主问题
- FollowUp  -> 针对当前问题的追问
- None      -> 本次面试已无新内容
Orchestrator 负责将返回值写入 session.history, 并补上候选人答复。
"""
from __future__ import annotations

from src import llm
from src.schemas import (
    CandidateAnswer,
    FollowUp,
    InterviewPlan,
    InterviewSession,
    Question,
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


def _needs_followup(answer: CandidateAnswer) -> bool:
    text = answer.text.strip()
    if len(text) < _MIN_ANSWER_CHARS:
        return True
    return not any(hint in text for hint in _SPECIFICITY_HINTS)


def _followup_text(question: Question, answer: CandidateAnswer) -> str:
    fallback = "能再展开一个具体的例子吗? 比如当时面对的约束、你做的取舍, 以及最终结果。"
    text = llm.complete(
        _FOLLOWUP_SYSTEM,
        f"问题: {question.text}\n候选人回答: {answer.text}\n请输出追问:",
        max_tokens=160,
    )
    if not text or llm.is_stub(text):
        return fallback
    return text


def next_turn(
    session: InterviewSession, plan: InterviewPlan
) -> Question | FollowUp | None:
    """Interviewer 入口。返回下一个 Question / FollowUp, 或 None 结束。"""
    questions = _all_questions(plan)
    if not questions:
        return None

    # 首问
    if not session.history:
        return questions[0]

    last = session.history[-1]
    # 上一回合是面试官说话, 说明还在等候选人, 不再产出新提问
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

    # 同题至多追问一次
    if current_q is not None and followups_since == 0:
        latest = session.answers[-1] if session.answers else None
        if latest is not None and _needs_followup(latest):
            return FollowUp(
                parent_question_id=current_q.question_id,
                text=_followup_text(current_q, latest),
                reason="回答较短或缺少具体例子, 需要进一步深挖。",
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
