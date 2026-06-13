"""Planner Agent — 根据 JobContext + CandidateProfile 生成 InterviewPlan。

Sprint 5.5 起按 JobContext.track 输出 stage 序列:
- campus (校招): [self_intro, knowledge x3, project x2 lazy, scenario x1]   ≈ 7 题
- lateral (社招): [self_intro, project x3 lazy, scenario x2, knowledge x1] ≈ 7 题

stage 内题目特性:
- self_intro: 固定文本, competency_id=None, 永不进 content_scores
- knowledge:  Milvus questions(category=knowledge) 召回 + LLM 精修, 复用旧路径
- project:    plan 阶段只占位 (lazy=True, text=""), 进 project stage 时由
              `resolve_lazy_questions` 用 Resume RAG (+ session.intro_text, task 4)
              现场回灌 text + source_chunk_ids; competency 槽位在 plan 阶段就预定,
              生成只换内容不换 competency_id
- scenario:   Milvus questions(category=scenario) 召回 + LLM 精修, 与 knowledge 同形
              但拉的是 scenario 题库

Plan 顶层 `plan.competencies` 是权威列表 (跨 stage 共享, Evaluator 走顶层);
round.competencies 保留为该 stage 涉及的子集, 供 HR 阶段视图展示。

Sprint 0/3 的"2 dim × 2 cat × 4 题"路径 Sprint 5.5 起覆盖式退役, 无 fallback,
让数据契约只有一条真实路径。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src import embeddings, llm, vector_store
from src.schemas import (
    CandidateProfile,
    Competency,
    InterviewPlan,
    InterviewRound,
    InterviewStage,
    JobContext,
    Question,
    QuestionCategory,
    QuestionType,
    Track,
)

log = logging.getLogger(__name__)

_KNOWLEDGE_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位与考察维度, 生成一道开放式、可深挖、贴合岗位的中文【基础知识】面试题。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

_KNOWLEDGE_RAG_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "下面给出一道从题库中召回的候选题, 请基于该题目, 必要时小幅改写让题目更贴合"
    "本次职位的 JD 与考察维度。改写应保留原题的考察意图, 不要彻底换题。"
    "只输出最终题目本身, 不要任何解释或前后缀。"
)

_SCENARIO_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位与考察维度, 生成一道开放式中文【场景题】(给一个具体情境, "
    "让候选人现场推理决策, 而不是回顾经历)。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

_SCENARIO_RAG_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "下面给出一道从场景题库中召回的候选题, 请基于该题目, 必要时小幅改写让情境更"
    "贴合本次职位与考察维度。改写应保留原题的情境结构 (具体场景 + 问候选人怎么做),"
    "不要把它改回知识题或经历题。"
    "只输出最终题目本身, 不要任何解释或前后缀。"
)

_PROJECT_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位、考察维度与候选人简历, 生成一道针对候选人具体项目/实习经历的中文深挖题。"
    "题目必须指向简历里的具体内容(项目、技术栈、角色或结果), 不要泛泛而问。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

_PROJECT_RAG_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "下面给出从候选人 Resume 中召回的若干相关片段。"
    "请围绕这些具体内容生成一道针对候选人项目/实习经历的中文深挖题。"
    "题目必须指向片段中的具体项目、技术栈、角色或结果, 不要泛泛而问, 也不要重复片段原文。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

# RAG 召回的候选题数量。当前只取 top-1 给 LLM 精修, 多召回纯为日后做
# diversity / 多轮选题留扩展位; 取 3 是个折中, 也方便日志里看到 runner-up。
_RAG_TOP_K = 3

_SELF_INTRO_TEXT = (
    "请用 2-3 分钟做个自我介绍, 重点讲你最近一段经历里你做的关键决策、"
    "你遇到的最大挑战, 以及最终的结果。"
)


# ---------- stage 配比 (硬编码; Sprint 5.7 再开放 HR 配置) ----------

@dataclass(frozen=True)
class _StageSlot:
    """单 stage 内一个题目槽位: 哪个 competency, 走哪类题源。"""
    competency_key: str | None    # "tech" / "comm" / None (self_intro)
    category: QuestionCategory


# campus (校招): self_intro 1 + knowledge 3 + project 2 lazy + scenario 1
#   knowledge 2 tech + 1 comm; project 1 tech + 1 comm; scenario 1 tech
_CAMPUS_STAGES: list[tuple[InterviewStage, list[_StageSlot]]] = [
    (InterviewStage.SELF_INTRO, [
        _StageSlot(None, QuestionCategory.SELF_INTRO),
    ]),
    (InterviewStage.KNOWLEDGE, [
        _StageSlot("tech", QuestionCategory.KNOWLEDGE),
        _StageSlot("tech", QuestionCategory.KNOWLEDGE),
        _StageSlot("comm", QuestionCategory.KNOWLEDGE),
    ]),
    (InterviewStage.PROJECT, [
        _StageSlot("tech", QuestionCategory.PROJECT_EXPERIENCE),
        _StageSlot("comm", QuestionCategory.PROJECT_EXPERIENCE),
    ]),
    (InterviewStage.SCENARIO, [
        _StageSlot("tech", QuestionCategory.SCENARIO),
    ]),
]

# lateral (社招): self_intro 1 + project 3 lazy + scenario 2 + knowledge 1
#   project 2 tech + 1 comm; scenario 1 tech + 1 comm; knowledge 1 tech
_LATERAL_STAGES: list[tuple[InterviewStage, list[_StageSlot]]] = [
    (InterviewStage.SELF_INTRO, [
        _StageSlot(None, QuestionCategory.SELF_INTRO),
    ]),
    (InterviewStage.PROJECT, [
        _StageSlot("tech", QuestionCategory.PROJECT_EXPERIENCE),
        _StageSlot("tech", QuestionCategory.PROJECT_EXPERIENCE),
        _StageSlot("comm", QuestionCategory.PROJECT_EXPERIENCE),
    ]),
    (InterviewStage.SCENARIO, [
        _StageSlot("tech", QuestionCategory.SCENARIO),
        _StageSlot("comm", QuestionCategory.SCENARIO),
    ]),
    (InterviewStage.KNOWLEDGE, [
        _StageSlot("tech", QuestionCategory.KNOWLEDGE),
    ]),
]

_STAGE_TITLES: dict[InterviewStage, str] = {
    InterviewStage.SELF_INTRO: "自我介绍",
    InterviewStage.KNOWLEDGE: "基础知识",
    InterviewStage.PROJECT: "项目深挖",
    InterviewStage.SCENARIO: "场景题",
}


def _stages_for_track(track: Track) -> list[tuple[InterviewStage, list[_StageSlot]]]:
    if track is Track.CAMPUS:
        return _CAMPUS_STAGES
    return _LATERAL_STAGES


# ---------- knowledge: 召回 + 精修 (复用 Sprint 3 路径) ----------

def _retrieve_seed_question(
    role_family: str,
    competency: Competency,
    jd_excerpt: str,
    *,
    category: QuestionCategory,
) -> dict | None:
    """从 Milvus 召回 top-1 候选题, 失败 / 空时返 None。
    Sprint 5.5: category 让 knowledge / scenario 各拉各的题源。"""
    query_text = (
        f"考察维度: {competency.name} - {competency.description}\n"
        f"JD 摘要: {jd_excerpt}"
    )
    vec = embeddings.embed(query_text)
    if embeddings.is_stub_vector(vec):
        return None
    try:
        hits = vector_store.search_questions(
            embedding=vec,
            top_k=_RAG_TOP_K,
            role_family=role_family,
            competency=competency.name,
            category=category.value,
        )
    except vector_store.MilvusNotConfigured:
        log.info("Milvus 未配置, %s 题走 fallback 路径", category.value)
        return None
    except Exception:
        # 网络抖动 / Milvus 报错 / schema drift 都不应让面试卡死, 静默退到现场生成
        log.exception("%s 召回失败, 走 fallback", category.value)
        return None
    if not hits:
        return None
    return hits[0]


def _knowledge_question(
    job: JobContext, comp: Competency, fallback: str,
) -> tuple[str, str | None]:
    """生成一道 knowledge 题。
    返回 (题目文本, source_question_id 或 None)。

    路径优先级:
    1. RAG 召回 + LLM 精修: 题库有题 + embed/Milvus/LLM 都正常
    2. RAG 召回 + 直接复用: 题库有题, LLM stub 时, 用候选题原文 (仍有 source_question_id)
    3. 纯 LLM 生成: 题库无题, LLM 正常 (无 source_question_id)
    4. fallback 模板: LLM 也 stub (无 source_question_id)
    """
    jd_excerpt = job.jd[:400]
    hit = _retrieve_seed_question(
        job.role_family, comp, jd_excerpt, category=QuestionCategory.KNOWLEDGE,
    )

    if hit is not None:
        source_id = hit["question_id"]
        seed_text = hit["text"]
        prompt = (
            f"候选题目(题库召回): {seed_text}\n"
            f"职位: {job.title}\n"
            f"JD 摘要: {jd_excerpt}\n"
            f"考察维度: {comp.name} - {comp.description}\n"
            "请基于候选题目, 必要时小幅改写让题目更聚焦本职位。"
        )
        adapted = llm.complete(_KNOWLEDGE_RAG_SYSTEM, prompt, max_tokens=240)
        if adapted and not llm.is_stub(adapted):
            return adapted, source_id
        return seed_text, source_id

    prompt = (
        f"职位: {job.title}\n"
        f"JD: {jd_excerpt}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        "请生成一道用于该维度的【基础知识】开放式面试题。"
    )
    text = llm.complete(_KNOWLEDGE_SYSTEM, prompt, max_tokens=200)
    if not text or llm.is_stub(text):
        return fallback, None
    return text, None


# ---------- scenario: 召回 + 精修 (Sprint 5.5 新加) ----------

def _scenario_question(
    job: JobContext, comp: Competency, fallback: str,
) -> tuple[str, str | None]:
    """生成一道 scenario 题。
    与 _knowledge_question 同形, 只是题源走 category=scenario 召回 +
    场景题专用 LLM prompt。
    """
    jd_excerpt = job.jd[:400]
    hit = _retrieve_seed_question(
        job.role_family, comp, jd_excerpt, category=QuestionCategory.SCENARIO,
    )

    if hit is not None:
        source_id = hit["question_id"]
        seed_text = hit["text"]
        prompt = (
            f"候选题目(场景题库召回): {seed_text}\n"
            f"职位: {job.title}\n"
            f"JD 摘要: {jd_excerpt}\n"
            f"考察维度: {comp.name} - {comp.description}\n"
            "请基于候选题目, 必要时小幅改写让情境更贴合本职位。保持场景题结构。"
        )
        adapted = llm.complete(_SCENARIO_RAG_SYSTEM, prompt, max_tokens=240)
        if adapted and not llm.is_stub(adapted):
            return adapted, source_id
        return seed_text, source_id

    prompt = (
        f"职位: {job.title}\n"
        f"JD: {jd_excerpt}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        "请生成一道用于该维度的【场景题】(给具体情境, 让候选人现场决策, 不要回顾经历)。"
    )
    text = llm.complete(_SCENARIO_SYSTEM, prompt, max_tokens=240)
    if not text or llm.is_stub(text):
        return fallback, None
    return text, None


# ---------- project: lazy 占位 + resolve_lazy 回灌 ----------

def _retrieve_resume_chunks(
    candidate_id: str, competency: Competency,
) -> list[dict]:
    """从 Milvus 召回候选人 Resume 中与本维度相关的切片。"""
    query_text = f"考察维度: {competency.name} - {competency.description}"
    vec = embeddings.embed(query_text)
    if embeddings.is_stub_vector(vec):
        return []
    try:
        return vector_store.search_documents(
            embedding=vec,
            top_k=_RAG_TOP_K,
            kind=vector_store.DOC_KIND_RESUME,
            source_id=candidate_id,
        )
    except vector_store.MilvusNotConfigured:
        log.info("Milvus 未配置, project 题走 fallback 路径")
        return []
    except Exception:
        log.exception("project Resume 召回失败, 走 fallback")
        return []


def _project_question(
    job: JobContext,
    candidate: CandidateProfile,
    comp: Competency,
    fallback: str,
    *,
    intro_text: str = "",
) -> tuple[str, list[str]]:
    """生成一道项目深挖题。
    Sprint 5.5: intro_text 是 task 4 才真正传入的候选人自我介绍全文,
    task 3 阶段默认空串 ——  prompt 加 intro_text 段落给 LLM 看, 但不强依赖。"""
    chunks = _retrieve_resume_chunks(candidate.candidate_id, comp)

    intro_block = (
        f"候选人自我介绍:\n{intro_text}\n"
        if intro_text.strip()
        else ""
    )

    if chunks:
        chunk_ids = [c["document_id"] for c in chunks]
        chunks_text = "\n---\n".join(c["text"] for c in chunks)
        prompt = (
            f"职位: {job.title}\n"
            f"JD: {job.jd[:300]}\n"
            f"考察维度: {comp.name} - {comp.description}\n"
            f"{intro_block}"
            f"候选人 Resume 相关片段:\n{chunks_text}\n"
            "请围绕这些具体内容生成一道项目深挖题。"
        )
        text = llm.complete(_PROJECT_RAG_SYSTEM, prompt, max_tokens=260)
        if text and not llm.is_stub(text):
            return text, chunk_ids
        return fallback, chunk_ids

    projects_hint = (
        "\n".join(f"- {p}" for p in candidate.projects)
        if candidate.projects else "(未结构化, 直接读 resume 原文)"
    )
    prompt = (
        f"职位: {job.title}\n"
        f"JD: {job.jd[:300]}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        f"{intro_block}"
        f"候选人简历摘要:\n{candidate.resume[:800]}\n"
        f"候选人已识别项目要点:\n{projects_hint}\n"
        "请围绕该考察维度, 生成一道针对其具体项目/实习经历的深挖题。"
    )
    text = llm.complete(_PROJECT_SYSTEM, prompt, max_tokens=220)
    if not text or llm.is_stub(text):
        return fallback, []
    return text, []


def _project_fallback(comp: Competency) -> str:
    """Project stage 最末 fallback —— 用维度别名挑模板, 避免空文本上线。"""
    if "技术" in comp.name or "深度" in comp.name:
        return (
            "请挑你简历里最有挑战的一段技术工作, 讲清楚你的角色、"
            "做的关键决策, 以及最终的结果与复盘。"
        )
    return (
        "请挑你简历里一次跨职能协作的经历, 讲清楚冲突点、"
        "你如何推动对齐, 以及最终是否落地。"
    )


# ---------- 主入口: plan + resolve_lazy ----------

def _build_competencies() -> tuple[Competency, Competency]:
    """plan.competencies 顶层用的两个维度。
    weight 反映期望 overall 加权: 技术深度 > 沟通协作。"""
    tech = Competency(
        name="技术深度",
        description="对岗位核心技术栈的理解深度与实践经验",
        weight=2.0,
    )
    comm = Competency(
        name="沟通协作",
        description="表达清晰度、跨职能协作经验、推动事情落地的能力",
        weight=1.0,
    )
    return tech, comm


def plan(job: JobContext, candidate: CandidateProfile) -> InterviewPlan:
    """Planner 入口: 按 job.track 出 stage 序列。
    knowledge / scenario 题 plan 阶段就生成;
    project 题在 plan 阶段只放 lazy 占位 (text=""), 由 resolve_lazy_questions
    在进入 project stage 时回灌。"""
    tech, comm = _build_competencies()
    comp_by_key = {"tech": tech, "comm": comm}

    rounds: list[InterviewRound] = []
    for idx, (stage, slots) in enumerate(_stages_for_track(job.track)):
        questions: list[Question] = []
        round_comps: list[Competency] = []
        for slot in slots:
            comp = comp_by_key.get(slot.competency_key) if slot.competency_key else None
            q = _build_question_for_slot(job, candidate, slot, comp)
            questions.append(q)
            if comp is not None and comp not in round_comps:
                round_comps.append(comp)
        rounds.append(InterviewRound(
            index=idx,
            title=_STAGE_TITLES[stage],
            stage=stage,
            competencies=round_comps,
            questions=questions,
        ))

    return InterviewPlan(
        job_id=job.job_id,
        rounds=rounds,
        competencies=[tech, comm],
    )


def _build_question_for_slot(
    job: JobContext,
    candidate: CandidateProfile,
    slot: _StageSlot,
    comp: Competency | None,
) -> Question:
    """按槽位生成一道题。project 题永远占位, knowledge/scenario 直接生成,
    self_intro 用固定文本。"""
    cat = slot.category

    if cat is QuestionCategory.SELF_INTRO:
        return Question(
            competency_id=None,
            text=_SELF_INTRO_TEXT,
            type=QuestionType.OPEN,
            category=cat,
        )

    if cat is QuestionCategory.KNOWLEDGE:
        assert comp is not None, "knowledge 题必须挂 competency"
        text, source_id = _knowledge_question(
            job, comp,
            fallback=_knowledge_fallback(comp),
        )
        return Question(
            competency_id=comp.competency_id,
            text=text,
            type=_question_type_for(comp),
            category=cat,
            source_question_id=source_id,
        )

    if cat is QuestionCategory.SCENARIO:
        assert comp is not None, "scenario 题必须挂 competency"
        text, source_id = _scenario_question(
            job, comp,
            fallback=_scenario_fallback(comp),
        )
        return Question(
            competency_id=comp.competency_id,
            text=text,
            type=QuestionType.SITUATIONAL,
            category=cat,
            source_question_id=source_id,
        )

    if cat is QuestionCategory.PROJECT_EXPERIENCE:
        assert comp is not None, "project 题必须挂 competency"
        # lazy 占位: text 空, 进 stage 时 resolve_lazy_questions 回灌
        return Question(
            competency_id=comp.competency_id,
            text="",
            type=_question_type_for(comp),
            category=cat,
            lazy=True,
        )

    raise AssertionError(f"未知 category: {cat}")


def _question_type_for(comp: Competency) -> QuestionType:
    return (
        QuestionType.TECHNICAL
        if "技术" in comp.name or "深度" in comp.name
        else QuestionType.BEHAVIORAL
    )


def _knowledge_fallback(comp: Competency) -> str:
    if "技术" in comp.name or "深度" in comp.name:
        return (
            "在你做过的系统里, 你认为最关键的技术权衡是什么? "
            "举一个你做过的取舍来说明。"
        )
    return (
        "当你和非技术同事(产品/业务/SRE)就方案产生分歧时, 你通常如何推进?"
    )


def _scenario_fallback(comp: Competency) -> str:
    if "技术" in comp.name or "深度" in comp.name:
        return (
            "你是核心服务的 oncall, 凌晨 3 点收到 P99 告警从 200ms 涨到 4s, "
            "业务量没显著变化。你的前 10 分钟做什么? 为什么按这个顺序?"
        )
    return (
        "线上 incident 进行中, SRE / 业务 PM / 运营三方都在群里追问 ETA, "
        "你刚定位到根因还没修。接下来 15 分钟你怎么沟通? 给谁什么信息?"
    )


def resolve_lazy_questions(
    plan: InterviewPlan,
    job: JobContext,
    candidate: CandidateProfile,
    *,
    intro_text: str = "",
) -> InterviewPlan:
    """回灌 plan 里所有 lazy 且未生成 (text=="") 的 project 题。
    Sprint 5.5 task 3: 简单沿用 _project_question 现有 RAG 路径;
    Sprint 5.5 task 4: intro_text 由 Orchestrator 在 project stage 入口传入,
    让生成的题真正反映候选人自我介绍内容。

    lazy 字段不被回写: 生成后 lazy 仍 True 作 HR 审计 (这题是 lazy 来的),
    判"已生成"用 text != ""。
    返回新的 InterviewPlan (model immutable, 通过重建)。"""
    comp_by_id = {c.competency_id: c for c in plan.competencies}
    new_rounds: list[InterviewRound] = []
    touched = 0

    for r in plan.rounds:
        new_qs: list[Question] = []
        for q in r.questions:
            if not q.lazy or q.text:
                new_qs.append(q)
                continue
            if q.category is not QuestionCategory.PROJECT_EXPERIENCE:
                # task 3 只 resolve project 题; 别类 lazy 留给未来扩展
                new_qs.append(q)
                continue
            comp = comp_by_id.get(q.competency_id) if q.competency_id else None
            if comp is None:
                log.warning(
                    "lazy project 题 %s competency_id 找不到, 跳过", q.question_id,
                )
                new_qs.append(q)
                continue
            text, chunk_ids = _project_question(
                job, candidate, comp,
                fallback=_project_fallback(comp),
                intro_text=intro_text,
            )
            new_qs.append(q.model_copy(update={
                "text": text,
                "source_chunk_ids": chunk_ids,
                # lazy 故意不动 —— 静态信号, 保留作审计
            }))
            touched += 1
        new_rounds.append(r.model_copy(update={"questions": new_qs}))

    log.info("resolve_lazy_questions: 回灌 %d 道 project 题", touched)
    return plan.model_copy(update={"rounds": new_rounds})
