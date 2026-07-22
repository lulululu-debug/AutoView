"""Assessor 真 LLM 路径校准 —— Sprint 6.5 F1。**烧 token (~60 次 mini 调用)。**

    python -m sim.calibrate_assessor

两组金标:
1. 核心集 evals/data/assessment_calibration.json (与 CI 启发式校准共用样本):
   sufficient 均值须显著高于 insufficient (gap ≥ 0.3); ambiguous 只记录。
2. 对抗扩展集 sim/data/adv_calibration.json (仅真 LLM 消费, 不进 CI):
   正确废话/跑题 → expect.max 上限; knowledge 守卫样本 → expect.min 下限
   (防"要求个人经历"矫枉过正伤及校招基础题)。

纪律: 改 _SYSTEM_PROMPT 后必须重跑本脚本, 两组全过才许跑 sim 批次复验。
直调 assessor._assess_via_llm (私有函数): 校准就是要绕开启发式 fallback,
LLM 失败要显式炸出来而不是静默降级。
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from sim._env import bootstrap

_CORE_GAP_PASS = 0.3


def main() -> None:
    bootstrap()

    from src.agents import assessor
    from src.schemas import CandidateAnswer, Question, QuestionCategory

    def _assess(category: str, question: str, answer: str) -> float:
        q = Question(
            competency_id="comp:tech",
            text=question,
            category=QuestionCategory(category),
        )
        a = CandidateAnswer(question_id=q.question_id, text=answer)
        result = assessor._assess_via_llm(q, a, aspects=[])
        return result.sufficiency

    failures: list[str] = []

    # ---- 1. 核心集 ----
    core = json.loads(
        Path("evals/data/assessment_calibration.json").read_text(encoding="utf-8"),
    )["samples"]
    buckets: dict[str, list[float]] = {"sufficient": [], "insufficient": []}
    print(f"[calibrate] 核心集 {len(core)} 条 (ambiguous 只记录) ...")
    for s in core:
        suff = _assess(s["category"], s["question"], s["answer"])
        if s["label"] in buckets:
            buckets[s["label"]].append(suff)
    suf_mean = statistics.fmean(buckets["sufficient"])
    insuf_mean = statistics.fmean(buckets["insufficient"])
    gap = suf_mean - insuf_mean
    ok = gap >= _CORE_GAP_PASS
    print(
        f"[calibrate] 核心集: sufficient={suf_mean:.3f} (n={len(buckets['sufficient'])}) "
        f"insufficient={insuf_mean:.3f} (n={len(buckets['insufficient'])}) "
        f"gap={gap:+.3f} {'✅' if ok else '❌ (< ' + str(_CORE_GAP_PASS) + ')'}"
    )
    if not ok:
        failures.append(f"核心集 gap {gap:+.3f} < {_CORE_GAP_PASS}")

    # ---- 2. 对抗扩展集 ----
    adv = json.loads(
        Path("sim/data/adv_calibration.json").read_text(encoding="utf-8"),
    )["samples"]
    print(f"[calibrate] 对抗扩展集 {len(adv)} 条 ...")
    for s in adv:
        suff = _assess(s["category"], s["question"], s["answer"])
        exp = s["expect"]
        if "max" in exp:
            ok = suff <= exp["max"]
            cond = f"≤{exp['max']}"
        else:
            ok = suff >= exp["min"]
            cond = f"≥{exp['min']}"
        print(f"  {s['id']:<12} sufficiency={suff:.2f} (期望 {cond}) "
              f"{'✅' if ok else '❌'}  # {s['note'][:36]}")
        if not ok:
            failures.append(f"{s['id']}: {suff:.2f} 不满足 {cond}")

    # ---- 结论 ----
    if failures:
        print(f"\n[calibrate] ❌ 未通过 ({len(failures)} 项):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[calibrate] ✅ 全部通过 —— 可以跑 sim 批次复验")


if __name__ == "__main__":
    main()
