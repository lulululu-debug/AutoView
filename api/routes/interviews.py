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

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response

from api.schemas import AnswerSubmit, InterviewStart
from src import cache, db, media_store, orchestrator, stt
from src.schemas import EvaluationReport, TurnResult
from src.tts import AUDIO_MIME

log = logging.getLogger(__name__)

# 单个录像分片上限。前端 5s 一片 @ ~650kbps ≈ 400KB, 20MB 是宽裕的防滥用线
_MAX_RECORDING_CHUNK_BYTES = 20 * 1024 * 1024

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


@router.websocket("/{session_id}/transcribe")
async def transcribe(ws: WebSocket, session_id: str) -> None:
    """Sprint 6-4: 语音转写 WS 代理。厂商 key 不出后端, 前端只连本端点。

    协议 (与 web session/stt.ts 共同遵守):
    - client -> server: 二进制帧 = PCM 16bit LE 16kHz mono 分片;
      文本帧 {"type":"finish"} = 说完了, 触发厂商定稿
    - server -> client: {"type":"partial","text":累计全文}
                        {"type":"final","text":累计全文}
                        {"type":"done"} / {"type":"error","message":...}
    - 关闭码: 4404 session 不存在, 4503 STT 未配置

    任何厂商侧异常都翻译成 error 消息 + 关连接, 前端退打字 —— 打字永远保底。
    """
    await ws.accept()

    # session 校验, 同 audio 端点: 不给无会话方白嫖 ASR
    try:
        session = cache.load_session(session_id)
    except Exception:
        session = None
    if session is None:
        await ws.send_json({"type": "error", "message": "面试会话不存在或已结束"})
        await ws.close(code=4404)
        return

    stream = await stt.create_stream()
    if stream is None:
        await ws.send_json({"type": "error", "message": "语音转写未配置"})
        await ws.close(code=4503)
        return

    async def pump_client() -> None:
        """浏览器 -> 厂商: 转发音频分片; finish 信号触发定稿。"""
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            chunk = msg.get("bytes")
            if chunk:
                await stream.send_audio(chunk)
                continue
            text = msg.get("text")
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if parsed.get("type") == "finish":
                await stream.finish()

    async def pump_vendor() -> None:
        """厂商 -> 浏览器: 转发转写事件; final 后补 done 收尾。"""
        while True:
            ev = await stream.receive()
            if ev is None:
                return
            payload: dict = {"type": ev.kind}
            if ev.kind in ("partial", "final"):
                payload["text"] = ev.text
            if ev.kind == "error":
                payload["message"] = ev.message
            await ws.send_json(payload)
            if ev.kind in ("done", "error"):
                return
            if ev.kind == "final":
                await ws.send_json({"type": "done"})
                return

    async def _safe(coro) -> None:
        # 断连 / 厂商异常都走这里静默收尾; 主链路 (打字提交) 不受影响
        try:
            await coro
        except Exception:
            log.debug("transcribe pump 提前结束", exc_info=True)

    try:
        tasks = {
            asyncio.create_task(_safe(pump_client())),
            asyncio.create_task(_safe(pump_vendor())),
        }
        _done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        await stream.close()
        try:
            await ws.close()
        except Exception:
            pass


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


@router.get("/{session_id}/fillers/{idx}/audio")
def get_filler_audio(session_id: str, idx: int) -> Response:
    """Sprint 6-3: 过渡语音 ("嗯, 我了解了" 等固定文案)。

    前端进面试后预取全部 filler, 提交回答时播放, 遮蔽 Assessor +
    lazy project gen 的 3-8s 思考空档。语义与 turn 音频端点一致:
    200 mp3 / 204 TTS 不可用 / 404 session 或 idx 不存在。
    文案固定不变, 浏览器缓存给足 7 天。
    """
    audio = orchestrator.get_filler_audio(session_id, idx)
    if audio is None:
        return Response(status_code=204)
    return Response(
        content=audio,
        media_type=AUDIO_MIME,
        headers={"Cache-Control": "private, max-age=604800"},
    )


@router.post("/{session_id}/recordings", status_code=204)
async def upload_recording_chunk(session_id: str, request: Request) -> None:
    """Sprint 6-5: 追加一个录像分片 (MediaRecorder webm)。**只录不判**。

    - 204: 落盘成功 (分片按序拼接, 顺序由前端串行上传链保证)
    - 404: session 不在 Redis (面试结束后不再收流)
    - 409: 录制存储未配置 (前端 /media/config 探测后不该走到这)
    - 413: 单片超限 (防滥用)

    录像仅作 HR 复核素材; 任何打分/分析消费都属 Sprint 7 且受 §7 约束。
    """
    if not media_store.is_configured():
        raise HTTPException(status_code=409, detail="录制存储未配置")

    try:
        session = cache.load_session(session_id)
    except Exception:
        session = None
    if session is None:
        raise HTTPException(status_code=404, detail="面试会话不存在或已结束")

    chunk = await request.body()
    if len(chunk) > _MAX_RECORDING_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail="录像分片过大")

    try:
        media_store.append_chunk(session_id, chunk)
    except media_store.InvalidSessionId:
        raise HTTPException(status_code=404, detail="面试会话不存在或已结束")


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
