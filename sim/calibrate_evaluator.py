"""Evaluator 分数校准 + MAE 门禁 —— Sprint 8.5 task 3 (脚手架)。

    python -m sim.calibrate_evaluator

前置: evals/data/report_score_labels.json 需要 20-30 份**报告级人工数值分**
标注 (评审修正 2: 现有金标是题级三分类, 不够用)。labels 为空时本脚本优雅
跳过并打印标注指引 —— 标注工作量在 sprint.md 记账。

有标注时:
1. 从 PG 取各 report 的模型 overall, 与人工分配对
2. 最小二乘拟合线性映射 human ≈ a×model + b (NAACL'25: 少量标注学
   "LLM 分 -> 人工分" 映射, 免微调); a<=0 = 排序都不对, 直接 fail
3. 门禁: 映射后 MAE <= 10 分 -> 通过; 通过前 **EVAL_PANEL_ENABLED 不许开**
   (CLAUDE.md 红线), rubric 权重调整也应参考本结果
4. 通过后把 (a, b) 常数写进 evaluator (随版本固定), 随代码同 commit
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sim._env import bootstrap

MAE_GATE = 10.0
_LABELS = Path("evals/data/report_score_labels.json")


def main() -> None:
    data = json.loads(_LABELS.read_text(encoding="utf-8"))
    labels = data.get("labels", [])
    if len(labels) < 10:
        print(f"[calibrate-evaluator] 标注不足 ({len(labels)} 份, 需要 >= 10;")
        print("  建议 20-30 份), 跳过。标注指引:")
        for line in data.get("notes", []):
            print(f"    {line}")
        print("[calibrate-evaluator] 标注完成后重跑本脚本。")
        return

    bootstrap()
    from src import db
    from src.db import init_db
    init_db()

    pairs: list[tuple[float, float]] = []   # (model_overall, human_overall)
    missing: list[str] = []
    for row in labels:
        report = db.load_report(str(row["report_id"]))
        if report is None:
            missing.append(str(row["report_id"]))
            continue
        pairs.append((float(report.overall), float(row["human_overall"])))
    if missing:
        print(f"[calibrate-evaluator] ⚠️ {len(missing)} 个 report_id 不在库: {missing[:3]}")
    if len(pairs) < 10:
        print(f"[calibrate-evaluator] 有效配对仅 {len(pairs)}, 不拟合。")
        sys.exit(1)

    n = len(pairs)
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxx = sum(p[0] * p[0] for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        print("[calibrate-evaluator] ❌ 模型分无方差, 无法拟合")
        sys.exit(1)
    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n
    if a <= 0:
        print(f"[calibrate-evaluator] ❌ 斜率 a={a:.3f} <= 0: 模型分与人工分"
              "排序方向都不一致, 校准救不了, 先修评分")
        sys.exit(1)

    mae_raw = sum(abs(m - h) for m, h in pairs) / n
    mae_cal = sum(abs((a * m + b) - h) for m, h in pairs) / n
    print(f"[calibrate-evaluator] n={n}  a={a:.4f} b={b:+.2f}")
    print(f"  校准前 MAE = {mae_raw:.1f} 分; 校准后 MAE = {mae_cal:.1f} 分 "
          f"(门禁 <= {MAE_GATE})")
    if mae_cal > MAE_GATE:
        print("[calibrate-evaluator] ❌ 未过门禁 —— EVAL_PANEL_ENABLED 保持关,"
              " 分析离群样本或扩标注")
        sys.exit(1)
    print("[calibrate-evaluator] ✅ 通过 —— 把 a/b 写进 evaluator 随版本固定,"
          " panel 开启需另行确认 (CLAUDE.md)")


if __name__ == "__main__":
    main()
