"""Evaluator Agent — 将 InterviewSession 转为 EvaluationReport。

合规约束 (ARCHITECTURE.md 第 7 节):
- 内容维度 content_scores 与表现维度 performance_observations 严格分区
- overall 只由 content_scores 加权得出, 不依赖任何软信号
- needs_human_review 默认为 True
"""
from __future__ import annotations

from src import llm
from src.schemas import (
    Competency,
    DimensionScore,
    EvaluationReport,
    InterviewPlan,
    InterviewSession,
    PerformanceObservation,
    Question,
    Signal,
)

_SUMMARY_SYSTEM = (
    "你是一名招聘委员会主席。请用 3-5 句中文综合评估候选人,"
    "指出突出表现与待观察点, 不要重复分数, 不要做录用建议。"
)

_SPECIFICITY_KEYWORDS = ("例如", "比如", "当时", "结果", "我们", "用了", "选择", "%")


def _score_for_competency(
    comp: Competency,
    questions: list[Question],
    session: InterviewSession,
) -> DimensionScore:
    comp_q_ids = {q.question_id for q in questions if q.competency_id == comp.competency_id}
    answers = [a for a in session.answers if a.question_id in comp_q_ids]

    if not answers:
        return DimensionScore(
            competency_id=comp.competency_id,
            score=0.0,
            evidence=["未收集到该维度的有效回答"],
        )

    total_len = sum(len(a.text.strip()) for a in answers)
    specificity_hits = sum(
        1 for a in answers for kw in _SPECIFICITY_KEYWORDS if kw in a.text
    )
    base = min(80.0, 35.0 + total_len * 0.35)
    bonus = min(15.0, specificity_hits * 3.0)
    score = round(base + bonus, 1)

    evidence = [
        a.text.strip()[:120] + ("…" if len(a.text.strip()) > 120 else "")
        for a in answers
    ]
    return DimensionScore(
        competency_id=comp.competency_id, score=score, evidence=evidence,
    )


def _summary(
    session: InterviewSession,
    content_scores: list[DimensionScore],
    comps: list[Competency],
) -> str:
    transcript = "\n".join(f"[{t.role.value}] {t.text}" for t in session.history)
    score_lines = "\n".join(
        f"- {next((c.name for c in comps if c.competency_id == s.competency_id), s.competency_id)}: {s.score:.1f}"
        for s in content_scores
    )
    user = (
        f"对话:\n{transcript}\n\n各内容维度分数:\n{score_lines}\n\n请给出 3-5 句综合评估。"
    )
    text = llm.complete(_SUMMARY_SYSTEM, user, max_tokens=400)
    if not text or llm.is_stub(text):
        avg = sum(s.score for s in content_scores) / max(len(content_scores), 1)
        return (
            f"候选人完成 {len(session.answers)} 条回答, 内容维度平均 {avg:.1f}。"
            "证据已记录在各维度下, 建议人工复核后再做决定。"
        )
    return text


def evaluate(
    session: InterviewSession,
    plan: InterviewPlan,
    signals: list[Signal] | None = None,
) -> EvaluationReport:
    """Evaluator 入口: Session + Plan + (可选) Signals -> EvaluationReport。"""
    signals = signals or []
    comps = [c for r in plan.rounds for c in r.competencies]
    questions = [q for r in plan.rounds for q in r.questions]

    content_scores = [_score_for_competency(c, questions, session) for c in comps]

    total_weight = sum(c.weight for c in comps) or 1.0
    overall = round(
        sum(
            s.score * next(c.weight for c in comps if c.competency_id == s.competency_id)
            for s in content_scores
        ) / total_weight,
        1,
    )

    performance = [
        PerformanceObservation(
            kind=sig.kind, observation=sig.value, confidence=sig.confidence,
        )
        for sig in signals
    ]

    return EvaluationReport(
        session_id=session.session_id,
        content_scores=content_scores,
        performance_observations=performance,
        overall=overall,
        summary=_summary(session, content_scores, comps),
        needs_human_review=True,
    )
