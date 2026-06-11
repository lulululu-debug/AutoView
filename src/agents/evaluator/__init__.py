"""Evaluator Agent — 将 InterviewSession 转为 EvaluationReport。

合规约束 (ARCHITECTURE.md §7):
- 内容维度 content_scores 与表现维度 performance_observations 严格分区
- overall 只由 content_scores 加权得出, 不依赖任何软信号
- needs_human_review 默认为 True

Sprint 3-7 RAG: 召回 JD + 公司资料切片做 context-aware summary。
- query = competency 维度名 + 描述 + 候选人回答摘要
- 召回 filter: source_id = job_id (拿到本职位的 JD 与公司资料, 不包含其他职位)
- EvaluationReport.rag_context_chunk_ids 记录用到的 document_id 列表, 供
  HR 端审计 + Sprint 3-8 召回 eval 校验
- 三级 fallback: 召回 + LLM -> 召回 + stub fallback -> 现场无 RAG 总结

只有 summary 走 RAG, content_scores / overall 不变 —— 评估的"数值/合规"
部分不能被语义召回影响, 不变量在 Sprint 1-5 eval 里守住。
"""
from __future__ import annotations

import logging

from src import embeddings, llm, vector_store
from src.schemas import (
    Competency,
    DimensionScore,
    EvaluationReport,
    InterviewPlan,
    InterviewSession,
    PerformanceObservation,
    Question,
    Signal,
    TurnRole,
)

log = logging.getLogger(__name__)

_SUMMARY_SYSTEM = (
    "你是一名招聘委员会主席。请用 3-5 句中文综合评估候选人,"
    "指出突出表现与待观察点, 不要重复分数, 不要做录用建议。"
)

_SUMMARY_RAG_SYSTEM = (
    "你是一名招聘委员会主席。下面给出与本次职位相关的 JD 与公司资料片段,"
    "请用 3-5 句中文综合评估候选人, 在突出表现与待观察点的同时, 适当"
    "关联到岗位与公司语境(如候选人经历与岗位关键诉求的契合度)。"
    "不要重复分数, 不要做录用建议, 不要原样复述 JD/公司资料片段。"
)

_SPECIFICITY_KEYWORDS = ("例如", "比如", "当时", "结果", "我们", "用了", "选择", "%")

# 召回 JD + 公司资料的 top-K, 拼起来当 LLM 总结的上下文
_RAG_TOP_K = 4


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


def _retrieve_job_context_chunks(
    job_id: str, comps: list[Competency], candidate_excerpt: str,
) -> list[dict]:
    """召回与本次评估相关的 JD + 公司资料片段。
    embed stub / MilvusNotConfigured / 网络异常 / 召回空 一律返 [], 上游退到无 RAG 路径。"""
    query_parts = [
        f"{c.name} - {c.description}" for c in comps
    ]
    if candidate_excerpt:
        query_parts.append(candidate_excerpt[:300])
    query_text = "\n".join(query_parts)

    vec = embeddings.embed(query_text)
    if embeddings.is_stub_vector(vec):
        return []
    try:
        # 不传 kind 过滤: source_id=job_id 已经只拿到本职位的 JD + 公司资料
        # (resume 用 candidate_id 做 source_id, 天然不会被召回到)
        return vector_store.search_documents(
            embedding=vec, top_k=_RAG_TOP_K, source_id=job_id,
        )
    except vector_store.MilvusNotConfigured:
        log.info("Milvus 未配置, evaluator summary 走无 RAG 路径")
        return []
    except Exception:
        log.exception("evaluator RAG 召回失败, 走无 RAG 路径")
        return []


def _fallback_summary(
    session: InterviewSession, content_scores: list[DimensionScore],
) -> str:
    """LLM 不可用时的兜底总结, 不依赖外部上下文。"""
    avg = sum(s.score for s in content_scores) / max(len(content_scores), 1)
    return (
        f"候选人完成 {len(session.answers)} 条回答, 内容维度平均 {avg:.1f}。"
        "证据已记录在各维度下, 建议人工复核后再做决定。"
    )


def _summary(
    session: InterviewSession,
    content_scores: list[DimensionScore],
    comps: list[Competency],
    chunks: list[dict] | None = None,
) -> str:
    """生成综合总结。chunks 非空时走 RAG 路径(prompt 多带 JD/公司资料切片)。"""
    transcript = "\n".join(f"[{t.role.value}] {t.text}" for t in session.history)
    score_lines = "\n".join(
        f"- {next((c.name for c in comps if c.competency_id == s.competency_id), s.competency_id)}: {s.score:.1f}"
        for s in content_scores
    )

    if chunks:
        chunks_text = "\n---\n".join(c["text"] for c in chunks)
        user = (
            f"对话:\n{transcript}\n\n"
            f"各内容维度分数:\n{score_lines}\n\n"
            f"职位/公司相关片段(给评估提供语境):\n{chunks_text}\n\n"
            "请给出 3-5 句综合评估, 适当关联岗位与公司语境。"
        )
        system = _SUMMARY_RAG_SYSTEM
        max_tokens = 500
    else:
        user = (
            f"对话:\n{transcript}\n\n"
            f"各内容维度分数:\n{score_lines}\n\n"
            "请给出 3-5 句综合评估。"
        )
        system = _SUMMARY_SYSTEM
        max_tokens = 400

    text = llm.complete(system, user, max_tokens=max_tokens)
    if not text or llm.is_stub(text):
        return _fallback_summary(session, content_scores)
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

    # Sprint 3-7: 召回 JD + 公司资料给 summary 当语境
    candidate_excerpt = " ".join(
        t.text for t in session.history if t.role == TurnRole.CANDIDATE
    )[:600]
    rag_chunks = _retrieve_job_context_chunks(
        session.job_id, comps, candidate_excerpt,
    )
    rag_chunk_ids = [c["document_id"] for c in rag_chunks]
    summary = _summary(session, content_scores, comps, chunks=rag_chunks or None)

    return EvaluationReport(
        session_id=session.session_id,
        content_scores=content_scores,
        performance_observations=performance,
        overall=overall,
        summary=summary,
        rag_context_chunk_ids=rag_chunk_ids,
        needs_human_review=True,
    )
