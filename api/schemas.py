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

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    """POST /jobs 请求体: server 自己生成 job_id, 客户端不传。"""
    title: str = Field(..., min_length=1, description="职位标题")
    jd: str = Field(..., min_length=1, description="职位描述原文 JD")
    requirements: list[str] = Field(default_factory=list, description="岗位要求列表")
    company_materials: str = Field(default="", description="公司资料(后期做 RAG 切片)")


class CandidateCreate(BaseModel):
    """POST /jobs/{job_id}/candidates 请求体。
    job_id 走 path param, candidate_id 由 server 生成, 都不在 body 里。"""
    resume: str = Field(..., min_length=1, description="Resume 原文")
    projects: list[str] = Field(
        default_factory=list,
        description="已识别的项目/实习要点(可由 resume 解析填充)",
    )


class CandidateCreated(BaseModel):
    """POST /jobs/{job_id}/candidates 响应: 202 Accepted。
    plan_pending=True 表示 Planner 在后台跑, 客户端轮询 GET .../plan。"""
    candidate_id: str
    job_id: str
    plan_pending: bool = True


class LoginRequest(BaseModel):
    """POST /auth/login 请求体。"""
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    """POST /auth/login 响应体, 兼容 OAuth2 Bearer 约定。
    expires_in 单位是秒, 让前端 SDK 直接拿去算续约时机。"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int                                  # 秒
    role: str                                        # 让前端不用再解 JWT 就能切 UI


class InterviewStart(BaseModel):
    """POST /interviews 请求体: 由 candidate_id 推出 job + plan, 客户端只传 candidate_id。"""
    candidate_id: str = Field(..., min_length=1)


class AnswerSubmit(BaseModel):
    """POST /interviews/{session_id}/answers 请求体。"""
    text: str = Field(..., min_length=1, description="候选人回答原文")
