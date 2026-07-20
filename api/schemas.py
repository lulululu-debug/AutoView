"""API 请求/响应模型 (HTTP 边界 DTO)。

为什么不直接复用 src/schemas/:
- src/schemas/ 是 agent 间的领域契约, server 自动生成的字段(job_id /
  candidate_id / plan_id) 都带 default_factory, 客户端可以伪造覆盖。
  在 API 边界把"客户端能传什么"显式列出来, 比给领域模型加 exclude/readonly
  干净, 也避免 ORM 元数据(created_at / updated_at) 泄漏到响应体。
- 响应模型仍然用 src/schemas 里的领域模型(JobContext 等), 用 FastAPI 的
  response_model 过滤序列化, 客户端只看到约定的字段。

本文件随 sprint 增长, 一资源一个 *Create / *Update 模型即可,
不要演化成 ORM/DTO 两套大模型。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    """POST /jobs 请求体: server 自己生成 job_id, 客户端不传。
    Sprint 5.5: track 决定面试 stage 序列 + 各 stage 题数配比, 默认 lateral。
    Sprint 5.7: 可选 followup_policy / completion_policy 来自 HR 高级折叠区,
    None / 不传 = 用 stage / schema 默认。"""
    title: str = Field(..., min_length=1, description="职位标题")
    jd: str = Field(..., min_length=1, description="职位描述原文 JD")
    requirements: list[str] = Field(default_factory=list, description="岗位要求列表")
    company_materials: str = Field(default="", description="公司资料(后期做 RAG 切片)")
    track: Literal["campus", "lateral"] = Field(
        default="lateral",
        description="招聘类型: campus 校招 / lateral 社招",
    )
    # Sprint 5.9: HR 选 role_family 决定 Planner 配比 + aspect 默认模板
    role_family: Literal[
        "backend", "frontend", "data_science", "product", "hr",
    ] = Field(
        default="backend",
        description="岗位族; 决定 (track, role_family) 配比 + aspect 模板",
    )
    # Sprint H: 出题来源。rag(默认) = 题库召回+精修; llm_direct = 纯 LLM 出题。
    question_source: Literal["rag", "llm_direct"] = Field(
        default="rag",
        description="出题来源: rag 题库召回+精修 / llm_direct 纯 LLM 按维度+技能出题",
    )
    # Sprint 5.7 高级折叠区配置; 用 dict 接, src.schemas.FollowUpPolicy /
    # CompletionPolicy 在 JobContext 构造时校验, 让 api/schemas 不重复声明字段。
    followup_policy: dict | None = Field(
        default=None,
        description="覆盖 FollowUpPolicy 默认 (None=用 stage 默认)",
    )
    completion_policy: dict | None = Field(
        default=None,
        description="覆盖 CompletionPolicy 默认 (None=用 schema 默认)",
    )
    # Sprint 5.9: HR 在 UI 上 (基于 aspects-template 默认) 增删改的 aspect 列表;
    # 空时 Planner 走 default_aspects_for(role_family) 兜底。
    aspects: list[dict] | None = Field(
        default=None,
        description="本岗位画像 aspect 列表; 空时用 role_family 默认模板",
    )


class ResumeSectionIn(BaseModel):
    """简历语义分段 (API 边界版, Sprint F Phase 2)。
    与 src.schemas.ResumeSection 同形但独立定义: source 不接受客户端传
    (server 端强制标 user_confirmed), 见本文件头"为什么不直接复用"。"""
    type: str = Field(default="other", max_length=32)
    title: str = Field(default="", max_length=80)
    text: str = Field(..., min_length=1)


class CandidateCreate(BaseModel):
    """POST /jobs/{job_id}/candidates 请求体。
    job_id 走 path param, candidate_id 由 server 生成, 都不在 body 里。
    Sprint F Phase 2: sections 可选 —— 前端把 parse-resume 返回的分段给
    候选人确认/编辑后原样提交; 提交了 sections 则后台跳过重新分段
    (候选人确认的结果优先于机器切分)。不传 = 旧纯文本流程。"""
    resume: str = Field(..., min_length=1, description="Resume 原文")
    projects: list[str] = Field(
        default_factory=list,
        description="已识别的项目/实习要点(可由 resume 解析填充)",
    )
    sections: list[ResumeSectionIn] = Field(
        default_factory=list,
        description="候选人确认后的简历语义分段 (可选)",
    )


class CandidateCreated(BaseModel):
    """POST /jobs/{job_id}/candidates 响应: 202 Accepted。
    plan_pending=True 表示 Planner 在后台跑, 客户端轮询 GET .../plan。"""
    candidate_id: str
    job_id: str
    plan_pending: bool = True


class ResumeSectionOut(BaseModel):
    """parse-resume 返回的单个分段 (Sprint F Phase 2)。
    source 透传给前端作提示 (llm_anchor 切的可提示"AI 分段, 请检查";
    whole_text 表示没切出来, 前端退纯文本编辑)。"""
    type: str
    title: str
    text: str
    source: str


class ParsedResume(BaseModel):
    """POST /jobs/{job_id}/candidates/parse-resume 响应 (Sprint 5.8)。
    parsed_text 是用户后续可编辑的字符串, 由前端填回 textarea, 再走旧
    POST .../candidates {resume: text} 提交。
    Sprint F Phase 2: 同时返回 sections 分段, 前端渲染成分段编辑器让
    候选人确认; 老前端忽略该字段零成本兼容。"""
    parsed_text: str
    sections: list[ResumeSectionOut] = Field(default_factory=list)


class LoginRequest(BaseModel):
    """POST /auth/login 请求体。"""
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    """POST /auth/login 响应体, 兼容 OAuth2 Bearer 约定。
    expires_in 单位是秒, 让前端 SDK 直接拿去算续约时机。

    Sprint 5.8: 同时 Set-Cookie httpOnly cookie 在响应里; body 仍含 access_token
    让 evals + 脚本走 Bearer 不变。浏览器 HR 端从 5.8 起改读 cookie。"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int                                  # 秒
    role: str                                        # 让前端不用再解 JWT 就能切 UI


