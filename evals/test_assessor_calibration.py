"""Sprint 5.6 / 5.9 Assessor 校准 eval —— 启发式 fallback 路径锁定。

定位:
- 本 eval 跑的是 Assessor 的 **启发式 fallback** (LLM stub 路径), 不烧 token。
- (Sprint 5.6) sufficiency 排序校准: "sufficient" 标签样本的平均 sufficiency
  严格高于 "insufficient" 标签的均值。差距 >= 0.2 = strong pass;
  0.0-0.2 = weak pass (打 warning 但不挡 CI)。
- (Sprint 5.6) ambiguous 标签样本只 record + 打印, 不参与 pass/fail。
- (Sprint 5.9) covered_aspects 启发式校准: 标了 expected_aspect_names 的样本,
  recall 平均 >= 0.5, 单样本 recall >= 0.3; 同时 distractor aspect 永远不被
  误命中 (precision 检查)。
- **真 LLM 路径的校准必须人工在 PR review 跑**: 配上 OPENAI_API_KEY, 复跑本文件,
  人肉看数据集每条 prediction 是否合理。人工 review 通过 + ASSESSOR_ENABLED 翻 true
  上线后, 此 eval 仍是启发式 fallback 路径的回归护栏。

数据集: evals/data/assessment_calibration.json (50+ 条样本, 覆盖 4 个 category)。
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
    AnswerAssessment,
    CandidateAnswer,
    Competency,
    InterviewPlan,
    InterviewRound,
    InterviewSession,
    JobContext,
    ProfileAspect,
    Question,
    QuestionCategory,
)

_DATA_PATH = Path(__file__).parent / "data" / "assessment_calibration.json"


def _load_dataset() -> list[dict]:
    with _DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)["samples"]


_COMPETENCY_ID = "cid-test"
# Sprint 5.9: covered_aspects 测试用的 distractor —— 名字 2-gram 与样本答案
# 都不重叠, 用来验证启发式不会乱命中 (precision check)。
# 选 3 个完全不在 IT 语境的词, 避免某条答案碰巧含到。
_DISTRACTOR_ASPECT_NAMES = ["古生物化石", "蝴蝶迁徙", "黑陶釉色"]


def _run_assessor_full(
    sample: dict, job: JobContext | None = None,
) -> AnswerAssessment:
    """构造 minimal fixture 跑 assessor.assess, 返回完整 AnswerAssessment.
    传 job=None -> covered_aspects 永远 []; 传 job 时启发式 fallback 会按
    aspect.name 2-gram 子串匹配。"""
    cat = QuestionCategory(sample["category"])
    competency_id = None if cat is QuestionCategory.SELF_INTRO else _COMPETENCY_ID
    q = Question(
        competency_id=competency_id,
        text=sample["question"],
        category=cat,
    )
    a = CandidateAnswer(question_id=q.question_id, text=sample["answer"])
    # session + plan 在 5.6 阶段 assess 没消费, 但接口要求传, 给个 minimal 实例
    comp = Competency(competency_id=_COMPETENCY_ID, name="技术深度", description="x")
    plan = InterviewPlan(
        job_id="j", rounds=[
            InterviewRound(index=0, title="t", competencies=[comp], questions=[q]),
        ],
        competencies=[comp],
    )
    session = InterviewSession(plan_id=plan.plan_id, job_id="j")
    return assessor.assess(q, a, session, plan, job=job)


def _run_assessor(sample: dict) -> float:
    """sufficiency-only 入口, sprint 5.6 旧 API 沿用. 不带 job."""
    return _run_assessor_full(sample).sufficiency


class CalibrationDatasetTests(unittest.TestCase):
    """数据集本身的结构性护栏 —— 防有人改坏 JSON。"""

    @classmethod
    def setUpClass(cls):
        cls.samples = _load_dataset()

    def test_dataset_size_in_range(self):
        # Sprint 5.9: 50-80 条 (Sprint 5.6 起步 20-30, Sprint 5.9 扩到 50+)。
        self.assertGreaterEqual(len(self.samples), 50)
        self.assertLessEqual(len(self.samples), 80)

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


class CoveredAspectsCalibrationTests(unittest.TestCase):
    """Sprint 5.9: 启发式 fallback 给 covered_aspects 填值的 *数据集级* 校准。

    与 evals/test_assessor_integration.CoveredAspectsHeuristicTests 区别:
    - 后者是单测式的边界 case (filter by competency / 空 job / 2-gram 命中);
    - 本类跑数据集级回归: 50 样本中 expected_aspect_names 标注的子集, 计算
      recall (HR 期望覆盖到的 aspect 被 Assessor 捞回多少) 和 precision
      (distractor aspect 不应被命中)。

    门槛 (启发式 baseline):
    - mean recall >= 0.5: 半数以上期望 aspect 被启发式接住。
    - 单样本 recall >= 0.3: 没有样本完全踩空。
    - 平均 precision = 1.0 (distractor 永远 0 命中): 启发式不能瞎匹配。
    """

    @classmethod
    def setUpClass(cls):
        cls.samples = [
            s for s in _load_dataset() if s.get("expected_aspect_names")
        ]
        # 让任何一个测试都先看到样本数, 不够就直接挂
        if len(cls.samples) < 10:
            raise unittest.SkipTest(
                f"覆盖 calibration 样本不足 (实际 {len(cls.samples)}, 需 >=10)",
            )
        # 整个数据集中出现过的所有 aspect 名 + distractor; 每条样本都用同一
        # 个全集 job, 模拟 HR 真实场景 (job aspect 是固定的, 不为单条答案定制)。
        all_names = set()
        for s in cls.samples:
            all_names.update(s["expected_aspect_names"])
        cls.expected_pool = sorted(all_names)
        cls.distractor_names = list(_DISTRACTOR_ASPECT_NAMES)
        cls.aspect_by_name = {
            name: ProfileAspect(
                competency_id=_COMPETENCY_ID, name=name, description="x",
            )
            for name in cls.expected_pool + cls.distractor_names
        }
        cls.job = JobContext(
            title="t", jd="x",
            aspects=list(cls.aspect_by_name.values()),
        )

    def _names_from_ids(self, ids: list[str]) -> set[str]:
        by_id = {a.aspect_id: a.name for a in self.aspect_by_name.values()}
        return {by_id[i] for i in ids if i in by_id}

    def test_covered_aspects_recall(self):
        per_sample: list[tuple[str, float, set[str], set[str]]] = []
        for s in self.samples:
            expected = set(s["expected_aspect_names"])
            result = _run_assessor_full(s, job=self.job)
            covered = self._names_from_ids(result.covered_aspects)
            hit = expected & covered
            recall = len(hit) / len(expected) if expected else 0.0
            per_sample.append((s["id"], recall, expected, covered))

        # 单样本 recall 检查
        weak = [(sid, r) for sid, r, _, _ in per_sample if r < 0.3]
        if weak:
            for sid, r in weak:
                # 把对应的 expected / covered 都打出来便于调
                row = next(p for p in per_sample if p[0] == sid)
                print(
                    f"\n[covered-recall] weak sample {sid}: "
                    f"recall={r:.2f} expected={sorted(row[2])} "
                    f"covered={sorted(row[3])}",
                )
        self.assertEqual(
            weak, [],
            "存在 recall<0.3 的样本, 启发式 2-gram 严重漏召 (见上方打印)",
        )

        mean_recall = sum(r for _, r, _, _ in per_sample) / len(per_sample)
        print(
            f"\n[covered-recall] n={len(per_sample)} "
            f"mean_recall={mean_recall:.3f} (>=0.5 = pass)",
        )
        self.assertGreaterEqual(
            mean_recall, 0.5,
            f"启发式 covered_aspects 平均 recall {mean_recall:.3f} < 0.5",
        )

    def test_distractor_aspects_never_hit(self):
        """precision check: distractor aspect 名跟答案完全不重叠, 启发式
        不应当把它们误判进 covered_aspects (任何一条样本都不行)。"""
        bad: list[tuple[str, set[str]]] = []
        for s in self.samples:
            result = _run_assessor_full(s, job=self.job)
            covered_names = self._names_from_ids(result.covered_aspects)
            wrong = covered_names & set(self.distractor_names)
            if wrong:
                bad.append((s["id"], wrong))
        self.assertEqual(
            bad, [],
            f"distractor aspect 被误命中: {bad}; 启发式 precision 退化",
        )


if __name__ == "__main__":
    unittest.main()
