"""效果指标汇总 —— Sprint 6.5 task 2。零 token, 纯离线复算 artifacts。

    python -m sim.report                     # 最新一次跑批
    python -m sim.report sim/runs/20260721-163246

指标:
- 区分度: 同 track 内 强>中>弱 的 overall 排序 pairwise 准确率;
  **分维度均值对比** (维度分饱和与否在这里现形 —— Evaluator 打分升级的验收尺)
- 稳定性: 同 persona 多次 repeat 的 overall 均值/标准差/极差 (N=1 时明示不可评)
- 过程指标: 答数 / 追问数 / 证据不足率 / 时长
- 对抗鲁棒性: 对抗 persona (同简历只换答风) vs 同 track medium 基线的 Δoverall
  —— 复制粘贴/跑题/敷衍应显著低于认真作答的同简历基线

判定信号 (与设计文档对齐):
- 「证据不足」以 summary 前缀为准, **不看 needs_human_review** —— 那个字段
  对每份报告恒为 True (§7 第 9 条: 最终决定必须由 HR 做), 无信息量。
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_INSUFFICIENT_PREFIX = "证据不充分"
# 维度分饱和判定: 同 track 强弱两档的维度均值差小于该阈值 → 该维度没有区分度
_DIM_FLAT_THRESHOLD = 5.0


def _latest_runs_dir() -> Path:
    root = Path("sim/runs")
    dirs = sorted(d for d in root.iterdir() if d.is_dir()) if root.exists() else []
    if not dirs:
        raise SystemExit("sim/runs/ 下没有跑批目录, 先跑 python -m sim.run_interviews")
    return dirs[-1]


def load_runs(runs_dir: Path) -> list[dict]:
    runs = []
    for f in sorted(runs_dir.glob("*.json")):
        if f.name == "summary.json":
            continue
        a = json.loads(f.read_text(encoding="utf-8"))
        if "report" in a:
            runs.append(a)
    if not runs:
        raise SystemExit(f"{runs_dir} 下没有可用 artifact")
    return runs


# ---- 单场派生量 ----

def _followups(a: dict) -> int:
    plan_q = {q["question_id"] for r in a["plan"]["rounds"] for q in r["questions"]}
    sess = a.get("session") or {}
    return sum(
        1 for t in sess.get("history", [])
        if t.get("role") == "interviewer"
        and t.get("ref_id") and t["ref_id"] not in plan_q
    )


def _insufficient(a: dict) -> bool:
    return (a["report"].get("summary") or "").startswith(_INSUFFICIENT_PREFIX)


def _dim_scores(a: dict) -> dict[str, float]:
    return {
        s["competency_id"]: s["score"] for s in a["report"]["content_scores"]
    }


# ---- 聚合 ----

def _mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def build_report(runs: list[dict]) -> str:
    by_persona: dict[str, list[dict]] = defaultdict(list)
    for a in runs:
        by_persona[a["persona_id"]].append(a)

    lines: list[str] = ["# 仿真效果报告", ""]
    lines.append(f"共 {len(runs)} 场 / {len(by_persona)} 个 persona")
    lines.append("")

    # 1. 概览
    lines += ["## 概览", "",
              "| persona | level | track | N | overall 均值±σ | 答数 | 追问 | 证据不足率 | 时长(s) |",
              "|---|---|---|---|---|---|---|---|---|"]
    stats: dict[str, dict] = {}
    for pid, arts in sorted(by_persona.items()):
        overalls = [a["overall"] for a in arts]
        st = {
            "level": arts[0]["level"], "track": arts[0]["track"],
            "expected_rank": arts[0].get("expected_rank", 0),
            "n": len(arts),
            "overall_mean": _mean(overalls), "overall_std": _std(overalls),
            "overall_range": (max(overalls) - min(overalls)) if overalls else 0.0,
            "answers_mean": _mean([a["n_answers"] for a in arts]),
            "followups_mean": _mean([_followups(a) for a in arts]),
            "insufficient_rate": _mean([1.0 if _insufficient(a) else 0.0 for a in arts]),
            "duration_mean": _mean([a["duration_s"] for a in arts]),
        }
        # 分维度均值
        dims: dict[str, list[float]] = defaultdict(list)
        for a in arts:
            for cid, s in _dim_scores(a).items():
                dims[cid].append(s)
        st["dims"] = {cid: _mean(v) for cid, v in dims.items()}
        stats[pid] = st
        lines.append(
            f"| {pid} | {st['level']} | {st['track']} | {st['n']} "
            f"| {st['overall_mean']:.1f}±{st['overall_std']:.1f} "
            f"| {st['answers_mean']:.1f} | {st['followups_mean']:.1f} "
            f"| {st['insufficient_rate']:.0%} | {st['duration_mean']:.0f} |"
        )
    lines.append("")

    # 2. 区分度 (核心 persona, 按 track)
    lines += ["## 区分度 (期望: strong > medium > weak)", ""]
    tracks = sorted({st["track"] for st in stats.values()})
    for track in tracks:
        core = {
            pid: st for pid, st in stats.items()
            if st["track"] == track and st["expected_rank"] > 0
        }
        if len(core) < 2:
            continue
        ordered = sorted(core.items(), key=lambda kv: -kv[1]["expected_rank"])
        actual = sorted(core.items(), key=lambda kv: -kv[1]["overall_mean"])
        pairs = correct = 0
        items = list(core.items())
        for i in range(len(items)):
            for j in range(len(items)):
                hi, hj = items[i][1], items[j][1]
                if hi["expected_rank"] > hj["expected_rank"]:
                    pairs += 1
                    if hi["overall_mean"] > hj["overall_mean"]:
                        correct += 1
        acc = correct / pairs if pairs else 0.0
        verdict = "✅" if acc == 1.0 else ("⚠️" if acc >= 0.5 else "❌")
        lines.append(
            f"- **{track}**: pairwise 准确率 {correct}/{pairs} = {acc:.0%} {verdict}; "
            f"实际排序: {' > '.join(f'{pid}({st['overall_mean']:.1f})' for pid, st in actual)}"
        )
        # 分维度区分度 —— 维度分饱和在这里现形
        all_dims = sorted({d for st in core.values() for d in st["dims"]})
        for dim in all_dims:
            vals = {
                st["level"]: st["dims"].get(dim)
                for st in core.values() if dim in st["dims"]
            }
            if len(vals) < 2:
                continue
            spread = max(vals.values()) - min(vals.values())
            flat = " ⚠️**无区分度 (饱和?)**" if spread < _DIM_FLAT_THRESHOLD else ""
            pretty = ", ".join(f"{lv}={v:.1f}" for lv, v in sorted(vals.items()))
            lines.append(f"  - `{dim}`: {pretty} (极差 {spread:.1f}){flat}")
    lines.append("")

    # 3. 稳定性
    lines += ["## 稳定性 (同 persona 跨 repeat)", ""]
    any_repeat = False
    for pid, st in sorted(stats.items()):
        if st["n"] > 1:
            any_repeat = True
            flag = "✅" if st["overall_std"] < 5.0 else "⚠️"
            lines.append(
                f"- {pid}: N={st['n']}, σ={st['overall_std']:.2f}, "
                f"极差={st['overall_range']:.1f} {flag}"
            )
    if not any_repeat:
        lines.append("- 所有 persona 均 N=1, 稳定性不可评 (用 --repeat 3 重跑)")
    lines.append("")

    # 4. 对抗鲁棒性
    adv = {pid: st for pid, st in stats.items() if st["level"] == "adversarial"}
    if adv:
        lines += ["## 对抗鲁棒性 (对抗 persona vs 同 track medium 基线)", ""]
        for pid, st in sorted(adv.items()):
            base = next(
                (s for s in stats.values()
                 if s["track"] == st["track"] and s["level"] == "medium"), None,
            )
            if base is None:
                lines.append(f"- {pid}: 无同 track medium 基线, 单值 {st['overall_mean']:.1f}")
                continue
            delta = st["overall_mean"] - base["overall_mean"]
            verdict = "✅ 被正确压低" if delta <= -5 else ("⚠️ 与认真作答无差" if abs(delta) < 5 else "❌ 反而更高")
            lines.append(
                f"- {pid}: {st['overall_mean']:.1f} vs 基线 {base['overall_mean']:.1f} "
                f"(Δ{delta:+.1f}) {verdict}"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    runs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_runs_dir()
    runs = load_runs(runs_dir)
    md = build_report(runs)
    out = runs_dir / "report.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n[sim] 报告已写入 {out}")


if __name__ == "__main__":
    main()
