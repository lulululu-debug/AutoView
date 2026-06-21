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

import logging
import os

from src import cache, db

log = logging.getLogger(__name__)
from src.agents import analyzer, assessor, evaluator, interviewer, planner
from src.schemas import (
    CandidateAnswer,
    CandidateProfile,
    EvaluationReport,
    FollowUp,
    InterviewPlan,
    InterviewSession,
    JobContext,
    Question,
    QuestionCategory,
    SessionStatus,
    Turn,
    TurnResult,
    TurnRole,
)


class SessionNotFound(LookupError):
    """session_id 在 Redis 中找不到(过期或从未存在)。"""


class SessionInvalidState(RuntimeError):
    """会话状态不允许当前操作, 比如对已 COMPLETED 的会话再 submit_answer。"""


def _assessor_enabled() -> bool:
    """Sprint 5.6: 由 env flag 控制 Assessor 是否进入 production codepath。
    默认 false —— 即代码部署进了仓库但不参与追问决策, 走 Sprint 0 启发式。
    eval / 人工 review calibration 通过后, 才在部署层把 flag 翻 true 上线。"""
    return os.environ.get("ASSESSOR_ENABLED", "").lower() in ("1", "true", "yes")


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
    # Sprint 5.5 task 4: lazy 占位题不再在 start_session 回灌. 改在 submit_answer
    # 检测到"下一题是 lazy + text 空"时, 用 session.intro_text + Resume RAG 现场
    # resolve. 这样 lateral / campus 都自然在候选人答完 self_intro 之后才生成
    # project 题, 题目能反映候选人自我介绍里提到的具体内容。
    session = InterviewSession(
        plan_id=plan.plan_id,
        job_id=job.job_id,
        status=SessionStatus.IN_PROGRESS,
    )

    first = interviewer.next_turn(session, plan, job=job)
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
    """记录候选人回答, 推进一步, 返回下一问或 done。

    Sprint 5.5 task 4:
    - 若刚答的是 self_intro 题, 把答案存到 session.intro_text, 供 project 题
      lazy gen 用 (前端 / HR UI 不直接展示这个字段)。
    - 若 Interviewer 给出的下一题是 lazy 占位 (text 空), 在返回 prompt 之前
      调 planner.resolve_lazy_questions 把整 plan 的 lazy 题都回灌, 写回 Redis,
      再用 question_id 找到回灌后的题给出 prompt。
      一次性回灌整 plan 比"逐题回灌"省 LLM round; project stage 多道题用同一份
      intro_text + 同一次 Resume RAG 调用, 也保证主题一致性。
    """
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

    # 若刚答的是 self_intro 题, 落 intro_text (覆盖式 —— 实际 plan 只 1 道,
    # 多道场景 task 4 不考虑, 取最后一条)。
    answered_q = _find_question(plan, last.ref_id)
    if answered_q is not None and answered_q.category is QuestionCategory.SELF_INTRO:
        session.intro_text = answer_text

    # Sprint 5.7: 让 Interviewer 看到 job.followup_policy / completion_policy
    # (HR 在高级折叠区配的覆盖)。job 从 PG 反查, 缺数据时为 None ->
    # next_turn 退到 stage / schema 默认 policy (5.6 行为)。
    # Sprint 5.9: job 还要给 Assessor 让它能取本题 competency 的 aspect 候选,
    # 输出 covered_aspects 喂给 richness 计算。
    job_for_decision = db.load_job(session.job_id)

    # Sprint 5.6: Assessor 在 next_turn 之前跑, 把 AnswerAssessment 落进
    # session.assessments. Interviewer 取 session.assessments[-1] 决策追问 +
    # 用 followup_goal 拼追问 prompt. ASSESSOR_ENABLED=false 时跳过, 走原启发式。
    if _assessor_enabled() and answered_q is not None:
        try:
            assessment = assessor.assess(
                answered_q, answer, session, plan, job=job_for_decision,
            )
            session.assessments.append(assessment)
        except Exception:
            # Assessor 自己有 LLM->启发式双路径, 理论上 assess() 不会抛;
            # 真抛了一律静默吞, 让面试链路不受 Assessor 影响。
            log.exception(
                "assessor.assess raised unexpectedly (qid=%s); skipping",
                answered_q.question_id,
            )

    nxt = interviewer.next_turn(session, plan, job=job_for_decision)
    if nxt is None:
        session.status = SessionStatus.COMPLETED
        cache.save_session(session)
        return TurnResult(session_id=session_id, done=True)

    # 若下一题是 lazy 占位, 现场 resolve 整 plan, 写回 cache + PG, 找到回灌后的题。
    if isinstance(nxt, Question) and nxt.lazy and not nxt.text:
        plan = _resolve_lazy_now(plan, session)
        cache.save_plan(plan)
        # Sprint 5.8 patch: 同步把 resolve 后的 plan 写回 PG, 让 HR 端 StageView
        # (走 GET /jobs/{j}/candidates/{c}/plan 拉 PG) 显示已生成的项目题 text;
        # 不然 finalize 之后永远显示 "待懒生成"。
        _persist_resolved_plan(plan)
        nxt = _find_question(plan, nxt.question_id) or nxt

    ref_id = _append_interviewer_turn(session, nxt)
    cache.save_session(session)
    return TurnResult(
        session_id=session_id, done=False, prompt=nxt.text, ref_id=ref_id,
    )


