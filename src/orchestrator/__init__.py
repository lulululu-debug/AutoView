"""Orchestrator — 串联 Planner → Interviewer 循环 → Analyzer → Evaluator。

骨架阶段在内存里维护一个 InterviewSession, 候选人回答按顺序由调用方传入。
Agent 之间不直接互相调用, 全部由本模块路由。
"""
from __future__ import annotations

from src.agents import analyzer, evaluator, interviewer, planner
from src.schemas import (
    CandidateAnswer,
    CandidateProfile,
    EvaluationReport,
    FollowUp,
    InterviewSession,
    JobContext,
    Question,
    SessionStatus,
    Turn,
    TurnRole,
)


def run_interview(
    job: JobContext,
    candidate: CandidateProfile,
    candidate_answers: list[str],
) -> EvaluationReport:
    """端到端跑一次面试。

    candidate_answers 是按顺序作答的纯文本列表; 用尽则提前结束面试。
    返回最终 EvaluationReport。
    """
    plan = planner.plan(job, candidate)
    session = InterviewSession(
        plan_id=plan.plan_id,
        job_id=job.job_id,
        status=SessionStatus.IN_PROGRESS,
    )
    answers_iter = iter(candidate_answers)

    while True:
        item = interviewer.next_turn(session, plan)
        if item is None:
            break

        if isinstance(item, Question):
            session.history.append(
                Turn(role=TurnRole.INTERVIEWER, text=item.text, ref_id=item.question_id)
            )
            parent_q_id = item.question_id
        elif isinstance(item, FollowUp):
            session.history.append(
                Turn(role=TurnRole.INTERVIEWER, text=item.text, ref_id=item.followup_id)
            )
            parent_q_id = item.parent_question_id
        else:
            break

        try:
            answer_text = next(answers_iter)
        except StopIteration:
            break

        answer = CandidateAnswer(question_id=parent_q_id, text=answer_text)
        session.answers.append(answer)
        session.history.append(
            Turn(role=TurnRole.CANDIDATE, text=answer_text, ref_id=answer.answer_id)
        )

    session.status = SessionStatus.COMPLETED
    signals = analyzer.analyze(session)
    return evaluator.evaluate(session, plan, signals)
