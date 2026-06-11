"""骨架入口: 写死 JobContext + CandidateProfile + 候选人回答, 跑通整条链路, 打印 EvaluationReport。

运行: python -m src.main
"""
from __future__ import annotations

from src.orchestrator import run_interview
from src.schemas import CandidateProfile, JobContext

_JD = """我们正在招聘一名后端工程师, 负责核心交易系统的稳定性与性能。
需要熟悉分布式系统、数据库优化, 并能与产品、SRE 紧密协作推动落地。"""

_RESUME = """张三 / 后端工程师 / 4 年经验
- 2024-2025  某电商公司 高级后端
  负责订单与支付链路稳定性; 主导大促前的容量评估与缓存改造,
  P99 由 800ms 降至 350ms; 推动跨团队风控规则的灰度上线流程。
- 2022-2024  某金融科技 后端
  从 0 到 1 搭建对账中台, 日处理 2 亿笔流水; 与产品/SRE 联合定义 SLO。
- 实习:    2021  某云厂商 PaaS 团队
  参与多租户 K8s Operator 开发, 提交 3 个上游 PR。"""

# 5 条按顺序作答的候选人文本(2 道基础知识题 + 2 道项目深挖题, 其中 Q1 短答触发一次追问):
# Q1 基础知识(技术) 短答 -> 追问 -> Q2 基础知识(沟通) -> Q3 项目(技术) -> Q4 项目(沟通)
_CANDIDATE_ANSWERS = [
    "做过一些性能优化, 主要是慢查询和缓存。",
    "比如去年大促前, 订单查询 P99 从 800ms 涨到 2s。"
    "我们排查发现是某个复合索引被改后失效, 同时 Redis 出现热点 key 击穿。"
    "我加回索引并改造为本地缓存 + Redis 二级缓存, 最终 P99 回到 350ms。",
    "通常我会先用数据让对方理解我担心的点, 比如拉一份线上回放或历史 case,"
    "再一起定义可灰度的中间方案; 我们组上半年的风控规则争议就是这么收的。",
    "对账中台那段最有挑战。日处理 2 亿笔, 早期对账延迟超过 30 分钟。"
    "我们把单表对账改成分桶 + 并行 worker, 引入幂等键, 用 Kafka 做回放,"
    "结果延迟降到 3 分钟, 漏对率从 0.4‰ 降到 0.02‰。",
    "上半年风控那次, 产品要求 24 小时内全量, 我担心误杀率。"
    "我拉了 SRE 一起跑离线回放, 用数据让产品同意先灰度 5%, 一周后再全量,"
    "误杀率从 3.1% 降到 0.4% 才放开。",
]


def main() -> None:
    job = JobContext(
        title="后端工程师",
        jd=_JD,
        requirements=[
            "3 年以上后端工程经验",
            "熟悉分布式系统与数据库优化",
            "良好的跨职能沟通能力",
        ],
        company_materials="一家以交易系统为核心的公司, 强调稳定性与数据驱动决策。",
    )
    candidate = CandidateProfile(
        resume=_RESUME,
        projects=[
            "电商订单/支付链路 P99 优化",
            "对账中台从 0 到 1",
            "K8s Operator 实习",
        ],
    )
    report = run_interview(job, candidate, _CANDIDATE_ANSWERS)
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
