"""
核心数据契约 —— 四个 agent 的输入输出全部基于这些类型对齐。

链路: JobContext + CandidateProfile -> Planner -> InterviewPlan
      -> Interviewer 循环(Question / CandidateAnswer / FollowUp)
      -> InterviewSession -> Evaluator -> EvaluationReport

Question.category 区分两类题:
- KNOWLEDGE          基础知识考察, 由 JobContext 驱动
- PROJECT_EXPERIENCE 项目/实习内容考察, 由 CandidateProfile.resume 驱动

Signal 为多模态扩展预留, 骨架阶段恒为空。
合规约束(见 ARCHITECTURE.md 第 7 节)在类型层面体现:
EvaluationReport 把 content_scores(内容维度) 与 performance_scores(表现维度) 分开,
软信号只进 performance_scores, 且 overall 不依赖软信号。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def _new_id() -> str:
    return uuid4().hex


# ---------- 输入: 职位 + 候选人 ----------

class JobContext(BaseModel):
    """HR 上传的原始资料, Planner 的输入之一。"""
    job_id: str = Field(default_factory=_new_id)
    title: str
    jd: str                                  # 职位描述原文
    requirements: list[str] = []             # 岗位要求(可由 jd 解析填充)
    company_materials: str = ""              # 公司资料(后期做 RAG 切片)
    role_family: str = "backend"             # Sprint 3-5: 题库召回按 role_family + 维度过滤
                                             # 当前题库只 seed 了 backend; 改职位族要先 seed 对应题库


class CandidateProfile(BaseModel):
    """候选人面试前上传的简历/资料, Planner 的输入之二。
    与 JobContext 一起决定面试计划: resume 用于生成项目/实习深挖题。

    job_id Optional 是有意为之: 走 API 路径时由 path param 注入(必填),
    走 src.main / evals 这种纯内存路径时不需要(planner 不消费 job_id),
    持久化到 PG 时若仍为 None 会被 save_candidate 显式拒绝。"""
    candidate_id: str = Field(default_factory=_new_id)
    job_id: str | None = None                # 关联职位; API 落库时必填, 见 db.save_candidate
    resume: str                              # Resume 原文(后期可结构化解析)
    projects: list[str] = []                 # 已识别的项目/实习要点(可由 resume 解析填充)


# ---------- 面试计划 ----------

class Competency(BaseModel):
    """单个考察维度。"""
    competency_id: str = Field(default_factory=_new_id)
    name: str                                # 如 "系统设计能力"
    description: str
    weight: float = 1.0                      # 维度权重(用于内容维度加权)


class QuestionType(str, Enum):
    BEHAVIORAL = "behavioral"
    TECHNICAL = "technical"
    SITUATIONAL = "situational"
    OPEN = "open"


class QuestionCategory(str, Enum):
    """题目类别 —— 与 type(题目风格) 正交, 表示"考察什么"。"""
    KNOWLEDGE = "knowledge"                  # 基础知识考察, JD 驱动
    PROJECT_EXPERIENCE = "project_experience"  # 项目/实习内容考察, Resume 驱动


class Question(BaseModel):
    question_id: str = Field(default_factory=_new_id)
    competency_id: str                       # 所属考察维度
    text: str
    type: QuestionType = QuestionType.OPEN
    category: QuestionCategory = QuestionCategory.KNOWLEDGE
    # Sprint 3-5 溯源 (knowledge 题): 从 SeedQuestion 召回 + LLM 精修时, 记录原题 id;
    # None 表示走的是 fallback / 现场生成路径, 没有题库来源。
    source_question_id: str | None = None
    # Sprint 3-6 溯源 (project 题): 从 Resume 切片召回时, 记录用到的 document_id 列表;
    # 空列表表示走的是 fallback / 现场生成路径, 没有 RAG 切片来源。
    source_chunk_ids: list[str] = []


class InterviewRound(BaseModel):
    """一轮面试: 一组维度与对应题目。"""
    round_id: str = Field(default_factory=_new_id)
    index: int                               # 第几轮(从 0 开始)
    title: str
    competencies: list[Competency]
    questions: list[Question]


class InterviewPlan(BaseModel):
    """Planner 的输出, Interviewer 的依据。"""
    plan_id: str = Field(default_factory=_new_id)
    job_id: str
    rounds: list[InterviewRound]


# ---------- 面试过程 ----------

class CandidateAnswer(BaseModel):
    """候选人对某题的回答。骨架阶段仅 text;
    后期音视频通过 media_ref 引用,不改本结构。"""
    answer_id: str = Field(default_factory=_new_id)
    question_id: str
    text: str
    media_ref: str | None = None             # 后期: 音视频存储引用
    asked_at: datetime = Field(default_factory=datetime.utcnow)


class FollowUp(BaseModel):
    """Interviewer 基于回答产出的追问。"""
    followup_id: str = Field(default_factory=_new_id)
    parent_question_id: str
    text: str
    reason: str                              # 为何追问(便于审计与调试)


class TurnRole(str, Enum):
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"


class Turn(BaseModel):
    """对话历史的一个回合。"""
    role: TurnRole
    text: str
    ref_id: str | None = None                # 关联的 question/followup/answer id
    at: datetime = Field(default_factory=datetime.utcnow)


class SessionStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class InterviewSession(BaseModel):
    """一次面试的完整状态。Interviewer 读写, Evaluator 消费。
    骨架阶段在内存; Sprint 1 起热存 Redis, 结束归档 Postgres。"""
    session_id: str = Field(default_factory=_new_id)
    plan_id: str
    job_id: str
    status: SessionStatus = SessionStatus.CREATED
    current_round: int = 0
    history: list[Turn] = []
    answers: list[CandidateAnswer] = []


# ---------- 多模态信号(扩展, 骨架恒空) ----------

class SignalKind(str, Enum):
    LANGUAGE = "language"                    # 语言(基于转写文本)
    TONE = "tone"                            # 语气/韵律(基于音频)
    GAZE = "gaze"                            # 视线/表情(基于视频)


class Signal(BaseModel):
    """多模态软信号。仅作为参考证据, 带置信度。
    合规约束: 绝不进入 overall 计算, 只出现在 performance_scores。"""
    kind: SignalKind
    value: str                               # 描述性, 非分数
    confidence: float                        # 0~1
    source: str                              # 来源说明(便于审计)


# ---------- 评估报告 ----------

class DimensionScore(BaseModel):
    competency_id: str
    score: float                             # 0~100
    evidence: list[str]                      # 支撑该评分的对话证据


class PerformanceObservation(BaseModel):
    """表现维度观察, 来源于软信号。与内容维度严格分离。"""
    kind: SignalKind
    observation: str
    confidence: float
    note: str = "参考信息, 不计入总分, 建议人工复核"


class SeedQuestion(BaseModel):
    """种子题库中的一道题 —— Sprint 3 起。
    Planner 按维度从题库召回 (Milvus) 后再由 LLM 精修, 替换原来的现场生成。
    PG 是真理之源, Milvus 仅作检索副本。

    question_id 用内容哈希 (sha256(role+competency+text)[:16]), 让脚本可重跑:
    同内容 = 同 id = upsert 不重复。"""
    question_id: str
    role_family: str                         # "backend" / "frontend" / "data_science" / ...
    competency: str                          # "技术深度" / "沟通协作" / ...
    text: str
    source: str = "llm_generated"            # llm_generated / fallback_template / human_curated


class TurnResult(BaseModel):
    """Orchestrator 的一次推进结果。
    start_session / submit_answer / resume_session 都返回这个,
    调用方据此决定下一步: 还要继续答(prompt 非空) 还是已结束(done=True)。"""
    session_id: str
    done: bool                               # True 表示面试已走完, 接下来该 finalize
    prompt: str | None = None                # 下一句面试官话: question 或 follow-up
    ref_id: str | None = None                # 对应 history 里 interviewer turn 的 ref_id


class EvaluationReport(BaseModel):
    """Evaluator 的输出。
    内容维度(content_scores) 与表现维度(performance_observations) 分区。
    overall 只基于 content_scores 加权, 不依赖任何软信号。"""
    report_id: str = Field(default_factory=_new_id)
    session_id: str
    content_scores: list[DimensionScore]                    # 内容维度: 进总分
    performance_observations: list[PerformanceObservation] = []  # 表现维度: 仅参考
    overall: float                                          # 仅由 content_scores 得出
    summary: str
    needs_human_review: bool = True                         # 默认需人工复核