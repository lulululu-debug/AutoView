"""Planner Agent — 根据 JobContext + CandidateProfile 生成 InterviewPlan。

骨架阶段固定输出 1 轮 / 2 维度 / 4 题:
- 每个考察维度配 1 道基础知识题(JD 驱动) + 1 道项目深挖题(Resume 驱动)
- 题目顺序遵循面试节奏: 先两道基础知识做铺垫, 再两道项目/实习深挖。

Sprint 3-5 起 knowledge 题走"题库召回 + LLM 精修":
- embed(维度描述 + JD 摘要) -> Milvus questions collection 按 role_family + competency 过滤
- 取 top-K 候选, 让 LLM 选最贴合并小幅改写
- Question.source_question_id 记录原题 id, 可追溯到题库
- 多重 fallback: 无 Milvus / 召回空 / LLM stub -> 退到原现场生成路径

Sprint 3-6 起 project 题会改成走 Resume RAG, 当前仍是现场 LLM 生成。
"""
from __future__ import annotations

import logging

from src import embeddings, llm, vector_store
from src.schemas import (
    CandidateProfile,
    Competency,
    InterviewPlan,
    InterviewRound,
    JobContext,
    Question,
    QuestionCategory,
    QuestionType,
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

_PROJECT_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位、考察维度与候选人简历, 生成一道针对候选人具体项目/实习经历的中文深挖题。"
    "题目必须指向简历里的具体内容(项目、技术栈、角色或结果), 不要泛泛而问。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

# RAG 召回的候选题数量。当前只取 top-1 给 LLM 精修, 多召回纯为日后做
# diversity / 多轮选题留扩展位; 取 3 是个折中, 也方便日志里看到 runner-up。
_RAG_TOP_K = 3


def _retrieve_seed_question(
    role_family: str, competency: Competency, jd_excerpt: str,
) -> dict | None:
    """从 Milvus 召回 top-1 候选题, 失败 / 空时返 None。
    返回 dict 含 question_id / text / role_family / competency 等字段。"""
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
        )
    except vector_store.MilvusNotConfigured:
        log.info("Milvus 未配置, knowledge 题走 fallback 路径")
        return None
    except Exception:
        # 网络抖动 / Milvus 报错 都不应让面试卡死, 静默退到现场生成
        log.exception("knowledge 召回失败, 走 fallback")
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
    hit = _retrieve_seed_question(job.role_family, comp, jd_excerpt)

    # 路径 1/2: 有召回, 走 RAG 精修
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
        # LLM 不可用时用候选题原文, 但仍记溯源 (路径 2)
        return seed_text, source_id

    # 路径 3/4: 无召回, 退到现场生成
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


def _project_question(
    job: JobContext, candidate: CandidateProfile, comp: Competency, fallback: str,
) -> str:
    """生成一道项目深挖题。Sprint 3-6 会改造为走 Resume RAG;
    Sprint 3-5 阶段仍是现场 LLM 生成。"""
    projects_hint = "\n".join(f"- {p}" for p in candidate.projects) if candidate.projects else "(未结构化, 直接读 resume 原文)"
    prompt = (
        f"职位: {job.title}\n"
        f"JD: {job.jd[:300]}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        f"候选人简历摘要:\n{candidate.resume[:800]}\n"
        f"候选人已识别项目要点:\n{projects_hint}\n"
        "请围绕该考察维度, 生成一道针对其具体项目/实习经历的深挖题。"
    )
    text = llm.complete(_PROJECT_SYSTEM, prompt, max_tokens=220)
    if not text or llm.is_stub(text):
        return fallback
    return text


def plan(job: JobContext, candidate: CandidateProfile) -> InterviewPlan:
    """Planner 入口: (JobContext, CandidateProfile) -> InterviewPlan。"""
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

    tech_text, tech_src = _knowledge_question(
        job, tech,
        fallback=f"在 {job.title} 这个岗位上, 你认为最关键的技术权衡是什么? 举一个你做过的取舍来说明。",
    )
    comm_text, comm_src = _knowledge_question(
        job, comm,
        fallback="当你和非技术同事(产品/业务/SRE)就方案产生分歧时, 你通常如何推进?",
    )

    q_tech_knowledge = Question(
        competency_id=tech.competency_id,
        type=QuestionType.TECHNICAL,
        category=QuestionCategory.KNOWLEDGE,
        text=tech_text,
        source_question_id=tech_src,
    )
    q_comm_knowledge = Question(
        competency_id=comm.competency_id,
        type=QuestionType.BEHAVIORAL,
        category=QuestionCategory.KNOWLEDGE,
        text=comm_text,
        source_question_id=comm_src,
    )
    q_tech_project = Question(
        competency_id=tech.competency_id,
        type=QuestionType.TECHNICAL,
        category=QuestionCategory.PROJECT_EXPERIENCE,
        text=_project_question(
            job, candidate, tech,
            fallback="请挑你简历里最有挑战的一段技术工作, 讲清楚你的角色、做的关键决策, 以及最终的结果与复盘。",
        ),
    )
    q_comm_project = Question(
        competency_id=comm.competency_id,
        type=QuestionType.BEHAVIORAL,
        category=QuestionCategory.PROJECT_EXPERIENCE,
        text=_project_question(
            job, candidate, comm,
            fallback="请挑你简历里一次跨职能协作的经历, 讲清楚冲突点、你如何推动对齐, 以及最终是否落地。",
        ),
    )

    round0 = InterviewRound(
        index=0,
        title="主面: 基础知识 + 项目/实习深挖",
        competencies=[tech, comm],
        questions=[q_tech_knowledge, q_comm_knowledge, q_tech_project, q_comm_project],
    )
    return InterviewPlan(job_id=job.job_id, rounds=[round0])
