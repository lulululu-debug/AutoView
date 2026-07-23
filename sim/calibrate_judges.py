"""judge 金标校准 —— Sprint 6.5 task 4。**烧少量 token (20 次 judge 调用)。**

    python -m sim.calibrate_judges

每个 judge 5 条人工金标 (好坏混合), 至多错 1 条才算过; 任一 judge 不过
→ exit 1, sim.judge 审计结论不算数。改 judges.py prompt 必须重跑本脚本。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sim._env import bootstrap

_MAX_MISS_PER_JUDGE = 1


def main() -> None:
    bootstrap()
    from sim import judges

    data = json.loads(
        Path("sim/data/judge_calibration.json").read_text(encoding="utf-8"),
    )
    failed_judges: list[str] = []

    def run(name: str, samples: list, call) -> None:
        misses = []
        for s in samples:
            got = call(s)
            if got != s["expected"]:
                misses.append(s["id"])
            print(f"  {s['id']:<12} 期望={s['expected']} 实际={got} "
                  f"{'✅' if got == s['expected'] else '❌'}")
        if len(misses) > _MAX_MISS_PER_JUDGE:
            failed_judges.append(f"{name} (错 {len(misses)}: {', '.join(misses)})")
        print(f"[calibrate-judges] {name}: {len(samples) - len(misses)}"
              f"/{len(samples)} {'✅' if len(misses) <= _MAX_MISS_PER_JUDGE else '❌'}\n")

    print("[calibrate-judges] 1/4 题目相关性")
    run("relevance", data["relevance"], lambda s: judges.judge_question_relevance(
        s["question"], s["jd"], s["competency"])["relevant"])

    print("[calibrate-judges] 2/4 追问针对性")
    run("followup", data["followup"], lambda s: judges.judge_followup_targeting(
        s["question"], s["answer"], s["missing_signals"], s["followup"])["targeted"])

    print("[calibrate-judges] 3/4 报告忠实性")
    run("faithfulness", data["faithfulness"], lambda s: judges.judge_report_faithfulness(
        s["summary"], s["transcript"])["faithful"])

    print("[calibrate-judges] 4/4 项目题溯源")
    run("project", data["project"], lambda s: judges.judge_project_question_faithfulness(
        s["question"], s["resume"], s["intro_text"])["grounded"])

    if failed_judges:
        print(f"[calibrate-judges] ❌ 未通过: {'; '.join(failed_judges)}")
        sys.exit(1)
    print("[calibrate-judges] ✅ 四个 judge 全部过校准 —— sim.judge 审计结论可用")


if __name__ == "__main__":
    main()
