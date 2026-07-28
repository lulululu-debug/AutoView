"""录制 golden traces —— Sprint 8.2 task 3。零 token (强制 stub)。

    python -m scripts.record_golden_traces

需要 TEST_POSTGRES_URL + REDIS_URL。每个场景连跑两遍做确定性自检
(决策签名不一致就 abort, 不产出不可信的 golden), 通过后写
evals/data/golden_traces/<name>.json (DecisionTrace 全量 JSON)。

约定 (Sprint 8.2 起): 改任何 prompt / policy 阈值 => golden 决策序列会变,
跑 evals/test_trace_replay.py 看 diff, 确认变化符合预期后**重录本脚本**并
连同代码一起 commit —— golden 更新必须是显式动作, 出现在 PR diff 里。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from evals._test_db import swap_to_test_url

swap_to_test_url()

OUT_DIR = Path(__file__).resolve().parent.parent / "evals" / "data" / "golden_traces"


def main() -> None:
    # orchestrator -> pymilvus 会 load_dotenv 回填 key; 隔离放 import 之后
    from evals.golden_scenarios import SCENARIOS, isolate_environment, run_scenario
    from src import db
    from src.db import init_db
    from src.trace.diff import decision_signature, diff_traces

    isolate_environment()
    init_db()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for sc in SCENARIOS:
        sid1 = run_scenario(sc)
        sid2 = run_scenario(sc)
        t1 = db.load_decision_trace(sid1)
        t2 = db.load_decision_trace(sid2)
        assert t1 is not None and t2 is not None, "trace 未归档"
        divergences = diff_traces(t1, t2)
        if divergences:
            print(f"[golden] ❌ {sc['name']} 两遍决策序列不一致, abort:")
            for d in divergences[:5]:
                print("   ", d)
            sys.exit(1)
        out = OUT_DIR / f"{sc['name']}.json"
        with out.open("w", encoding="utf-8") as f:
            json.dump(t1.model_dump(mode="json"), f, ensure_ascii=False, indent=1)
        print(
            f"[golden] ✅ {sc['name']}: spans={len(t1.spans)} "
            f"llm_calls={len(t1.llm_calls)} 签名项={len(decision_signature(t1))} "
            f"-> {out.relative_to(OUT_DIR.parent.parent)}"
        )


if __name__ == "__main__":
    main()
