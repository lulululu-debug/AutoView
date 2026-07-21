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
from src.coverage import compute_coverage, insufficient_competencies
from src.schemas import (
    Competency,
    CompletionPolicy,
    DimensionScore,
    EvaluationReport,
    InterviewPlan,
    InterviewSession,
    JobContext,
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

# DimensionScore.evidence 在 HR UI 渲染成列表, 太多条会刷屏 (实战遇到候选人
# 复制粘贴同一段回答 10 次的 case, 导致 evidence 同句重复 10 行). 取 top-N,
# 并按 text 去重, 是 HR 真正想看的"代表性证据"形态。
_EVIDENCE_MAX = 5


def _score_for_competency(
    comp: Competency,
    questions: list[Question],
    session: InterviewSession,
) -> DimensionScore:
    """维度分入口 —— Sprint 6.5 起双路径:
    优先 assessment 驱动 (_assessment_score); session 无 assessments
    (ASSESSOR_ENABLED=false / 老 session) 退 Sprint 0 启发式保底, 不删。"""
    ds = _assessment_score(comp, questions, session)
    if ds is not None:
        return ds
    return _heuristic_score(comp, questions, session)


def _assessment_score(
    comp: Competency,
    questions: list[Question],
    session: InterviewSession,
) -> DimensionScore | None:
    """Sprint 6.5 (sim 冒烟结案后的修复): score = 100 × mean(该维度每道题的
    best sufficiency)。

    背景: 旧启发式 (字数+关键词) 在真实长答案下饱和于 95 (base 129 字封顶 80
    + bonus 5 词封顶 15), 强弱候选人同分, 区分度全靠未答维度记 0 的加权拖拽。
    质量信号 (AnswerAssessment.sufficiency) 早已落库却没被打分消费, 本函数补上。

    规则:
    - **只对实际被问过的题求均值** (被问过 = 有 ≥1 条 assessment):
      CompletionPolicy 提前结束 / hard cap 截断是系统行为, 没被问到的题不许
      记 0 反过来罚候选人 —— 覆盖缺口由 coverage + evidence_insufficient
      表达, 质量分只衡量已收集证据的质量, 不双重计罚;
    - 同题多次 assessment (原答 + 追问后再评) 取 max —— 与 coverage 同口径;
    - 该维度有题但一道都没被问到 -> 0.0 (无证据, 与 coverage=0 一致);
    - session.assessments 全空 -> None (调用方退启发式);
    - 该维度没配题 -> None (启发式走"无答案 -> 0"老路径)。

    合规: 本分数是 sufficiency 的聚合派生量, 层级与已展示的 coverage 相同
    (Sprint 5.7 附加确认该层级可见); 裸 sufficiency 数字仍不进 UI。
    """
    if not session.assessments:
        return None
    comp_q_ids = {
        q.question_id for q in questions
        if q.competency_id == comp.competency_id
    }
    if not comp_q_ids:
        return None

    best: dict[str, float] = {}  # 只收被问过的题
    for a in session.assessments:
        if a.question_id in comp_q_ids:
            if a.sufficiency > best.get(a.question_id, -1.0):
                best[a.question_id] = a.sufficiency

    if not best:
        return DimensionScore(
            competency_id=comp.competency_id,
            score=0.0,
            evidence=["未收集到该维度的有效回答"],
        )

    score = round(100.0 * sum(best.values()) / len(best), 1)
    answers = [a for a in session.answers if a.question_id in best]
    evidence = (
        _evidence(answers) if answers else ["未收集到该维度的有效回答"]
    )
    return DimensionScore(
        competency_id=comp.competency_id, score=score, evidence=evidence,
    )


def _heuristic_score(
    comp: Competency,
    questions: list[Question],
    session: InterviewSession,
) -> DimensionScore:
    """Sprint 0 启发式 (字数 + 关键词) —— 仅作 assessments 缺失时的保底。
    已知缺陷 (sim 冒烟坐实): 真实长答案下 base/bonus 双双封顶, 分数饱和于
    95, 无区分度。不要再作为主路径使用, 也不要删 (双路径保底约定)。"""
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

    return DimensionScore(
        competency_id=comp.competency_id, score=score, evidence=_evidence(answers),
    )


def _evidence(answers: list) -> list[str]:
    """Evidence: 按 text 去重 (防复制粘贴刷屏) + 截 120 字 + 上限 _EVIDENCE_MAX,
    保留答题顺序。assessment 路径与启发式路径共用, UI 契约不变。"""
    evidence: list[str] = []
    seen: set[str] = set()
    for a in answers:
        norm = a.text.strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        evidence.append(norm[:120] + ("…" if len(norm) > 120 else ""))
        if len(evidence) >= _EVIDENCE_MAX:
            break
    return evidence


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


_EVIDENCE_INSUFFICIENT_PREFIX = "证据不充分, 建议人工面谈: "


def evaluate(
    session: InterviewSession,
    plan: InterviewPlan,
    signals: list[Signal] | None = None,
    job: JobContext | None = None,
) -> EvaluationReport:
    """Evaluator 入口: Session + Plan + (可选) Signals + (可选) JobContext
    -> EvaluationReport。

    Sprint 5.7: 计算 competency_coverage; 任一 mandatory competency 未达
    CompletionPolicy.min_competency_coverage 时, summary 前缀加
    "证据不充分, 建议人工面谈: " + needs_human_review=True (不引新字段)。
    job=None 时用 CompletionPolicy schema 默认值。"""
    signals = signals or []
    # Sprint 5.5: plan.competencies 是顶层权威列表 (跨 stage 共享去重);
    # round.competencies 退化为该 stage 的子集视图, 仅供 HR 阶段视图使用。
    # 老 plan (Sprint 5.5 之前) plan.competencies 为空时回退到 round 聚合,
    # 兼容老 session 重跑 evaluate 的场景。
    comps = list(plan.competencies) or [
        c for r in plan.rounds for c in r.competencies
    ]
    # 注: self_intro 题 competency_id=None, 自动不匹配任何 comp.competency_id,
    # 所以不会进任何 DimensionScore.evidence —— 符合设计意图。
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

    # Sprint 5.7: 计算 coverage + evidence_insufficient flag
    coverage = compute_coverage(session, plan)
    completion = (
        job.completion_policy if job and job.completion_policy is not None
        else CompletionPolicy()
    )
    insufficient = insufficient_competencies(coverage, completion, plan)
    if insufficient:
        names = _competency_names(comps, insufficient)
        summary = (
            f"{_EVIDENCE_INSUFFICIENT_PREFIX}"
            f"{', '.join(names)} 等维度证据不足。\n\n{summary}"
        )

    return EvaluationReport(
        session_id=session.session_id,
        content_scores=content_scores,
        performance_observations=performance,
        overall=overall,
        summary=summary,
        rag_context_chunk_ids=rag_chunk_ids,
        needs_human_review=True,
        competency_coverage=coverage,
    )


def _competency_names(
    comps: list[Competency], cids: list[str],
) -> list[str]:
    by_id = {c.competency_id: c.name for c in comps}
    return [by_id.get(cid, cid[:8]) for cid in cids]
