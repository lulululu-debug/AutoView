"""面试会话三段式 —— Sprint 2-5。

POST   /interviews                       开始面试, 返回 session_id + 首问
POST   /interviews/{session_id}/answers  提交回答, 返回下一问或 done
GET    /interviews/{session_id}          中断恢复 / 看当前待答提示

为什么是这三个端点(而不是单一 /turn):
- start / submit / resume 在 orchestrator 层是三个状态机入口, API 层一一对应
  最符合直觉。客户端 SDK 也好用。
- finalize 不放在这里, 是 Sprint 2-6 (GET /interviews/{id}/report) 的隐式触发,
  因为对外的语义是"获取报告"而不是"结束会话"。

POST /interviews 只收 candidate_id:
- candidate -> 推 job (via candidates.job_id), -> 推 latest plan
  (via load_latest_plan_for_candidate)。HR 端不需要细粒度选 plan 版本。
- 把 plan 显式传给 orchestrator.start_session(...., plan=plan), 避免它重跑
  planner 产出不同的 plan_id。
- plan 还没生成时(BG Planner 还在跑) 返 409, 让客户端轮询完 GET .../plan
  再来开面试。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import AnswerSubmit, InterviewStart
from src import db, orchestrator
from src.schemas import EvaluationReport, TurnResult

router = APIRouter(prefix="/interviews", tags=["interviews"])


@router.post("", response_model=TurnResult, status_code=201)
def start_interview(body: InterviewStart) -> TurnResult:
    """开始一次面试: 加载 job + candidate + plan, 建会话, 返首问。"""
    candidate = db.load_candidate(body.candidate_id)
    if candidate is None:
        raise HTTPException(
            status_code=404, detail=f"candidate {body.candidate_id} 不存在",
        )

    job = db.load_job(candidate.job_id)
    if job is None:
        # 兜底: 理论上 FK RESTRICT 拦着, 但如果出现了别炸 500
        raise HTTPException(
            status_code=404, detail=f"job {candidate.job_id} 不存在",
        )

    plan = db.load_latest_plan_for_candidate(body.candidate_id)
    if plan is None:
        # plan 还在 Background Planner 里跑, 或失败了; 客户端应当先轮询
        # GET /jobs/{job_id}/candidates/{candidate_id}/plan
        raise HTTPException(
            status_code=409,
            detail="plan 尚未生成, 请先等 Planner 完成 (GET .../candidates/{id}/plan)",
        )

    return orchestrator.start_session(job, candidate, plan=plan)


@router.post("/{session_id}/answers", response_model=TurnResult)
def submit_answer(session_id: str, body: AnswerSubmit) -> TurnResult:
    """提交一条回答, 返回下一问或 done=True。
    SessionNotFound -> 404, SessionInvalidState (会话已 COMPLETED 再 submit) -> 409。"""
    return orchestrator.submit_answer(session_id, body.text)


@router.get("/{session_id}", response_model=TurnResult)
def resume_interview(session_id: str) -> TurnResult:
    """中断恢复 / 查询: 返当前待答提示, 或 done=True。
    SessionNotFound -> 404。"""
    return orchestrator.resume_session(session_id)


@router.get("/{session_id}/report", response_model=EvaluationReport)
def get_interview_report(session_id: str) -> EvaluationReport:
    """获取评估报告。
    - 会话已答完 (status=COMPLETED, 仍在 Redis): 隐式 finalize 归档 PG, 返报告
    - 会话尚未答完 (status=IN_PROGRESS): 409, 客户端先答完再来
                                          (提前结束面试是另一个端点的事, Sprint 2 不做)
    - 已归档 (Redis 已清, PG 有报告): 直接从 PG 读
    - 都没有: 404
    幂等: 同一 session_id 多次 GET 都返同一份。"""
    return orchestrator.get_report(session_id)
