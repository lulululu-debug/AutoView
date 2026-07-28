"""Competency 信念状态的共轭更新 —— Sprint 8.3 Step 2。

与 src.coverage 同级的纯数值共享模块: 不调 LLM、不碰 DB/cache、
orchestrator 独家写入 (blackboard 模式), agent 只读。

模型: 高斯共轭 (已知观测噪声)。
- 先验 μ0=0.5, σ0²=0.25 (宽先验: 未观测时对候选人不设立场)
- 观测 = calibrated_sufficiency (P(信号充分), 仅真 LLM 路径产生),
  固定观测噪声 σ_obs²=0.09 (±0.3 —— LLM judge 单次判分的经验噪声量级)
- 后验精度线性累加 -> **方差只随观测数单调下降** (与 Beta 不同, 不受
  观测值影响), 且均值 = 精度加权和 -> **观测顺序不变性**。
  这两条是 evals/test_belief_update.py 钉死的数值性质。

为什么不用 Beta: Beta 方差在"意外观测"下会回升 (m(1-m) 项), 破坏
"方差 = 还需要多少证据"的调度语义; 高斯固定噪声下 variance 只由 n 决定,
`min_variance_to_probe` 阈值就有了清晰含义 (≈ 每维度问几次就够)。
参考: variance(n) = 1/(1/0.25 + n/0.09) -> n=1: 0.066, n=2: 0.038,
n=3: 0.026, n=5: 0.017。默认 min_variance_to_probe=0.03 ≈ 3 次观测后
该维度不再消耗追问预算。

合规: belief 数字与 assessment 同待遇 —— 不进 HR UI、不进报告展示,
仅 orchestrator 决策 + 落库审计 (CLAUDE.md)。
"""
from __future__ import annotations

from src.schemas import CompetencyBelief

PRIOR_MEAN = 0.5
PRIOR_VAR = 0.25
OBS_VAR = 0.09


def initial_belief(competency_id: str) -> CompetencyBelief:
    return CompetencyBelief(
        competency_id=competency_id,
        mean=PRIOR_MEAN,
        variance=PRIOR_VAR,
        n_observations=0,
    )


def update_belief(
    belief: CompetencyBelief | None,
    competency_id: str,
    observation: float,
) -> CompetencyBelief:
    """一次观测的共轭更新, 返回新对象 (不原地改)。observation ∈ [0,1]。"""
    if belief is None:
        belief = initial_belief(competency_id)
    tau = 1.0 / belief.variance
    tau_new = tau + 1.0 / OBS_VAR
    mean_new = (belief.mean * tau + observation / OBS_VAR) / tau_new
    return CompetencyBelief(
        competency_id=competency_id,
        mean=mean_new,
        variance=1.0 / tau_new,
        n_observations=belief.n_observations + 1,
    )


def lcb(belief: CompetencyBelief, k: float = 1.0) -> float:
    """置信下界 mean - k·√variance, 截断到 [0,1]。CompletionPolicy
    use_belief_lcb 开关消费 (比裸 max(sufficiency) 保守也更公平)。"""
    val = belief.mean - k * (belief.variance ** 0.5)
    return min(1.0, max(0.0, val))
