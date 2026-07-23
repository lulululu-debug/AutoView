"""HR 复核回流统计 —— Sprint 6.5 task 6。零 LLM, 只读 dev PG。

    python -m scripts.review_stats

思路: ReviewRecord 是天然的效果反馈信号 (真人对照的免费替代品):
- 复核率: 有多少报告真的被 HR 看过并下了结论
- 分数-决策一致性: recommend / borderline / reject 三桶的 overall 均值
  应当单调递减 —— AI 打分与 HR 判断同向才说明报告可信
  (注: 设计上 AI 不做录用建议, 所以没有"采纳率"直接指标, 用同向性代替)
- 改分率: dimension_overrides 非空的复核占比 + 每维度 |Δ| 均值 ——
  HR 改得越多越大, AI 维度分越不可信
- 证据不足 × 决策: 被标"证据不充分"的报告 HR 最终怎么判 (兜底机制是否被用)

数据为空时优雅降级 (dev 期 HR 复核还没真实使用); 聚合逻辑是纯函数,
evals/test_review_stats.py 用构造数据锁行为。
"""
from __future__ import annotations

import statistics
from collections import defaultdict

_DECISION_ORDER = ("recommend", "borderline", "reject")
_INSUFFICIENT_PREFIX = "证据不充分"


def compute_stats(reports: list[dict], reviews: list[dict]) -> dict:
    """纯函数聚合。reports: [{report_id, overall, summary, content_scores}];
    reviews: [{report_id, decision, dimension_overrides}]。"""
    by_report = {r["report_id"]: r for r in reports}
    n_reports, n_reviews = len(reports), len(reviews)

    overall_by_decision: dict[str, list[float]] = defaultdict(list)
    overridden = 0
    dim_deltas: dict[str, list[float]] = defaultdict(list)
    insufficient_decisions: dict[str, int] = defaultdict(int)

    for rv in reviews:
        rep = by_report.get(rv["report_id"])
        if rep is None:
            continue
        overall_by_decision[rv["decision"]].append(rep["overall"])
        overrides = rv.get("dimension_overrides") or []
        if overrides:
            overridden += 1
            orig = {s["competency_id"]: s["score"] for s in rep["content_scores"]}
            for o in overrides:
                if o["competency_id"] in orig:
                    dim_deltas[o["competency_id"]].append(
                        o["score"] - orig[o["competency_id"]],
                    )
        if (rep.get("summary") or "").startswith(_INSUFFICIENT_PREFIX):
            insufficient_decisions[rv["decision"]] += 1

    means = {
        d: statistics.fmean(v) for d, v in overall_by_decision.items() if v
    }
    ordered = [means[d] for d in _DECISION_ORDER if d in means]
    monotonic = all(a > b for a, b in zip(ordered, ordered[1:])) if len(ordered) >= 2 else None

    return {
        "n_reports": n_reports,
        "n_reviews": n_reviews,
        "review_rate": (n_reviews / n_reports) if n_reports else 0.0,
        "decision_counts": {d: len(v) for d, v in overall_by_decision.items()},
        "overall_mean_by_decision": {d: round(m, 1) for d, m in means.items()},
        "score_decision_monotonic": monotonic,  # None = 桶不足无法判
        "override_rate": (overridden / n_reviews) if n_reviews else 0.0,
        "dim_mean_abs_delta": {
            k: round(statistics.fmean([abs(x) for x in v]), 1)
            for k, v in dim_deltas.items()
        },
        "insufficient_decisions": dict(insufficient_decisions),
    }


def render(stats: dict) -> str:
    lines = ["# HR 复核回流统计", ""]
    lines.append(f"- 报告 {stats['n_reports']} 份, 复核 {stats['n_reviews']} 条 "
                 f"(复核率 {stats['review_rate']:.0%})")
    if stats["n_reviews"] == 0:
        lines.append("- (还没有复核数据 —— HR 复核真实使用后重跑本脚本)")
        return "\n".join(lines)
    lines.append(f"- decision 分布: {stats['decision_counts']}")
    lines.append(f"- 各决策桶 overall 均值: {stats['overall_mean_by_decision']}")
    mono = stats["score_decision_monotonic"]
    lines.append(
        "- 分数-决策同向性: "
        + ("桶不足, 无法判" if mono is None else ("✅ 单调 (AI 分与 HR 判断同向)" if mono else "❌ 不单调 (AI 分与 HR 判断打架, 查报告质量)"))
    )
    lines.append(f"- 改分率: {stats['override_rate']:.0%}"
                 + (f", 每维度 |Δ| 均值: {stats['dim_mean_abs_delta']}"
                    if stats["dim_mean_abs_delta"] else ""))
    if stats["insufficient_decisions"]:
        lines.append(f"- '证据不充分' 报告的最终决策: {stats['insufficient_decisions']}")
    return "\n".join(lines)


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    import json
    import os

    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["POSTGRES_URL"])
    with eng.connect() as c:
        reports = [
            {
                "report_id": r[0], "overall": float(r[1]),
                "summary": r[2] or "",
                "content_scores": r[3] if isinstance(r[3], list) else json.loads(r[3] or "[]"),
            }
            for r in c.execute(text(
                "SELECT report_id, overall, summary, content_scores "
                "FROM evaluation_reports"))
        ]
        reviews = [
            {
                "report_id": r[0], "decision": r[1],
                "dimension_overrides": r[2] if isinstance(r[2], list) else json.loads(r[2] or "[]"),
            }
            for r in c.execute(text(
                "SELECT report_id, decision, dimension_overrides "
                "FROM review_records"))
        ]
    print(render(compute_stats(reports, reviews)))


if __name__ == "__main__":
    main()
