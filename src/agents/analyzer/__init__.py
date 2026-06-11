"""Multimodal Analyzer Agent — 骨架占位。

Sprint 0 不实现任何视觉/音频分析, 恒返回空 Signal 列表。
合规约束: 软信号只能作为参考证据进入 EvaluationReport.performance_observations,
永远不参与打分; 真正实现见 Sprint 7。
"""
from __future__ import annotations

from src.schemas import InterviewSession, Signal


def analyze(session: InterviewSession) -> list[Signal]:
    """Analyzer 入口。骨架阶段恒返回空列表。"""
    _ = session
    return []
