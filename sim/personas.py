"""仿真候选人 persona 库 —— Sprint 6.5 task 1。

设计:
- 核心 6 个: 强/中/弱 × campus/lateral。简历与答题风格**同向**
  (强简历配强回答), 让「区分度」指标有明确的期望排序: strong > medium > weak。
- 对抗 3 个: 复用 lateral-medium 的简历, 只换答题风格 —— 隔离变量,
  测的是面试系统对**作答行为**的鲁棒性, 不是对简历的。
- 简历刻意写成纯文本 (与真实候选人 paste 的形态一致), 中文, 300-500 字。

persona 是 sim 的内部契约, 不进 src/schemas (那里只放 agent 的输入输出)。
"""
from __future__ import annotations

from pydantic import BaseModel

from src.schemas import Track


class Persona(BaseModel):
    """一个仿真候选人 = 简历 + 答题风格。"""
    persona_id: str
    display_name: str
    track: Track
    level: str                # strong / medium / weak / adversarial
    resume: str
    answer_style: str         # 拼进候选人扮演 prompt 的风格指令

    # 区分度指标用: 期望排序权重, 越大越强 (对抗型不参与排序)
    expected_rank: int = 0


# ---- 答题风格指令 ----

_STYLE_STRONG = """回答风格要求:
- 具体、量化、有第一人称细节: 用真实数字 (QPS/P99/百分比/规模), 讲清楚
  你个人做了什么决策、为什么、结果如何 (STAR 结构)
- 主动暴露 trade-off 与踩过的坑, 体现工程判断力
- 每答 150-300 字, 紧扣问题, 不堆砌"""

_STYLE_MEDIUM = """回答风格要求:
- 能答对方向, 但细节深度一般: 说得出做了什么, 但量化数据少、决策理由浅
- 偶尔用"我们团队"带过个人贡献, 追问细节时能补一些但不完整
- 每答 80-150 字"""

_STYLE_WEAK = """回答风格要求:
- 模糊、泛泛、背概念: 多用"一般来说""应该是", 缺少第一人称经历与数据
- 被追问细节时开始含糊或绕圈, 偶有概念性错误 (如把乐观锁悲观锁说反)
- 每答 40-80 字"""

_STYLE_COPY_PASTE = """回答风格要求 (对抗测试 - 复制粘贴型):
- 每个回答都像从技术博客/教程复制的通用段落: 术语正确、结构完整、
  但**完全没有**第一人称经历、没有具体项目细节、没有个人决策
- 语气像百科条目, 不用"我", 与题目关键词沾边即可
- 每答 150-250 字"""

_STYLE_OFF_TOPIC = """回答风格要求 (对抗测试 - 跑题型):
- 开头一句沾一下题目, 然后迅速滑向无关话题 (职业规划/公司福利/行业八卦/
  抱怨前司), 不正面回答问题
- 每答 80-150 字"""

_STYLE_TERSE = """回答风格要求 (对抗测试 - 超短敷衍型):
- 极简敷衍: "做过""会的""就正常做呗""还行", 每答不超过 15 字
- 被追问也不展开"""


# ---- 简历文本 ----

_RESUME_LATERAL_STRONG = """张伟 | 后端工程师 | 6 年经验
工作经历:
- 某电商中台 (2021-至今) 高级后端: 负责订单与对账系统。主导订单库分库分表
  (32 库 256 表), 大促峰值 12w QPS, P99 从 800ms 降至 210ms; 设计对账平台
  日处理 3 亿笔, 漏对率 0.4‰ → 0.01‰; 带 3 人小组, 推动全链路压测落地。
- 某出行公司 (2019-2021) 后端: 计价服务重构, 规则引擎化, 需求交付周期 2 周 → 3 天。
技能: Java/Go, MySQL 内核参数调优, Redis 集群, Kafka, K8s。
项目亮点: 自研幂等键框架开源 (1.2k star), 内部故障演练平台发起人。"""

_RESUME_LATERAL_MEDIUM = """李强 | 后端工程师 | 4 年经验
工作经历:
- 某 SaaS 公司 (2022-至今) 后端: 参与 CRM 系统开发, 负责客户模块与工单模块的
  接口开发和维护; 参与过一次数据库慢查询优化, 配合 DBA 加索引。
- 某外包公司 (2020-2022): 各类企业管理系统 CRUD 开发。
技能: Java, Spring Boot, MySQL, Redis 基本使用, 了解 Kafka。
项目: 公司内部报表系统 (主要开发者), 用定时任务跑批生成日报。"""

_RESUME_LATERAL_WEAK = """王磊 | 后端开发 | 3 年经验
工作经历:
- 某小型软件公司 (2021-至今): 参与多个项目的后端开发, 主要写业务接口,
  用过 Spring Boot 和 MyBatis。
- 实习 (2020): 测试岗, 写过一些自动化脚本。
技能: Java 基础, MySQL 增删改查, 会用 Redis 存缓存, Git。
项目: 参与过商城项目和管理后台项目的部分模块。"""

