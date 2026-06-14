"""Sprint 5.6 Assessor 校准 eval —— 启发式 fallback 路径锁定。

定位:
- 本 eval 跑的是 Assessor 的 **启发式 fallback** (LLM stub 路径), 不烧 token。
- 验证 "sufficient" 标签样本的平均 sufficiency 严格高于 "insufficient" 标签的均值。
  差距 >= 0.2 = strong pass; 0.0-0.2 = weak pass (打 warning 但不挡 CI)。
- ambiguous 标签样本只 record + 打印, 不参与 pass/fail (label 本身有争议)。
- **真 LLM 路径的校准必须人工在 PR review 跑**: 把 ASSESSOR_ENABLED 翻 ON
  + 配上 OPENAI_API_KEY, 复跑本文件, 人肉看数据集每条 prediction 是否合理。
  人工 review 通过, 才把 ASSESSOR_ENABLED 默认改 true 上线。

数据集: evals/data/assessment_calibration.json (24 条样本, 覆盖 4 个 category)。
"""
from __future__ import annotations

import json
import os
import statistics
import unittest
from pathlib import Path

# 强制 stub: 让 Assessor 走启发式 fallback (这是本 eval 验证的对象)
os.environ.pop("OPENAI_API_KEY", None)

from src.agents import assessor  # noqa: E402
from src.schemas import (  # noqa: E402
    CandidateAnswer,
    Competency,
    InterviewPlan,
    InterviewRound,
    InterviewSession,
    Question,
    QuestionCategory,
)

_DATA_PATH = Path(__file__).parent / "data" / "assessment_calibration.json"


def _load_dataset() -> list[dict]:
    with _DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)["samples"]


def _run_assessor(sample: dict) -> float:
    """构造 minimal fixture 跑 assessor.assess, 返回 sufficiency。"""
    cat = QuestionCategory(sample["category"])
    competency_id = None if cat is QuestionCategory.SELF_INTRO else "cid-test"
    q = Question(
        competency_id=competency_id,
        text=sample["question"],
        category=cat,
    )
    a = CandidateAnswer(question_id=q.question_id, text=sample["answer"])
    # session + plan 在 5.6 阶段 assess 没消费, 但接口要求传, 给个 minimal 实例
    comp = Competency(name="技术深度", description="x")
    plan = InterviewPlan(
        job_id="j", rounds=[
            InterviewRound(index=0, title="t", competencies=[comp], questions=[q]),
        ],
        competencies=[comp],
    )
    session = InterviewSession(plan_id=plan.plan_id, job_id="j")
    result = assessor.assess(q, a, session, plan)
    return result.sufficiency


class CalibrationDatasetTests(unittest.TestCase):
    """数据集本身的结构性护栏 —— 防有人改坏 JSON。"""

    @classmethod
    def setUpClass(cls):
        cls.samples = _load_dataset()

    def test_dataset_size_in_range(self):
        # sprint: 20-30 条
        self.assertGreaterEqual(len(self.samples), 20)
        self.assertLessEqual(len(self.samples), 50)

    def test_all_categories_covered(self):
        cats = {s["category"] for s in self.samples}
        self.assertEqual(
            cats,
            {"knowledge", "project_experience", "self_intro", "scenario"},
            "校准集必须覆盖 4 类 QuestionCategory",
        )

    def test_all_labels_valid(self):
        for s in self.samples:
            self.assertIn(
                s["label"], {"sufficient", "insufficient", "ambiguous"},
                f"{s['id']} label 非法",
            )

    def test_each_clear_label_has_min_samples(self):
        """每个明显档至少 5 条, 让均值稳定。"""
        from collections import Counter
        by_label = Counter(s["label"] for s in self.samples)
        self.assertGreaterEqual(by_label["sufficient"], 5)
        self.assertGreaterEqual(by_label["insufficient"], 5)


class CalibrationScoringTests(unittest.TestCase):
    """启发式 fallback 路径的排序校准 —— 5.6 上线门槛。"""

    @classmethod
    def setUpClass(cls):
        cls.samples = _load_dataset()
        cls.scored = [
            {**s, "sufficiency": _run_assessor(s)} for s in cls.samples
        ]

    def _by_label(self, label: str) -> list[float]:
        return [s["sufficiency"] for s in self.scored if s["label"] == label]

    def test_sufficient_mean_greater_than_insufficient_mean(self):
        """核心 pass 条件: sufficient 平均 sufficiency > insufficient 平均。"""
        suf = statistics.mean(self._by_label("sufficient"))
        insuf = statistics.mean(self._by_label("insufficient"))
        gap = suf - insuf
        print(
            f"\n[calibration] mean sufficiency: "
            f"sufficient={suf:.3f} insufficient={insuf:.3f} gap={gap:+.3f}",
        )
        if 0 < gap < 0.2:
            # weak pass: 排序对但区分度不够, 打 warning 但不挡 CI
            print(
                f"[calibration] WARNING: weak pass (gap={gap:.3f} < 0.2), "
                "启发式区分度偏弱, 等 LLM 路径上线再看是否需要校准。",
            )
        self.assertGreater(
            suf, insuf,
            "sufficient 平均 sufficiency 必须 > insufficient. "
            "改了 Assessor 启发式 / 数据集就要复跑这条。",
        )

    def test_per_category_ordering_holds(self):
        """每个 category 内部, sufficient 均值也应当 > insufficient 均值
        (不要一个 category 上 sufficient 把均值拉高, 其他 category 反着掉)。"""
        for cat in ("knowledge", "project_experience", "scenario"):
            # self_intro 是单一档 (Assessor 给 floor 0.9), 不进比较
            suf_scores = [
                s["sufficiency"] for s in self.scored
                if s["category"] == cat and s["label"] == "sufficient"
            ]
            insuf_scores = [
                s["sufficiency"] for s in self.scored
                if s["category"] == cat and s["label"] == "insufficient"
            ]
            if not suf_scores or not insuf_scores:
                continue  # 该 category 没标够 sufficient/insufficient 对
            suf = statistics.mean(suf_scores)
            insuf = statistics.mean(insuf_scores)
            self.assertGreater(
                suf, insuf,
                f"category={cat}: sufficient 均值 {suf:.3f} 应 > "
                f"insufficient 均值 {insuf:.3f}",
            )

    def test_self_intro_always_high(self):
        """self_intro 在 fallback 路径有 floor 0.9 兜底,
        与 FollowUpPolicy.for_stage(SELF_INTRO) max=0 形成双保险。"""
        intro_scores = [
            s["sufficiency"] for s in self.scored
            if s["category"] == "self_intro"
        ]
        self.assertTrue(intro_scores, "数据集应有 self_intro 样本")
        for s in intro_scores:
            self.assertGreaterEqual(
                s, 0.9,
                f"self_intro sufficiency 必须 >= 0.9, 实际 {s:.3f}",
            )

    def test_ambiguous_samples_recorded_not_asserted(self):
        """ambiguous 样本只打印不 assert, 给人工 review 看 Assessor 在
        模糊区是怎么 score 的。"""
        ambs = [s for s in self.scored if s["label"] == "ambiguous"]
        if ambs:
            print("\n[calibration] ambiguous samples (record only):")
            for s in sorted(ambs, key=lambda x: x["sufficiency"]):
                print(
                    f"  {s['id']:<12} cat={s['category']:<20} "
                    f"sufficiency={s['sufficiency']:.3f}",
                )
        # 不 assert; 这条测试存在的意义是让人看输出
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
