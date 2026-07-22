"""仿真跑批 CLI —— Sprint 6.5 task 1。**烧真 token, 显式运行。**

    python -m sim.run_interviews --personas core --repeat 1
    python -m sim.run_interviews --personas lateral-strong,lateral-weak
    python -m sim.run_interviews --personas adversarial --repeat 2

跑前打印预估成本; artifacts 落 sim/runs/<时间戳>/, 供 sim.report (task 2)
与 judge (task 4) 复算。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from sim._env import bootstrap


def main() -> None:
    ap = argparse.ArgumentParser(description="仿真面试跑批 (真 LLM, 烧 token)")
    ap.add_argument(
        "--personas", default="core",
        help="core / adversarial / all / 逗号分隔的 persona id",
    )
    ap.add_argument("--repeat", type=int, default=1, help="每个 persona 跑几次")
    ap.add_argument("--out", default="sim/runs", help="artifacts 根目录")
    ap.add_argument(
        "--run-dir", default=None,
        help="精确输出目录 (分批合并 / 断点续跑用); 缺省 = <out>/<时间戳>",
    )
    args = ap.parse_args()

    bootstrap()

    # bootstrap 之后再 import 业务模块 (env 已定型)
    from sim.personas import select
    from sim.runner import run_one
    from src import db

    personas = select(args.personas)
    n_runs = len(personas) * args.repeat
    print(
        f"[sim] 计划 {n_runs} 场仿真 ({len(personas)} persona × {args.repeat}); "
        f"预估 {n_runs * 30}-{n_runs * 60} 次 gpt-4o-mini 调用, "
        f"约 ¥{n_runs * 0.1:.1f}-{n_runs * 0.5:.1f}"
    )

    db.init_db()
    out_dir = (
        Path(args.run_dir) if args.run_dir
        else Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for p in personas:
        for i in range(args.repeat):
            run_id = f"r{i + 1}"
            print(f"[sim] ▶ {p.persona_id} {run_id} ...", flush=True)
            try:
                row = run_one(p, run_id, out_dir)
            except Exception as e:  # 单场失败不挡整批
                print(f"[sim] ✗ {p.persona_id} {run_id} 失败: {e}")
                rows.append({
                    "persona_id": p.persona_id, "run_id": run_id,
                    "level": p.level, "error": str(e),
                })
                continue
            rows.append(row)
            print(
                f"[sim] ✓ {p.persona_id} {run_id}: overall={row['overall']:.3f} "
                f"answers={row['n_answers']} human_review={row['needs_human_review']} "
                f"({row['duration_s']}s)"
            )

    # 汇总表 (详细指标交给 task 2 的 sim.report)
    print("\n[sim] ==== 汇总 ====")
    print(f"{'persona':<20}{'run':<5}{'level':<13}{'overall':<9}{'答数':<5}")
    for r in sorted(rows, key=lambda r: -(r.get("overall") or 0)):
        if "error" in r:
            print(f"{r['persona_id']:<20}{r['run_id']:<5}{r['level']:<13}FAILED: {r['error'][:40]}")
        else:
            print(
                f"{r['persona_id']:<20}{r['run_id']:<5}{r['level']:<13}"
                f"{r['overall']:<9.3f}{r['n_answers']:<5}"
            )
    import json
    # 带时间戳防分批互相覆盖; report.py 靠 "report" 字段过滤, 不会误读 summary
    (out_dir / f"summary-{time.strftime('%H%M%S')}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    print(f"\n[sim] artifacts: {out_dir}/")


if __name__ == "__main__":
    main()
