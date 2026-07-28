"""从 sim 批次 artifact 冻结 persona 答案 —— Sprint 8.3.1。

    python -m scripts.freeze_persona_answers [--batch sim/runs/batch-20260728-s83]

把批次里每个 persona 的真实回答按 category (self_intro/knowledge/
project_experience/scenario) 抽成有序答案池, 写 sim/data/frozen_answers.json。
追问的回答归入父题 category 的池 (按遭遇顺序), 复放时同池顺序消费。

重冻结时机: planner 出题变更 (fixture 答案与新题失配) / persona 定义变更。
与 golden trace 重录同款纪律: 重冻结必须显式 commit, 出现在 PR diff 里。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

OUT = Path("sim/data/frozen_answers.json")


def freeze_batch(batch_dir: Path) -> dict:
    out: dict = {}
    for f in sorted(batch_dir.glob("*_r1.json")):
        art = json.loads(f.read_text(encoding="utf-8"))
        persona_id = art.get("persona_id")
        sess = art.get("session")
        plan = art.get("plan")
        if not (persona_id and sess and plan):
            continue
        q_by_id = {
            q["question_id"]: q
            for r in plan["rounds"] for q in r["questions"]
        }
        pools: dict[str, list[str]] = {}
        last_cat = "self_intro"
        # history 里 interviewer turn 的 ref -> 该题 category; 紧随的
        # candidate turn 即回答。追问 ref 不在题库 -> 归上一个 category。
        pending_cat: str | None = None
        for turn in sess["history"]:
            if turn["role"] == "interviewer":
                q = q_by_id.get(turn.get("ref_id") or "")
                pending_cat = q["category"] if q else last_cat
                last_cat = pending_cat
            elif turn["role"] == "candidate" and pending_cat:
                pools.setdefault(pending_cat, []).append(turn["text"])
                pending_cat = None
        out[persona_id] = {
            "source_batch": batch_dir.name,
            "source_session": art.get("session_id"),
            "pools": pools,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="冻结 persona 答案")
    ap.add_argument("--batch", default="sim/runs/batch-20260728-s83")
    args = ap.parse_args()
    data = freeze_batch(Path(args.batch))
    if not data:
        raise SystemExit("批次里没有可冻结的 artifact")
    OUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    for pid, entry in data.items():
        sizes = {c: len(v) for c, v in entry["pools"].items()}
        print(f"[freeze] {pid:<18} {sizes}")
    print(f"[freeze] -> {OUT} ({len(data)} personas)")


if __name__ == "__main__":
    main()
