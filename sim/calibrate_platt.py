"""Platt 校准拟合 —— Sprint 8.3 task 1。**烧 token (~42 次 mini 调用,
与 sim/calibrate_assessor 同 prompt, 大概率命中 LLM Redis 缓存 ≈ 零成本)。**

    python -m sim.calibrate_platt

在核心金标 (evals/data/assessment_calibration.json) 的二分类样本上收集
真 LLM 路径 raw sufficiency, 拟合 Platt (单特征 logistic):
    P(sufficient | s) = sigmoid(a * s + b)
输出 (a, b) + P=0.5 对应的 raw 等效阈值 + 训练集 AUC/单调性检查。

产出物**手动**硬编码进 src/agents/assessor/calibration.py (随代码版本固定,
可复现; 不做运行时拟合)。重跑时机: 金标扩充 / Assessor prompt 变更后。

评审修正: 50 条样本用 Platt 不用 isotonic (isotonic 自由度高, 小样本过拟合;
标注到百级再评估切换)。ambiguous 8 条不参与拟合。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from sim._env import bootstrap


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def fit_platt(pairs: list[tuple[float, int]], iters: int = 200) -> tuple[float, float]:
    """单特征 logistic 回归, Newton 法。pairs: [(raw_sufficiency, y)], y∈{0,1}。
    Platt 原文的标签平滑 (t+ = (N+ + 1)/(N+ + 2)) 防小样本过置信。"""
    n_pos = sum(y for _, y in pairs)
    n_neg = len(pairs) - n_pos
    t_pos = (n_pos + 1.0) / (n_pos + 2.0)
    t_neg = 1.0 / (n_neg + 2.0)
    data = [(s, t_pos if y == 1 else t_neg) for s, y in pairs]

    a, b = 1.0, 0.0
    for _ in range(iters):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for s, t in data:
            p = _sigmoid(a * s + b)
            w = max(p * (1.0 - p), 1e-9)
            g_a += (p - t) * s
            g_b += (p - t)
            h_aa += w * s * s
            h_ab += w * s
            h_bb += w
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (g_a * h_bb - g_b * h_ab) / det
        db = (g_b * h_aa - g_a * h_ab) / det
        a -= da
        b -= db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return a, b


def main() -> None:
    bootstrap()
    from src.agents import assessor
    from src.schemas import CandidateAnswer, Question, QuestionCategory

    core = json.loads(
        Path("evals/data/assessment_calibration.json").read_text(encoding="utf-8"),
    )["samples"]

    pairs: list[tuple[float, int]] = []
    print(f"[platt] 收集真 LLM raw sufficiency ({len(core)} 条, ambiguous 跳过) ...")
    for s in core:
        if s["label"] not in ("sufficient", "insufficient"):
            continue
        q = Question(
            competency_id="comp:tech",
            text=s["question"],
            category=QuestionCategory(s["category"]),
        )
        a = CandidateAnswer(question_id=q.question_id, text=s["answer"])
        raw = assessor._assess_via_llm(q, a, aspects=[]).sufficiency
        pairs.append((raw, 1 if s["label"] == "sufficient" else 0))

    a, b = fit_platt(pairs)
    if a <= 0:
        print(f"[platt] ❌ 拟合斜率 a={a:.3f} <= 0 (映射非单调增), 数据有问题")
        sys.exit(1)

    # P=0.5 的 raw 等效阈值: a*s+b=0 -> s = -b/a
    raw_at_half = -b / a
    # AUC (小样本, 只作 sanity)
    pos = sorted(s for s, y in pairs if y == 1)
    neg = sorted(s for s, y in pairs if y == 0)
    wins = sum(
        1.0 if p > n else (0.5 if p == n else 0.0) for p in pos for n in neg
    )
    auc = wins / (len(pos) * len(neg))

    print(f"[platt] n={len(pairs)} (pos={len(pos)} neg={len(neg)})")
    print(f"[platt] a={a:.6f} b={b:.6f}")
    print(f"[platt] P=0.5 等效 raw 阈值 = {raw_at_half:.3f} "
          f"(现行 min_sufficiency_to_stop=0.6)")
    print(f"[platt] 训练集 AUC = {auc:.3f}")
    for probe in (0.2, 0.35, 0.5, 0.6, 0.7, 0.85):
        print(f"    raw {probe:.2f} -> calibrated {_sigmoid(a * probe + b):.3f}")
    print("[platt] 把 a/b 写进 src/agents/assessor/calibration.py 并跑双门禁")


if __name__ == "__main__":
    main()
