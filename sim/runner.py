"""仿真跑批引擎 —— Sprint 6.5 task 1。

一场仿真 = 建 job/candidate → 简历分段 + Milvus ingest (与生产 BG 流程同款,
同步跑) → planner.plan → orchestrator 三段式走完整面试 (LLM 扮演候选人作答)
→ get_report 归档。

直调 planner + orchestrator, 不走 HTTP —— 测的是 agent 编排的效果,
不是 FastAPI 的序列化。artifact 落 JSON, 字段足够 task 2/4 的指标与
judge 复算, 不需要回库查。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from sim import candidate as sim_candidate
from sim.personas import Persona

log = logging.getLogger(__name__)

# 防失控保险: CompletionPolicy 自身有 max_total_questions, 这里再挂一道
_MAX_TURNS = 40

_JD_BACKEND = """负责核心业务系统的后端设计与开发: 高并发服务的性能优化与稳定性
保障, 分布式场景下的数据一致性方案, 与产品/前端协作推进需求落地, 参与线上问题
排查与故障复盘。要求扎实的计算机基础 (数据库/网络/操作系统), 熟悉常见中间件
(MySQL/Redis/Kafka), 有良好的沟通与工程素养。"""


def _job_for_track(track) -> "JobContext":
    from src.schemas import JobContext

    return JobContext(
        title="后端工程师",
        jd=_JD_BACKEND,
        requirements=["分布式系统", "数据库优化", "高并发", "沟通协作"],
        role_family="backend",
        track=track,
    )


def run_one(persona: Persona, run_id: str, out_dir: Path) -> dict:
    """跑一场完整仿真面试, artifact 写盘并返回摘要 dict。"""
    from src import db, ingestion, orchestrator
    from src.agents import planner
    from src.schemas import CandidateProfile

    t0 = time.time()
    job = _job_for_track(persona.track)
    db.save_job(job)

    cand = CandidateProfile(
        candidate_id=f"sim-{persona.persona_id}-{run_id}-{int(t0)}",
        job_id=job.job_id,
        resume=persona.resume,
    )
    db.save_candidate(cand)

    # 生产 BG 流程同款: 分段落 PG + 切片入 Milvus; 失败不阻塞 (下游有 fallback)
    try:
        sections = ingestion.segment_resume(persona.resume)
        db.save_candidate_sections(cand.candidate_id, sections)
    except Exception:
        log.warning("segment_resume 失败, lazy gen 走老 RAG 路径", exc_info=True)
    try:
        ingestion.ingest_resume(cand.candidate_id, persona.resume)
    except Exception:
        log.warning("ingest_resume 失败, 项目题退 resume 全文路径", exc_info=True)

    plan = planner.plan(job, cand)
    db.save_plan(plan, cand.candidate_id)

    turn = orchestrator.start_session(job, cand, plan=plan)
    session_id = turn.session_id
    history: list[tuple[str, str]] = []
    turn_latencies: list[float] = []

    n_turns = 0
    while not turn.done and n_turns < _MAX_TURNS:
        question = turn.prompt or ""
        ans = sim_candidate.answer(persona, question, history, nonce=run_id)
        history.append(("面试官", question))
        history.append(("候选人", ans))
        t_turn = time.time()
        turn = orchestrator.submit_answer(session_id, ans)
        turn_latencies.append(round(time.time() - t_turn, 2))
        n_turns += 1

    report = orchestrator.get_report(session_id)
    session = db.load_session(session_id)

    artifact = {
        "persona_id": persona.persona_id,
        "level": persona.level,
        "expected_rank": persona.expected_rank,
        "track": persona.track.value,
        "run_id": run_id,
        "job_id": job.job_id,
        "candidate_id": cand.candidate_id,
        "session_id": session_id,
        "overall": report.overall,
        "needs_human_review": report.needs_human_review,
        "competency_coverage": report.competency_coverage,
        "n_answers": n_turns,
        "duration_s": round(time.time() - t0, 1),
        "turn_latencies_s": turn_latencies,
        # 完整对象: task 2 (指标) / task 4 (judge) 直接从 artifact 复算
        "job": job.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "session": session.model_dump(mode="json") if session else None,
        "report": report.model_dump(mode="json"),
    }

    out_path = out_dir / f"{persona.persona_id}_{run_id}.json"
    out_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=1), encoding="utf-8",
    )

    return {
        "persona_id": persona.persona_id,
        "level": persona.level,
        "run_id": run_id,
        "overall": report.overall,
        "n_answers": n_turns,
        "needs_human_review": report.needs_human_review,
        "duration_s": artifact["duration_s"],
        "artifact": str(out_path),
    }
