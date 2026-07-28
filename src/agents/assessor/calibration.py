"""sufficiency 的 Platt 校准 —— Sprint 8.3 Step 1。

参数由 sim/calibrate_platt.py 离线拟合, 硬编码固定随代码版本 (可复现,
不做运行时拟合):
    拟合于 2026-07-28, 核心金标 42 条二分类样本 (29 suf / 13 insuf),
    真 LLM (gpt-4o-mini) raw sufficiency, 训练集 AUC 0.956。
    calibrated = P(信号充分 | raw) = sigmoid(A * raw + B)

参考锚点: raw 0.6 (现行 stop 阈值) -> 0.927; raw 0.35 -> 0.62; raw 0.2 -> 0.33。
FollowUpPolicy.min_calibrated_to_stop 默认 0.9269 = 精确等效 raw 0.6 ——
Step 1 只换单位不换行为, 产品口径调整须走 sim 批次复验 (CLAUDE.md 双门禁)。

只应用于 **LLM 路径**: 启发式 fallback 的分数 (字数+关键词) 不是同一分布,
不得喂进本映射 —— 启发式路径 calibrated_sufficiency 恒 None, 决策自动退回
raw 阈值 (行为与 8.2 之前逐字节一致)。

重拟合时机: 金标扩充 / Assessor prompt 变更 (后者本来就要求重跑全套校准);
重拟合 = 跑 sim/calibrate_platt.py, 更新本文件常数, 连带复核
min_calibrated_to_stop, 随代码同 commit。
评审修正: 小样本用 Platt 不用 isotonic, 标注到百级再评估切换。
"""
from __future__ import annotations

import math

PLATT_A = 8.142082
PLATT_B = -2.344218


def calibrate_sufficiency(raw: float) -> float:
    """raw sufficiency -> P(信号充分)。严格单调增, 值域 (0,1)。"""
    z = PLATT_A * raw + PLATT_B
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)