_RESUME_CAMPUS_STRONG = """陈曦 | 2026 届硕士 | 计算机科学
教育: 某 985 高校计算机硕士, 本科同校, GPA 前 10%。
实习:
- 某大厂基础架构组 (2025.6-2025.12): 参与自研 KV 存储的 compaction 调度优化,
  写放大降低 23%; 独立完成慢请求根因分析工具, 组内推广使用。
论文/竞赛: CCF-A 会议二作一篇 (分布式一致性方向); ACM 区域赛银牌。
项目: 用 Rust 实现 Raft KV (带成员变更与快照), 单机压测 8w QPS;
课程项目实现了简化版 MapReduce。
技能: C++/Rust/Go, 熟悉 LSM-tree、Raft、常见并发模型。"""

_RESUME_CAMPUS_MEDIUM = """刘洋 | 2026 届本科 | 软件工程
教育: 某一本高校软件工程本科, GPA 中上。
实习:
- 某中厂业务组 (2025.7-2025.9): 参与营销活动页后端开发, 写了几个接口,
  修过线上小 bug。
项目: 课设做过外卖点餐系统 (Spring Boot + Vue); 和同学一起做过一个
校园二手交易小程序, 负责后端。
技能: Java 基础扎实, 了解 MySQL 索引和事务, 用过 Redis, 刷了 300 道算法题。"""

_RESUME_CAMPUS_WEAK = """赵鹏 | 2026 届本科 | 信息管理
教育: 某二本高校信息管理与信息系统本科。
实习: 无正式实习, 暑期在亲戚公司帮忙做过 Excel 数据整理。
项目: 跟着网课视频做过一个博客系统 (SSM 框架), 大部分代码参考教程;
毕设选题是图书管理系统。
技能: 会 Java 和 Python 基本语法, MySQL 简单查询, 正在学 Spring。"""


# ---- persona 实例 ----

CORE_PERSONAS: list[Persona] = [
    Persona(
        persona_id="lateral-strong", display_name="张伟", track=Track.LATERAL,
        level="strong", resume=_RESUME_LATERAL_STRONG,
        answer_style=_STYLE_STRONG, expected_rank=3,
    ),
    Persona(
        persona_id="lateral-medium", display_name="李强", track=Track.LATERAL,
        level="medium", resume=_RESUME_LATERAL_MEDIUM,
        answer_style=_STYLE_MEDIUM, expected_rank=2,
    ),
    Persona(
        persona_id="lateral-weak", display_name="王磊", track=Track.LATERAL,
        level="weak", resume=_RESUME_LATERAL_WEAK,
        answer_style=_STYLE_WEAK, expected_rank=1,
    ),
    Persona(
        persona_id="campus-strong", display_name="陈曦", track=Track.CAMPUS,
        level="strong", resume=_RESUME_CAMPUS_STRONG,
        answer_style=_STYLE_STRONG, expected_rank=3,
    ),
    Persona(
        persona_id="campus-medium", display_name="刘洋", track=Track.CAMPUS,
        level="medium", resume=_RESUME_CAMPUS_MEDIUM,
        answer_style=_STYLE_MEDIUM, expected_rank=2,
    ),
    Persona(
        persona_id="campus-weak", display_name="赵鹏", track=Track.CAMPUS,
        level="weak", resume=_RESUME_CAMPUS_WEAK,
        answer_style=_STYLE_WEAK, expected_rank=1,
    ),
]

# 对抗型: 同一份中等简历, 只换答题行为 —— 隔离变量
ADVERSARIAL_PERSONAS: list[Persona] = [
    Persona(
        persona_id="adv-copy-paste", display_name="李强", track=Track.LATERAL,
        level="adversarial", resume=_RESUME_LATERAL_MEDIUM,
        answer_style=_STYLE_COPY_PASTE,
    ),
    Persona(
        persona_id="adv-off-topic", display_name="李强", track=Track.LATERAL,
        level="adversarial", resume=_RESUME_LATERAL_MEDIUM,
        answer_style=_STYLE_OFF_TOPIC,
    ),
    Persona(
        persona_id="adv-terse", display_name="李强", track=Track.LATERAL,
        level="adversarial", resume=_RESUME_LATERAL_MEDIUM,
        answer_style=_STYLE_TERSE,
    ),
]

ALL_PERSONAS: dict[str, Persona] = {
    p.persona_id: p for p in CORE_PERSONAS + ADVERSARIAL_PERSONAS
}


def select(spec: str) -> list[Persona]:
    """按 CLI --personas 选择: core / adversarial / all / 逗号分隔的 id 列表。"""
    if spec == "core":
        return list(CORE_PERSONAS)
    if spec == "adversarial":
        return list(ADVERSARIAL_PERSONAS)
    if spec == "all":
        return CORE_PERSONAS + ADVERSARIAL_PERSONAS
    chosen = []
    for pid in spec.split(","):
        pid = pid.strip()
        if pid not in ALL_PERSONAS:
            raise SystemExit(
                f"未知 persona: {pid!r}; 可用: {', '.join(ALL_PERSONAS)}",
            )
        chosen.append(ALL_PERSONAS[pid])
    return chosen
