"""Sprint 8.3.1 —— persona 冻结答案的结构护栏。零 token。

- fixture 完整性: 9 persona 齐全, self_intro 池非空, 池子非空
- Dispenser: 顺序消费 / 缺 category 借池不炸 / 溢出返回可区分文本
  (逐字复用会触发 Assessor 复制粘贴硬规则) / 未冻结 persona 报错

跑法: python -m unittest evals.test_frozen_personas
"""
from __future__ import annotations

import unittest

from sim.frozen import Dispenser, load_fixtures

_EXPECTED_PERSONAS = {
    "campus-strong", "campus-medium", "campus-weak",
    "lateral-strong", "lateral-medium", "lateral-weak",
    "adv-copy-paste", "adv-off-topic", "adv-terse",
}


class FixtureIntegrityTests(unittest.TestCase):
    def test_all_personas_frozen(self) -> None:
        fixtures = load_fixtures()
        self.assertEqual(set(fixtures.keys()), _EXPECTED_PERSONAS)

    def test_pools_nonempty_with_intro(self) -> None:
        for pid, entry in load_fixtures().items():
            pools = entry["pools"]
            self.assertTrue(pools, f"{pid} 池子为空")
            self.assertGreaterEqual(
                len(pools.get("self_intro", [])), 1, f"{pid} 缺 self_intro",
            )
            for cat, answers in pools.items():
                self.assertTrue(
                    all(a.strip() for a in answers), f"{pid}/{cat} 有空答案",
                )

    def test_source_recorded(self) -> None:
        """重冻结纪律的可追溯性: 每个条目记录来源批次。"""
        for pid, entry in load_fixtures().items():
            self.assertTrue(entry.get("source_batch"), f"{pid} 无 source_batch")


class DispenserTests(unittest.TestCase):
    def test_sequential_consumption(self) -> None:
        d = Dispenser("lateral-medium")
        pool = load_fixtures()["lateral-medium"]["pools"]["project_experience"]
        got = [d.next("project_experience") for _ in range(min(3, len(pool)))]
        self.assertEqual(got, pool[:len(got)])

    def test_missing_category_borrows(self) -> None:
        d = Dispenser("lateral-medium")  # 该 persona 无 knowledge 池
        out = d.next("knowledge")
        self.assertTrue(out.strip())

    def test_overflow_returns_distinct_text(self) -> None:
        """溢出复用必须与原文不同 —— 逐字相同会触发复制粘贴硬规则。"""
        d = Dispenser("adv-copy-paste")
        pool = load_fixtures()["adv-copy-paste"]["pools"]["scenario"]
        seen = [d.next("scenario") for _ in range(len(pool))]
        overflow = d.next("scenario")
        self.assertNotIn(overflow, seen)
        self.assertIn(seen[0][:20], overflow)  # 内容语义保留 (前缀可见)

    def test_unknown_persona_raises(self) -> None:
        with self.assertRaises(KeyError):
            Dispenser("ghost-persona")


if __name__ == "__main__":
    unittest.main()
