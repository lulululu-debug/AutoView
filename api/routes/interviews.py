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
from fastapi.responses import Response

from api.schemas import AnswerSubmit, InterviewStart
from src import db, orchestrator
from src.schemas import EvaluationReport, TurnResult
from src.tts import AUDIO_MIME

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


@router.get("/{session_id}/turns/{ref_id}/audio")
def get_turn_audio(session_id: str, ref_id: str) -> Response:
    """Sprint 6-2: 面试官 turn 的 TTS 音频 (mp3)。

    - 200 audio/mpeg: 合成成功 (tts 层按 text+provider+voice Redis 缓存,
      同一 ref_id 幂等, 重复请求不重复打厂商 API)
    - 204: TTS 未配置 / 合成失败 -> 前端静默退纯文字 (双路径保底)
    - 404: session 不存在 (SessionNotFound) / ref_id 不是本会话的
      面试官 turn (TurnNotFound)

    Cache-Control 让浏览器本地也缓一天: 中断恢复重进同一题不再回源。
    """
    audio = orchestrator.get_turn_audio(session_id, ref_id)
    if audio is None:
        return Response(status_code=204)
    return Response(
        content=audio,
        media_type=AUDIO_MIME,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/{session_id}/finalize", status_code=204)
def finalize_interview(session_id: str) -> None:
    """触发归档但不返回 report 数据。

    候选人 done 页 (Sprint 5-3) mount 时调用, 把 Redis 里 status=COMPLETED
    的 session 尽快落 PG。否则 HR 端 list_candidates 永远看到 ready, 看不到
    completed (候选人不调 GET /report 是 Sprint 4 的合规决策, 报告 JSON
    一秒钟都不应经过候选人浏览器)。

    内部走 orchestrator.get_report (含 finalize + 幂等), 丢掉返回值。
    状态语义与 GET /report 一致:
    - 不存在 -> 404
    - IN_PROGRESS -> 409 (尚未答完, 不允许提前归档)
    - 已 finalize 过 -> 204 幂等
    - 第一次成功 finalize -> 204

    合规: 不返回 report 内容; FastAPI 看到 status_code=204 自动空 body。
    """
    orchestrator.get_report(session_id)


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
