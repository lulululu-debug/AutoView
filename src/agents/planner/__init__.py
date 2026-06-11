"""Planner Agent — 根据 JobContext + CandidateProfile 生成 InterviewPlan。

骨架阶段固定输出 1 轮 / 2 维度 / 4 题:
- 每个考察维度配 1 道基础知识题(JD 驱动) + 1 道项目深挖题(Resume 驱动)
- 题目文本由 LLM 润色; LLM 不可用时回退到与 JD/Resume 紧扣的模板

题目顺序遵循面试节奏: 先两道基础知识做铺垫, 再两道项目/实习深挖。
"""
from __future__ import annotations

from src import llm
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

_KNOWLEDGE_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位与考察维度, 生成一道开放式、可深挖、贴合岗位的中文【基础知识】面试题。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

_PROJECT_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位、考察维度与候选人简历, 生成一道针对候选人具体项目/实习经历的中文深挖题。"
    "题目必须指向简历里的具体内容(项目、技术栈、角色或结果), 不要泛泛而问。"
    "只输出题目本身, 不要任何解释或前后缀。"
)


def _knowledge_question(job: JobContext, comp: Competency, fallback: str) -> str:
    prompt = (
        f"职位: {job.title}\n"
        f"JD: {job.jd[:400]}\n"
        f"考察维度: {comp.name} — {comp.description}\n"
        "请生成一道用于该维度的【基础知识】开放式面试题。"
    )
    text = llm.complete(_KNOWLEDGE_SYSTEM, prompt, max_tokens=200)
    if not text or llm.is_stub(text):
        return fallback
    return text


def _project_question(
    job: JobContext, candidate: CandidateProfile, comp: Competency, fallback: str,
) -> str:
    projects_hint = "\n".join(f"- {p}" for p in candidate.projects) if candidate.projects else "(未结构化, 直接读 resume 原文)"
    prompt = (
        f"职位: {job.title}\n"
        f"JD: {job.jd[:300]}\n"
        f"考察维度: {comp.name} — {comp.description}\n"
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
        name="沟通与协作",
        description="表达清晰度、跨职能协作经验、推动事情落地的能力",
        weight=1.0,
    )

    q_tech_knowledge = Question(
        competency_id=tech.competency_id,
        type=QuestionType.TECHNICAL,
        category=QuestionCategory.KNOWLEDGE,
        text=_knowledge_question(
            job, tech,
            fallback=f"在 {job.title} 这个岗位上, 你认为最关键的技术权衡是什么? 举一个你做过的取舍来说明。",
        ),
    )
    q_comm_knowledge = Question(
        competency_id=comm.competency_id,
        type=QuestionType.BEHAVIORAL,
        category=QuestionCategory.KNOWLEDGE,
        text=_knowledge_question(
            job, comm,
            fallback="当你和非技术同事(产品/业务/SRE)就方案产生分歧时, 你通常如何推进?",
        ),
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
