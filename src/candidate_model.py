"""CandidateModel 的纯规则更新 —— Sprint 8.4 task 1。

与 src.coverage / src.beliefs 同级的共享纯模块: 不调 LLM、不碰 DB/cache;
orchestrator 独家写 (blackboard), agent 只读。

题目级更新 (每份 AnswerAssessment 落地时):
- strengths -> verified (该 assessment 分数达标) / claimed (未达标)
- concerns  -> doubted
- Mem0 式 ADD/UPDATE/NOOP: 同 competency + 字符 2-gram Jaccard 相似度粗判
  同一条目 -> 合并 evidence (UPDATE/NOOP), 否则新增 (ADD)。
  向量检索区分不了"语义相近但结论相反" (Belief Memory 教训), 这里根本
  不用 embedding —— 相似度只做去重, 结论方向由 status 承载。
- 矛盾确定性标记: 同 competency 下 verified 条目与 doubted 条目文本相似
  度过阈 -> 双方 status=contradicted + 记入 contradictions 对, **保留双方
  evidence**。不让 LLM 仲裁谁对 (确定性规则, 同输入同输出)。

降级铁律: integrate_assessment 内部任何异常 -> 退化为只追加 (append-only),
绝不让记忆更新影响面试链路。
"""
from __future__ import annotations

import logging

from src.schemas import (
    AnswerAssessment,
    CandidateAnswer,
    CandidateModel,
    ClaimStatus,
    Question,
    SkillClaim,
)

log = logging.getLogger(__name__)

# 相似度 = 字符 2-gram overlap coefficient (交集/较短者)。不用 Jaccard:
# 中文里"掌握分库分表" vs "分库分表经验存疑"的 Jaccard 只有 ~0.33 (措辞
# 一正一反, 并集被撑大), overlap 是 0.6 —— 矛盾检测要的是"短语包含式的
# 同主题", overlap 更贴合。
# 合并阈值偏保守: 宁可多一条也不错并两条不同结论。
MERGE_SIMILARITY = 0.6
# 矛盾阈值更低: 双方措辞天然一正一反, 重叠只剩主题词。
CONTRADICTION_SIMILARITY = 0.3
# strengths 升 verified 的分数线: 与 FollowUpPolicy raw 阈值同源 (0.6 =
# 有效证据), 不引入新的量表
VERIFIED_MIN_SUFFICIENCY = 0.6


def _bigrams(text: str) -> set[str]:
    t = "".join(text.split())
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def similarity(a: str, b: str) -> float:
    """字符 2-gram overlap coefficient (中文友好), 纯函数供 eval。"""
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def integrate_assessment(
    model: CandidateModel,
    question: Question,
    answer: CandidateAnswer,
    assessment: AnswerAssessment,
) -> CandidateModel:
    """把一份 assessment 的 strengths/concerns 沉淀进 CandidateModel。
    返回新对象 (不原地改)。内部异常 -> 退化 append-only。"""
    incoming: list[SkillClaim] = []
    verified = assessment.sufficiency >= VERIFIED_MIN_SUFFICIENCY
    for s in assessment.strengths:
        if s.strip():
            incoming.append(SkillClaim(
                competency_id=question.competency_id,
                claim=s.strip(),
                status=ClaimStatus.VERIFIED if verified else ClaimStatus.CLAIMED,
                confidence=assessment.confidence,
                evidence=[answer.answer_id],
                source_stage=question.category.value,
            ))
    for c in assessment.concerns:
        if c.strip():
            incoming.append(SkillClaim(
                competency_id=question.competency_id,
                claim=c.strip(),
                status=ClaimStatus.DOUBTED,
                confidence=assessment.confidence,
                evidence=[answer.answer_id],
                source_stage=question.category.value,
            ))
    if not incoming:
        return model

    try:
        return _merge(model, incoming)
    except Exception:
        log.exception("candidate_model 合并失败, 退化 append-only")
        return model.model_copy(update={
            "claims": [*model.claims, *incoming],
        })


def _merge(model: CandidateModel, incoming: list[SkillClaim]) -> CandidateModel:
    claims = [c.model_copy(deep=True) for c in model.claims]
    contradictions = [list(p) for p in model.contradictions]
    flagged = {cid for pair in contradictions for cid in pair}

    for new in incoming:
        same_comp = [
            c for c in claims if c.competency_id == new.competency_id
        ]
        # 1) 去重合并: 同 competency + 同方向 + 文本高度相似 -> 并 evidence
        mergeable = next(
            (
                c for c in same_comp
                if _same_direction(c.status, new.status)
                and similarity(c.claim, new.claim) >= MERGE_SIMILARITY
            ),
            None,
        )
        if mergeable is not None:
            for ev in new.evidence:
                if ev not in mergeable.evidence:
                    mergeable.evidence.append(ev)
            # claimed 条目拿到达标佐证 -> 升 verified
            if (
                mergeable.status is ClaimStatus.CLAIMED
                and new.status is ClaimStatus.VERIFIED
            ):
                mergeable.status = ClaimStatus.VERIFIED
            continue

        claims.append(new)

        # 2) 矛盾确定性标记: verified/claimed vs doubted 讲同一件事
        if new.claim_id in flagged:
            continue
        opposite = (
            (ClaimStatus.VERIFIED, ClaimStatus.CLAIMED)
            if new.status is ClaimStatus.DOUBTED
            else (ClaimStatus.DOUBTED,)
            if new.status in (ClaimStatus.VERIFIED, ClaimStatus.CLAIMED)
            else ()
        )
        for other in same_comp:
            if other.status not in opposite or other.claim_id in flagged:
                continue
            if similarity(other.claim, new.claim) >= CONTRADICTION_SIMILARITY:
                other.status = ClaimStatus.CONTRADICTED
                new.status = ClaimStatus.CONTRADICTED
                contradictions.append([other.claim_id, new.claim_id])
                flagged.update((other.claim_id, new.claim_id))
                break

    return CandidateModel(claims=claims, contradictions=contradictions)


def _same_direction(a: ClaimStatus, b: ClaimStatus) -> bool:
    positive = (ClaimStatus.VERIFIED, ClaimStatus.CLAIMED)
    return (a in positive and b in positive) or (
        a is ClaimStatus.DOUBTED and b is ClaimStatus.DOUBTED
    )


def doubted_claims(
    model: CandidateModel, competency_id: str | None = None,
) -> list[SkillClaim]:
    """doubted/contradicted 条目 (可按 competency 过滤) —— lazy gen 与
    追问 focus 的消费入口 (task 2/3)。"""
    out = [
        c for c in model.claims
        if c.status in (ClaimStatus.DOUBTED, ClaimStatus.CONTRADICTED)
    ]
    if competency_id is not None:
        out = [c for c in out if c.competency_id == competency_id]
    return out
