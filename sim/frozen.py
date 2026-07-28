"""冻结答案发放器 —— Sprint 8.3.1。

--frozen 批次里替代 sim/candidate.py 的生成路径: 按 category 顺序消费
sim/data/frozen_answers.json 里录制的答案池, 候选人端零 LLM 调用。
输入全固定 -> 批次间分差只可能来自评估端 (assessor/planner/policy),
这就是"真 LLM 版 golden"。

溢出策略 (评估端变更让追问变多, 池子被问穿时): 循环复用 + 圈数后缀 ——
直接逐字复用会触发 Assessor 的复制粘贴硬规则 (故意的对抗检测), 后缀让
文本可区分, 同时保持内容语义; 溢出应罕见, 出现即打 log 供审查。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_FIXTURE_PATH = Path(__file__).resolve().parent / "data" / "frozen_answers.json"

_fixtures: dict | None = None


def load_fixtures() -> dict:
    global _fixtures
    if _fixtures is None:
        _fixtures = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    return _fixtures


class Dispenser:
    """单场面试的答案发放器 (有状态, 每场新建)。"""

    def __init__(self, persona_id: str) -> None:
        fixtures = load_fixtures()
        if persona_id not in fixtures:
            raise KeyError(
                f"persona {persona_id!r} 无冻结答案 —— 先跑 "
                "scripts/freeze_persona_answers.py"
            )
        self._pools: dict[str, list[str]] = fixtures[persona_id]["pools"]
        self._cursor: dict[str, int] = {}

    def next(self, category: str) -> str:
        pool = self._pools.get(category)
        if not pool:
            # 该 category 无录制 (计划结构变更): 借第一个非空池, 打 log
            log.warning("frozen: category %s 无池, 借用其他池", category)
            pool = next(v for v in self._pools.values() if v)
        i = self._cursor.get(category, 0)
        self._cursor[category] = i + 1
        if i < len(pool):
            return pool[i]
        lap = i // len(pool)
        log.warning(
            "frozen: category %s 池溢出 (第 %d 圈) —— 评估端变更让问答变多?",
            category, lap + 1,
        )
        return f"{pool[i % len(pool)]}(补充第 {lap} 次: 我的经验就是这些。)"