def _find_question(plan: InterviewPlan, question_id: str) -> Question | None:
    """按 id 在 plan 各 round 中找题。resolve 前后 question_id 不变, 这是
    Planner 的契约 —— resolve 只换 text + chunks。"""
    for r in plan.rounds:
        for q in r.questions:
            if q.question_id == question_id:
                return q
    return None


def _resolve_lazy_now(plan: InterviewPlan, session: InterviewSession) -> InterviewPlan:
    """从 PG 反查 job + candidate, 调 planner.resolve_lazy_questions 把整 plan
    的 lazy 题回灌。任何 PG 缺失走静态 fallback, 不让面试卡死。"""
    job = db.load_job(session.job_id)
    candidate = db.load_candidate_for_plan(plan.plan_id)
    if job is None or candidate is None:
        # 极端兜底: PG 里的 job / candidate 找不到 (内存路径 / 老数据);
        # 用空 candidate / 占位 job 让 resolve 走 fallback 模板, 至少题面非空。
        job = job or JobContext(
            job_id=session.job_id, title="(unknown job)", jd="",
        )
        candidate = candidate or CandidateProfile(
            candidate_id=session.session_id, resume="",
        )
    return planner.resolve_lazy_questions(
        plan, job, candidate, intro_text=session.intro_text,
    )


def _persist_resolved_plan(plan: InterviewPlan) -> None:
    """Sprint 5.8 patch: 把 resolve 后的 plan 写回 PG。
    PG.InterviewPlanORM 是按 candidate_id 落的, 反查一次拿 id。
    内存路径 (run_interview / src.main 没 candidate 在 PG) 直接静默吞,
    不阻塞面试链路。"""
    candidate = db.load_candidate_for_plan(plan.plan_id)
    if candidate is None or candidate.candidate_id is None:
        log.info(
            "resolved plan 没找到对应 candidate, 跳过 PG sync (plan_id=%s)",
            plan.plan_id,
        )
        return
    try:
        db.save_plan(plan, candidate_id=candidate.candidate_id)
    except Exception:
        # PG 写挂不能让面试停 (Redis 那份 plan 已 saved); 只 log + 继续
        log.exception(
            "persist resolved plan to PG 失败 (plan_id=%s), Redis 那份仍有效",
            plan.plan_id,
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


def get_report(session_id: str) -> EvaluationReport:
    """获取面试报告。三态:
    - Redis 里仍在 + status=COMPLETED -> 调 finalize, 归档 PG, 清 Redis, 返报告
    - Redis 里仍在 + status=IN_PROGRESS -> 抛 SessionInvalidState (不允许提前结束)
    - Redis 里没了 (已 finalize 过) -> 从 PG 读, 没有则 SessionNotFound

    幂等: 同一 session_id 多次调用都返同一份报告。
    第一次走 Redis -> finalize 分支, 第二次起走 PG 分支。

    与 finalize 的区别: finalize 会无条件把 IN_PROGRESS 强转成 COMPLETED
    (允许调用方提前结束面试), 本函数严格拒绝 IN_PROGRESS。"""
    session = cache.load_session(session_id)
    if session is not None:
        if session.status != SessionStatus.COMPLETED:
            raise SessionInvalidState(
                f"session {session_id} 尚未答完, 无法取报告"
            )
        return finalize(session_id)

    report = db.load_report_by_session(session_id)
    if report is None:
        raise SessionNotFound(
            f"session {session_id} 不存在 (从未创建, 或已超出归档保留期)"
        )
    return report


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
    # Sprint 5.7: 让 Evaluator 看到 job.completion_policy 决定 evidence_insufficient
    # 阈值; PG 缺 job (内存路径) 时 None -> 用 schema 默认。
    job_for_eval = db.load_job(session.job_id)
    report = evaluator.evaluate(session, plan, signals, job=job_for_eval)

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