class UserMe(BaseModel):
    """GET /auth/me 响应 (Sprint 5.8): HrGuard 用来判 session 仍有效 +
    UI 展示当前登录用户名。"""
    user_id: str
    username: str
    role: str


# ---------- HR Dashboard (Sprint 5-2) ----------

class CandidateWithStatus(BaseModel):
    """GET /hr/jobs/{id}/candidates 单个候选人条目。
    resume_excerpt 是 200 字截断, 详情进 GET /jobs/{id}/candidates/{cid} 看。"""
    candidate_id: str
    job_id: str
    resume_excerpt: str
    status: str                              # plan_pending / ready / completed / reviewed
    session_id: str | None = None            # 走完 finalize 后才有
    report_id: str | None = None             # status >= completed 时有
    review_decision: str | None = None       # status == reviewed 时有
    created_at: datetime


class ResumeChunk(BaseModel):
    """GET /hr/candidates/{cid}/resume-chunks 单个切片 —— Sprint E 出题过程视图。
    project 题的 source_chunk_ids 指向这里的 document_id, HR 可对照看
    「哪段简历内容催生了哪道深挖题」。"""
    document_id: str
    chunk_index: int
    text: str


class ReviewSubmit(BaseModel):
    """PATCH /hr/reports/{id}/review 请求体。
    reviewer_id 由 server 从 JWT 取, 客户端不传; record_id 也由 server 生成。"""
    comments: str = ""
    dimension_overrides: list[dict] = Field(default_factory=list)
    decision: str = Field(..., min_length=1)
    # decision 用 str 而非 Literal: 在 schema 层不引入 enum 重复定义,
    # 由后端 route 校验在 ReviewDecision 合法值内


class InterviewStart(BaseModel):
    """POST /interviews 请求体: 由 candidate_id 推出 job + plan, 客户端只传 candidate_id。"""
    candidate_id: str = Field(..., min_length=1)


class AnswerSubmit(BaseModel):
    """POST /interviews/{session_id}/answers 请求体。"""
    text: str = Field(..., min_length=1, description="候选人回答原文")


# ---------- Admin: 知识库审核 (Sprint C) ----------

class DatasetSummary(BaseModel):
    """GET /admin/datasets 单条目。"""
    dataset_id: str
    n_chunks: int
    n_pending: int
    n_approved: int
    n_rejected: int
    n_seed: int
    # Sprint D-lite: datasets 元数据 (LEFT JOIN, 老数据缺元数据时为空串)
    topic: str = ""
    description: str = ""
    source_repo: str = ""
    source_commit: str = ""
    # Sprint upload: knowledge / scenario
    category: str = "knowledge"


class ChunkWithDraftStats(BaseModel):
    """GET /admin/datasets/{ds}/chunks 单条目。审核入口列表。"""
    chunk_id: str
    file_path: str
    heading_path: list[str]
    quality_tag: str
    is_starred: bool
    char_count: int
    n_pending: int
    n_approved: int
    n_rejected: int


class ChunkWithDraftsResponse(BaseModel):
    """GET /admin/chunks/{cid}/drafts: chunk 上下文 + 该 chunk 全部 drafts。
    chunk / drafts 类型直接用 src.schemas 的领域模型, FastAPI 自动序列化。"""
    chunk: dict     # KnowledgeChunk
    drafts: list[dict]  # list[QuestionDraft]


class EditDraftRequest(BaseModel):
    """PATCH /admin/drafts/{did} 请求体; None 字段不动。"""
    question_text: str | None = None
    key_points: list[str] | None = None


class ApproveDraftRequest(BaseModel):
    """POST /admin/drafts/{did}/approve / POST /admin/chunks/{cid}/bulk-approve.
    competency_id 必填; role_family 默认 backend (JavaGuide 全是 backend, 其他
    数据集 HR UI 应主动传)."""
    competency_id: Literal["comp:tech", "comp:comm"]
    role_family: str = "backend"


class MediaConfig(BaseModel):
    """GET /media/config (Sprint 6-4/6-5): 部署级媒体能力探测。
    前端据此决定显示麦克风入口 (stt) / 是否拉 turn 音频 (tts) /
    是否启动 MediaRecorder 录制 (recording) + consent 文案说"会不会录"。
    只反映配置, 不保证厂商可用 —— 运行期失败仍由各自的降级路径兜住。"""
    stt_enabled: bool
    tts_enabled: bool
    recording_enabled: bool
