"""competency_coverage 计算 —— Sprint 5.7。

定位:
- 纯计算, 无 I/O, 无 LLM。Interviewer 在线决策"是否提前结束"用它;
  Evaluator 在报告级写 EvaluationReport.competency_coverage 也用它。
- 单一实现避免两边漂移: 同一 (session, plan) 输入永远得到同一 coverage 字典。

规则 (per design point b 确认):
- 对 plan.competencies 每个 competency_id, 取 session.assessments 中
  question.competency_id == competency_id 的 assessments 的 max(sufficiency)。
- 该 competency 下没有 assessments -> coverage = 0.0 (有 plan 上的题但没回答评估)。
- session.assessments 是空 (ASSESSOR_ENABLED=false 或还没答题) -> 全 0.0。
- 老 plan (plan.competencies 顶层空) -> 返回 {} (短路, 让上游退化到旧行为)。

max 而非平均:
- 候选人在某一题打中即为该维度"有证据", 不需要每题都达标。
- 加权平均要题级权重, 5.7 不引入这个复杂度。
- 噪声敏感性 (一次假高分会拉满) 由 Assessor 的 confidence 兜底, 未来可改为
  "max where confidence >= 阈值" 二次过滤, schema 不变即可升级。
"""
from __future__ import annotations

from src.schemas import (
    AnswerAssessment,
    CompletionPolicy,
    InterviewPlan,
    InterviewSession,
    Question,
)


def compute_coverage(
    session: InterviewSession, plan: InterviewPlan,
) -> dict[str, float]:
    """返回 {competency_id: coverage ∈ [0, 1]}。
    plan.competencies 空 -> {} (短路, 老 plan 兼容)。"""
    if not plan.competencies:
        return {}

    # 建 question_id -> competency_id 查找表 (self_intro 题 competency_id 为 None,
    # 不进任何 competency 桶)
    q_to_comp: dict[str, str | None] = {}
    for r in plan.rounds:
        for q in r.questions:
            q_to_comp[q.question_id] = q.competency_id

    # 聚合: competency_id -> list[sufficiency]
    buckets: dict[str, list[float]] = {
        c.competency_id: [] for c in plan.competencies
    }
    for a in session.assessments:
        cid = q_to_comp.get(a.question_id)
        if cid is None or cid not in buckets:
            continue
        buckets[cid].append(a.sufficiency)

    return {
        cid: (max(vals) if vals else 0.0)
        for cid, vals in buckets.items()
    }


def mandatory_coverage_met(
    coverage: dict[str, float],
    policy: CompletionPolicy,
    plan: InterviewPlan,
) -> bool:
    """mandatory competency 是否全部达到 policy.min_competency_coverage。
    policy.mandatory_competencies 空 = plan.competencies 全部 mandatory (默认)。

    plan.competencies 空 (老 plan 短路): 返回 False, 让上游走"题答完就 done"
    的旧路径, 不被 CompletionPolicy 影响。"""
    if not plan.competencies:
        return False

    mandatory_ids: list[str]
    if policy.mandatory_competencies:
        mandatory_ids = list(policy.mandatory_competencies)
    else:
        mandatory_ids = [c.competency_id for c in plan.competencies]

    for cid in mandatory_ids:
        if coverage.get(cid, 0.0) < policy.min_competency_coverage:
            return False
    return True


def insufficient_competencies(
    coverage: dict[str, float],
    policy: CompletionPolicy,
    plan: InterviewPlan,
) -> list[str]:
    """返回 mandatory 中未达 min_competency_coverage 的 competency_id 列表;
    用于 Evaluator 标 evidence_insufficient + 在 summary 里点出哪几个维度不足。

    plan.competencies 空 -> [] (老 plan 不参与判定)。"""
    if not plan.competencies:
        return []

    if policy.mandatory_competencies:
        mandatory_ids = list(policy.mandatory_competencies)
    else:
        mandatory_ids = [c.competency_id for c in plan.competencies]

    return [
        cid for cid in mandatory_ids
        if coverage.get(cid, 0.0) < policy.min_competency_coverage
    ]


def total_questions_asked(session: InterviewSession) -> int:
    """已答题数 (按 session.answers 计数; followup 也计入, 与 next_turn 的
    总数判定口径一致)。CompletionPolicy.max_total_questions 用这个对比。"""
    return len(session.answers)
