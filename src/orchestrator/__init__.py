"""Orchestrator — 基于 Redis 的会话状态机(Sprint 1)。

三段式 API:
- start_session(job, candidate)         -> TurnResult     创建 plan + session, 给出首问
- submit_answer(session_id, text)       -> TurnResult     推进一步, 给出下一问 或 done
- resume_session(session_id)            -> TurnResult     中断恢复, 重发当前待答的提示
- finalize(session_id)                  -> EvaluationReport  跑评估、归档 Postgres、清 Redis

便利方法:
- run_interview(job, candidate, answers) -> EvaluationReport
  本质就是 start_session + 循环 submit_answer + finalize, 保留它让骨架 src.main
  以及 Sprint 0 的端到端测试方式不必改。

状态机契约:
- Session 在 Redis 中只存 IN_PROGRESS 或 COMPLETED(等待 finalize) 两个状态;
  finalize 后 session/plan 立刻从 Redis 删除, 仅保留在 Postgres 归档。
- 每次 submit_answer 结束时, history 末尾要么是 interviewer turn(等候答题),
  要么 status=COMPLETED(全部题目走完)。这是 resume_session 能直接重发的依据。
- Plan 与 Session TTL 一致(见 cache.base.ttl_seconds), 同生共死。

Agent 之间不直接互相调用 —— planner/interviewer/analyzer/evaluator 全部由本模块路由。
"""
from __future__ import annotations

from src import cache, db
from src.agents import analyzer, evaluator, interviewer, planner
from src.schemas import (
    CandidateAnswer,
    CandidateProfile,
    EvaluationReport,
    FollowUp,
    InterviewPlan,
    InterviewSession,
    JobContext,
    Question,
    SessionStatus,
    Turn,
    TurnResult,
    TurnRole,
)


class SessionNotFound(LookupError):
    """session_id 在 Redis 中找不到(过期或从未存在)。"""


class SessionInvalidState(RuntimeError):
    """会话状态不允许当前操作, 比如对已 COMPLETED 的会话再 submit_answer。"""


# ---------- 内部工具 ----------

def _append_interviewer_turn(session: InterviewSession, item: Question | FollowUp) -> str:
    ref_id = item.question_id if isinstance(item, Question) else item.followup_id
    session.history.append(
        Turn(role=TurnRole.INTERVIEWER, text=item.text, ref_id=ref_id)
    )
    return ref_id


def _last_interviewer_turn(session: InterviewSession) -> Turn | None:
    return next(
        (t for t in reversed(session.history) if t.role == TurnRole.INTERVIEWER),
        None,
    )


# ---------- 三段式 API ----------

def start_session(
    job: JobContext,
    candidate: CandidateProfile,
    plan: InterviewPlan | None = None,
) -> TurnResult:
    """生成/复用 plan + 新建 session, 写 Redis, 返回首问。

    plan: 显式传入时复用(不调 planner), 默认 None 时现场生成。
    设计意图: API 路径(Sprint 2 之后) 从 PG 加载已生成的 plan 并显式传入,
    确保 HR 端看到的 plan 与面试实际用的是同一份;
    内存路径(run_interview / src.main / 旧 eval) 不传, 由 planner 现场生成
    (backward compat)。
    """
    if plan is None:
        plan = planner.plan(job, candidate)
    session = InterviewSession(
        plan_id=plan.plan_id,
        job_id=job.job_id,
        status=SessionStatus.IN_PROGRESS,
    )

    first = interviewer.next_turn(session, plan)
    if first is None:
        # 退化情况: 计划没有任何题目, 直接完结
        session.status = SessionStatus.COMPLETED
        cache.save_plan(plan)
        cache.save_session(session)
        return TurnResult(session_id=session.session_id, done=True)

    ref_id = _append_interviewer_turn(session, first)
    cache.save_plan(plan)
    cache.save_session(session)
    return TurnResult(
        session_id=session.session_id, done=False, prompt=first.text, ref_id=ref_id,
    )


def submit_answer(session_id: str, answer_text: str) -> TurnResult:
    """记录候选人回答, 推进一步, 返回下一问或 done。"""
    session = cache.load_session(session_id)
    if session is None:
        raise SessionNotFound(
            f"session {session_id} 不在 Redis 中(过期或从未创建)"
        )
    if session.status != SessionStatus.IN_PROGRESS:
        raise SessionInvalidState(
            f"session {session_id} 状态为 {session.status.value}, 不可再 submit_answer"
        )

    plan = cache.load_plan(session.plan_id)
    if plan is None:
        # session 还在但 plan 没了: TTL 异常或被意外清掉
        raise SessionNotFound(f"plan {session.plan_id} 不在 Redis 中")

    last = _last_interviewer_turn(session)
    if last is None or last.ref_id is None:
        raise SessionInvalidState(
            f"session {session_id} 没有等待中的 interviewer 提示, 无法接收回答"
        )

    answer = CandidateAnswer(question_id=last.ref_id, text=answer_text)
    session.answers.append(answer)
    session.history.append(
        Turn(role=TurnRole.CANDIDATE, text=answer_text, ref_id=answer.answer_id)
    )

    nxt = interviewer.next_turn(session, plan)
    if nxt is None:
        session.status = SessionStatus.COMPLETED
        cache.save_session(session)
        return TurnResult(session_id=session_id, done=True)

    ref_id = _append_interviewer_turn(session, nxt)
    cache.save_session(session)
    return TurnResult(
        session_id=session_id, done=False, prompt=nxt.text, ref_id=ref_id,
    )


def resume_session(session_id: str) -> TurnResult:
    """中断恢复: 返回当前待回答的提示(或已完成时 done=True)。幂等。"""
    session = cache.load_session(session_id)
    if session is None:
        raise SessionNotFound(f"session {session_id} 不在 Redis 中")

    if session.status == SessionStatus.COMPLETED:
        return TurnResult(session_id=session_id, done=True)

    last = _last_interviewer_turn(session)
    if last is None or last.ref_id is None:
        raise SessionInvalidState(
            f"session {session_id} 无待答提示, 状态异常"
        )
    return TurnResult(
        session_id=session_id, done=False, prompt=last.text, ref_id=last.ref_id,
    )


def finalize(session_id: str) -> EvaluationReport:
    """跑 evaluator, 把 session + report 归档进 Postgres, 清 Redis。"""
    session = cache.load_session(session_id)
    if session is None:
        raise SessionNotFound(f"session {session_id} 不在 Redis 中")
    plan = cache.load_plan(session.plan_id)
    if plan is None:
        raise SessionNotFound(f"plan {session.plan_id} 不在 Redis 中")

    # 允许在尚未走完所有题目时由调用方决定提前结束
    if session.status != SessionStatus.COMPLETED:
        session.status = SessionStatus.COMPLETED

    signals = analyzer.analyze(session)
    report = evaluator.evaluate(session, plan, signals)

    db.save_session(session)
    db.save_report(report)

    cache.delete_session(session_id)
    cache.delete_plan(session.plan_id)

    return report


# ---------- 便利方法: 保持 Sprint 0 端到端 API ----------

def run_interview(
    job: JobContext,
    candidate: CandidateProfile,
    candidate_answers: list[str],
) -> EvaluationReport:
    """便利方法: start + 顺序喂答案 + finalize, 串成 Sprint 0 风格的一把跑完。
    候选人回答耗尽时提前 finalize。"""
    result = start_session(job, candidate)
    for ans in candidate_answers:
        if result.done:
            break
        result = submit_answer(result.session_id, ans)
    return finalize(result.session_id)
