"""决策 Trace 收集器 —— Sprint 8.2。

contextvars 纯内存收集, 无 I/O: 与 src.coverage 同级的共享纯模块, agent 可
import 埋点, 不违反「agent 不碰 DB/cache」(存取 Redis/PG 只在 orchestrator)。

用法:
- orchestrator 在三段式入口 activate(trace_obj), finally 里 deactivate 并存储
- llm.complete()/embeddings.embed() 在唯一调用点 record_llm() —— 无活动
  trace 时零开销 no-op, 离线管线 (knowledge_pipeline / scripts) 不受影响
- 决策点 record_decision(name, ..., **attributes), attributes 记阈值比较的
  实际数值, 让 "为何追问 / 为何结束" 可重构
- span_label("assess"): with 块内的 LLM 调用记录带上归属环节标签

纪律: 本模块**纯旁路观测**, 任何函数都不得影响调用方控制流 (录制失败静默)。
限界: embedding 只录摘要不回放 —— 检索结果依赖 Milvus 状态; 但召回片段
已进 chat prompt 原文被整体录制, 回放 chat 调用即足以重构决策。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from src.schemas import DecisionSpan, DecisionTrace, LLMCallRecord

_active: ContextVar[DecisionTrace | None] = ContextVar("trace_active", default=None)
_label: ContextVar[str] = ContextVar("trace_label", default="")


def activate(trace: DecisionTrace):
    """激活 trace, 返回 token 供 deactivate。orchestrator 专用。"""
    return _active.set(trace)


def deactivate(token) -> None:
    _active.reset(token)


def current() -> DecisionTrace | None:
    return _active.get()


@contextmanager
def span_label(name: str) -> Iterator[None]:
    """with 块内的 record_llm 带上归属环节标签。"""
    token = _label.set(name)
    try:
        yield
    finally:
        _label.reset(token)


def record_llm(
    *,
    kind: str,
    request_hash: str,
    model: str,
    system: str = "",
    user: str = "",
    response: str = "",
    path: str,
    latency_ms: float = 0.0,
) -> None:
    """录一次 LLM/embedding 调用。无活动 trace 时 no-op。"""
    trace = _active.get()
    if trace is None:
        return
    trace.llm_calls.append(LLMCallRecord(
        kind=kind,
        request_hash=request_hash,
        model=model,
        system=system,
        user=user,
        response=response,
        path=path,
        latency_ms=latency_ms,
        span=_label.get(),
    ))


def record_decision(name: str, question_id: str = "", **attributes) -> None:
    """录一个决策 span。attributes 必须 json-safe。无活动 trace 时 no-op。"""
    trace = _active.get()
    if trace is None:
        return
    trace.spans.append(DecisionSpan(
        name=name,
        question_id=question_id,
        attributes=attributes,
        ts=time.time(),
    ))
