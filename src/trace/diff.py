"""决策序列确定性 diff —— golden trace 回归 (Sprint 8.2 task 3)。

TRAIL 实测 LLM 自动审 trace 能力很差 (最佳 11%), 所以回归判定用**确定性
比较**: 把 trace 规约成决策签名序列, 逐项 diff, 首个分歧点即定位。

规约规则 (可复现性要求):
- 剔除运行期噪声: span_id / ts / latency_ms / record_id / path
  (path 是运行环境属性: 同一决策在录制时 stub、回放时 replay)
- 剔除跨 run 不稳定的 uuid: question_id / plan_id;
  coverage / assessed_counts 这类以 competency_id (uuid) 为键的 dict
  规约为排序后的值列表 (值序列不变即认为决策一致)
- llm 调用规约为 (kind, request_hash, response): request_hash 已含
  prompt/模型/参数全文哈希, 内容级等价
"""
from __future__ import annotations

from src.schemas import DecisionSpan, DecisionTrace

_DROP_ATTRS = {"plan_id", "competency_id"}
_ID_KEYED_DICT_ATTRS = {"coverage", "assessed_counts"}


def _normalize_attrs(attrs: dict) -> tuple:
    items = []
    for k in sorted(attrs):
        if k in _DROP_ATTRS:
            continue
        v = attrs[k]
        if k in _ID_KEYED_DICT_ATTRS and isinstance(v, dict):
            v = sorted(v.values())
        items.append((k, repr(v)))
    return tuple(items)


def span_signature(span: DecisionSpan) -> tuple:
    return (span.name, _normalize_attrs(span.attributes))


def decision_signature(trace: DecisionTrace) -> list[tuple]:
    """决策签名序列: spans 与 llm 调用各自按序规约。"""
    sig: list[tuple] = [("span",) + span_signature(s) for s in trace.spans]
    sig += [
        ("llm", c.kind, c.request_hash, c.response)
        for c in trace.llm_calls
    ]
    return sig


def diff_traces(golden: DecisionTrace, other: DecisionTrace) -> list[str]:
    """逐项比较, 返回人读分歧列表; [] = 一致。首个分歧带定位信息。"""
    a, b = decision_signature(golden), decision_signature(other)
    out: list[str] = []
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            out.append(f"#{i} {x[0]}: golden={x[1:3]}... != other={y[1:3]}...")
    if len(a) != len(b):
        out.append(f"长度不一致: golden={len(a)} other={len(b)}")
    return out
