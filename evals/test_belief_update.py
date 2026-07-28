"""Sprint 8.3 task 2 —— CompetencyBelief 共轭更新的数值性质。纯函数, 零依赖。

钉死的性质 (调度语义依赖它们):
- 方差随观测数**严格单调下降**且与观测值无关 (高斯固定噪声; Beta 做不到)
- 均值对观测**顺序不变** (精度加权和)
- 先验中立 (0.5); 不原地改 (返回新对象)

跑法: python -m unittest evals.test_belief_update
"""
from __future__ import annotations

import itertools
import unittest

from src.beliefs import OBS_VAR, PRIOR_MEAN, PRIOR_VAR, initial_belief, lcb, update_belief


def _run_sequence(obs: list[float]):
    b = None
    for o in obs:
        b = update_belief(b, "c1", o)
    return b


class BeliefUpdateTests(unittest.TestCase):
    def test_prior_neutral(self) -> None:
        b = initial_belief("c1")
        self.assertEqual(b.mean, PRIOR_MEAN)
        self.assertEqual(b.variance, PRIOR_VAR)
        self.assertEqual(b.n_observations, 0)

    def test_variance_strictly_decreasing_regardless_of_values(self) -> None:
        """极端交替观测下方差仍单调降 —— 方差只由 n 决定。"""
        b = None
        prev = PRIOR_VAR
        for o in [0.0, 1.0, 0.0, 1.0, 0.5, 0.9]:
            b = update_belief(b, "c1", o)
            self.assertLess(b.variance, prev)
            prev = b.variance

    def test_variance_depends_only_on_n(self) -> None:
        v_high = _run_sequence([0.9, 0.9, 0.9]).variance
        v_mixed = _run_sequence([0.1, 0.9, 0.5]).variance
        self.assertAlmostEqual(v_high, v_mixed, places=12)

    def test_order_invariance(self) -> None:
        obs = [0.2, 0.7, 0.95, 0.4]
        base = _run_sequence(obs)
        for perm in itertools.permutations(obs):
            b = _run_sequence(list(perm))
            self.assertAlmostEqual(b.mean, base.mean, places=12)
            self.assertAlmostEqual(b.variance, base.variance, places=12)

    def test_mean_moves_toward_observations(self) -> None:
        high = _run_sequence([0.9, 0.9])
        low = _run_sequence([0.1, 0.1])
        self.assertGreater(high.mean, 0.7)
        self.assertLess(low.mean, 0.3)

    def test_no_mutation(self) -> None:
        b0 = initial_belief("c1")
        b1 = update_belief(b0, "c1", 0.8)
        self.assertEqual(b0.n_observations, 0)
        self.assertEqual(b1.n_observations, 1)

    def test_lcb_conservative_and_clamped(self) -> None:
        b = _run_sequence([0.9])
        self.assertLess(lcb(b), b.mean)
        self.assertGreaterEqual(lcb(b), 0.0)
        # k=0 退化为 mean
        self.assertAlmostEqual(lcb(b, k=0.0), b.mean, places=12)

    def test_variance_table_documented(self) -> None:
        """beliefs.py 注释里的数值表不许悄悄漂 (min_variance_to_probe 依赖它)。"""
        v1 = 1.0 / (1.0 / PRIOR_VAR + 1.0 / OBS_VAR)
        v3 = 1.0 / (1.0 / PRIOR_VAR + 3.0 / OBS_VAR)
        self.assertAlmostEqual(v1, 0.0662, places=3)
        self.assertAlmostEqual(v3, 0.0268, places=3)
        # 默认 min_variance_to_probe=0.03: 3 次观测后停止消耗预算
        from src.schemas import FollowUpPolicy
        thr = FollowUpPolicy().min_variance_to_probe
        self.assertGreater(v1, thr)
        self.assertLess(v3, thr)


if __name__ == "__main__":
    unittest.main()
