"""Sprint 6.5 task 6 —— HR 复核回流统计的纯函数护栏。零 infra 零 LLM。

跑法: python -m unittest evals.test_review_stats
"""
from __future__ import annotations

import unittest

from scripts.review_stats import compute_stats, render


def _report(rid: str, overall: float, tech: float = 80.0, summary: str = "") -> dict:
    return {
        "report_id": rid, "overall": overall, "summary": summary,
        "content_scores": [
            {"competency_id": "comp:tech", "score": tech},
            {"competency_id": "comp:comm", "score": 70.0},
        ],
    }


def _review(rid: str, decision: str, overrides: list | None = None) -> dict:
    return {"report_id": rid, "decision": decision,
            "dimension_overrides": overrides or []}


class ComputeStatsTests(unittest.TestCase):
    def test_empty(self) -> None:
        s = compute_stats([], [])
        self.assertEqual(s["n_reviews"], 0)
        self.assertIn("还没有复核数据", render(s))

    def test_monotonic_alignment(self) -> None:
        reports = [_report("r1", 85), _report("r2", 60), _report("r3", 30)]
        reviews = [
            _review("r1", "recommend"),
            _review("r2", "borderline"),
            _review("r3", "reject"),
        ]
        s = compute_stats(reports, reviews)
        self.assertTrue(s["score_decision_monotonic"])
        self.assertEqual(s["review_rate"], 1.0)

    def test_non_monotonic_flagged(self) -> None:
        reports = [_report("r1", 30), _report("r2", 85)]
        reviews = [_review("r1", "recommend"), _review("r2", "reject")]
        s = compute_stats(reports, reviews)
        self.assertFalse(s["score_decision_monotonic"])
        self.assertIn("打架", render(s))

    def test_single_bucket_cannot_judge(self) -> None:
        s = compute_stats([_report("r1", 80)], [_review("r1", "recommend")])
        self.assertIsNone(s["score_decision_monotonic"])

    def test_override_rate_and_delta(self) -> None:
        reports = [_report("r1", 80, tech=90.0), _report("r2", 70)]
        reviews = [
            _review("r1", "recommend",
                    [{"competency_id": "comp:tech", "score": 60.0}]),
            _review("r2", "recommend"),
        ]
        s = compute_stats(reports, reviews)
        self.assertEqual(s["override_rate"], 0.5)
        self.assertEqual(s["dim_mean_abs_delta"]["comp:tech"], 30.0)

    def test_insufficient_cross(self) -> None:
        reports = [_report("r1", 40, summary="证据不充分, 建议人工面谈: x")]
        reviews = [_review("r1", "borderline")]
        s = compute_stats(reports, reviews)
        self.assertEqual(s["insufficient_decisions"], {"borderline": 1})

    def test_orphan_review_ignored(self) -> None:
        s = compute_stats([], [_review("ghost", "recommend")])
        self.assertEqual(s["decision_counts"], {})


if __name__ == "__main__":
    unittest.main()
