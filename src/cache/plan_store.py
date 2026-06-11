"""InterviewPlan 在 Redis 的读写。

为什么 plan 也进 Redis:
- 会话进行中, Interviewer 每次 next_turn 都需要 plan(取题、判断当前题
  是不是已经追问过)。把 plan 放在 Redis 与 session 并排, 避免每次都
  让 Planner 重跑一遍 LLM(那既慢又不确定)。
- TTL 与 session_store 共用 ttl_seconds(), 两者同生共死, 避免出现
  session 还在、plan 已过期 这种破窗。
- finalize 时由 orchestrator 显式 delete_plan, 不靠 TTL 兜底。

为什么不进 Postgres(Sprint 1 范围内):
- 题目原文已经写进 session.history(transcript), 归档后审计够用。
- plan 的 Competency/权重等结构性数据如需长期保留, 在 Sprint 2/3
  接 API 与 RAG 时再设独立表。
"""
from __future__ import annotations

from typing import Optional

from src.cache.base import get_redis, ttl_seconds
from src.schemas import InterviewPlan

_KEY_PREFIX = "plan:"


def _key(plan_id: str) -> str:
    return f"{_KEY_PREFIX}{plan_id}"


def save_plan(plan: InterviewPlan) -> None:
    """写入(或覆盖) plan, 刷新 TTL。"""
    r = get_redis()
    r.set(_key(plan.plan_id), plan.model_dump_json(), ex=ttl_seconds())


def load_plan(plan_id: str) -> Optional[InterviewPlan]:
    r = get_redis()
    raw = r.get(_key(plan_id))
    if raw is None:
        return None
    return InterviewPlan.model_validate_json(raw)


def delete_plan(plan_id: str) -> None:
    r = get_redis()
    r.delete(_key(plan_id))
