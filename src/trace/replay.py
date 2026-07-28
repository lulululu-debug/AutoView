"""确定性回放 —— Sprint 8.2 task 3。

REPLAY_MODE=1 时 llm.complete()/complete_vision() 按 request_hash 命中
录制返回, miss 一律抛 ReplayDivergence —— 标记分歧让回归 fail, 绝不静默
(AgentRR 教训: 盲重放会掩盖输入漂移)。回放判定在 API key 判断**之前**:
无 key 也能回放, 回归 eval 零 token。

store 显式加载 (set_store / load_store), 不自动读盘 —— 回放是 eval/审计
场景的显式行为, 生产路径 REPLAY_MODE 不设即零开销。

限界: 同 request_hash 多次调用产生不同响应时 (真 LLM temperature>0),
store 是 hash->response 单值映射, 取录制序列的最后一条 —— stub/缓存路径
无此问题; 真 LLM golden 录制若要精确多次回放, 需升级为 hash->队列 (留账)。
embedding 不回放 (见 src/trace 模块注)。
"""
from __future__ import annotations

import json
import os

from src.schemas import DecisionTrace

_CHAT_KINDS = ("chat", "chat_vision")

_store: dict[str, str] | None = None


class ReplayDivergence(RuntimeError):
    """回放 miss: 当前代码产生了录制中不存在的请求 (决策路径已漂移)。"""


def replay_active() -> bool:
    return os.environ.get("REPLAY_MODE", "").lower() in ("1", "true", "yes")


def set_store(mapping: dict[str, str]) -> None:
    global _store
    _store = dict(mapping)


def clear_store() -> None:
    global _store
    _store = None


def load_store(path: str) -> None:
    """从 golden trace 文件 (DecisionTrace JSON) 或 {"records": {...}} 加载。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "records" in data:
        set_store(data["records"])
    else:
        set_store(store_from_trace(DecisionTrace.model_validate(data)))


def store_from_trace(trace: DecisionTrace) -> dict[str, str]:
    """从录制 trace 构建回放映射 (只取 chat 类调用)。"""
    return {
        c.request_hash: c.response
        for c in trace.llm_calls
        if c.kind in _CHAT_KINDS
    }


def lookup(request_hash: str) -> str:
    """回放查找。store 未加载或 miss -> ReplayDivergence。"""
    if _store is None:
        raise ReplayDivergence(
            "REPLAY_MODE=1 但未加载 store (set_store/load_store)"
        )
    if request_hash not in _store:
        raise ReplayDivergence(
            f"回放 miss: request_hash={request_hash} 不在录制中 "
            "(prompt/模型/参数已与录制时不同)"
        )
    return _store[request_hash]
