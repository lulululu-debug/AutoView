"""Planner Agent — 根据 JobContext + CandidateProfile 生成 InterviewPlan。

Sprint 5.5 起按 JobContext.track 输出 stage 序列:
- campus (校招): [self_intro, knowledge x3, project x2 lazy, scenario x1]   ≈ 7 题
- lateral (社招): [self_intro, project x3 lazy, scenario x2, knowledge x1] ≈ 7 题

stage 内题目特性:
- self_intro: 固定文本, competency_id=None, 永不进 content_scores
- knowledge:  Milvus questions(category=knowledge) 召回 + LLM 精修, 复用旧路径
- project:    plan 阶段只占位 (lazy=True, text=""), 进 project stage 时由
              `resolve_lazy_questions` 用 Resume RAG (+ session.intro_text, task 4)
              现场回灌 text + source_chunk_ids; competency 槽位在 plan 阶段就预定,
              生成只换内容不换 competency_id
- scenario:   Milvus questions(category=scenario) 召回 + LLM 精修, 与 knowledge 同形
              但拉的是 scenario 题库

Plan 顶层 `plan.competencies` 是权威列表 (跨 stage 共享, Evaluator 走顶层);
round.competencies 保留为该 stage 涉及的子集, 供 HR 阶段视图展示。

Sprint 0/3 的"2 dim × 2 cat × 4 题"路径 Sprint 5.5 起覆盖式退役, 无 fallback,
让数据契约只有一条真实路径。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from src import db, embeddings, llm, vector_store
from src.agents.planner import skill_extraction, topic_match
from src.schemas import (
    RESUME_DEEPDIVE_TYPES,
    CandidateProfile,
    Competency,
    InterviewPlan,
    InterviewRound,
    InterviewStage,
    JobContext,
    PlanTrace,
    ProfileAspect,
    Question,
    QuestionCategory,
    QuestionTrace,
    QuestionType,
    ResumeSection,
    Track,
)

log = logging.getLogger(__name__)

_KNOWLEDGE_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位与考察维度, 生成一道开放式、可深挖的中文【基础知识】面试题。"
    "若给定了【主题】, 题目必须严格落在该主题范围内, 只考该主题的基础知识, "
    "不要为了贴合岗位而跑到主题以外或编造与主题无关的概念。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

# Sprint H: 纯 LLM 出题 (question_source=llm_direct) —— 好题 rubric + few-shot。
# 与 RAG 路径 (_KNOWLEDGE_RAG_SYSTEM) 完全隔离; rubric 让 LLM"理解什么是好题",
# few-shot 给出正/反示范定调。改这里不影响现有 RAG 流程。
_GOOD_QUESTION_RUBRIC = (
    "好的面试题标准:\n"
    "1. 开放、可深挖: 不是一句话能答完的是非/定义题, 能顺着追问 2-3 层。\n"
    "2. 单一核心考点: 一道题只考一个清晰的知识对象, 不塞多个不相关的点。\n"
    "3. 考理解而非记忆: 避免可以直接背诵/搜索到标准答案的死题 "
    "(如\"HTTP 状态码 200 是什么意思\"), 倾向考权衡、原理、边界 "
    "(如\"为什么 HTTP/2 用二进制分帧而不是继续用文本协议\")。\n"
    "4. 自包含: 候选人不看任何额外材料, 仅凭题干就知道在问什么。\n"
    "5. 贴合但不生硬: 结合岗位与候选人技能确定考察方向, 但不硬塞岗位关键词凑数。\n"
)

_KNOWLEDGE_LLM_SYSTEM = (
    "你是一名资深技术面试设计专家。请针对给定的【考察维度 + 候选人技能】"
    "直接生成一道开放式中文【基础知识】面试题。\n"
    + _GOOD_QUESTION_RUBRIC +
    "示范 (好): 候选人技能含 Redis → "
    "\"Redis 的持久化有 RDB 和 AOF 两种, 它们在数据安全性和恢复速度上各有"
    "什么取舍? 你会怎么按业务选?\"\n"
    "示范 (差, 太死板可背诵): \"Redis 默认端口是多少?\"\n"
    "示范 (差, 多考点混杂): \"讲讲 Redis 的持久化、集群和分布式锁。\"\n"
    "若给定了【主题】, 题目必须严格落在该主题范围内。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

_SCENARIO_LLM_SYSTEM = (
    "你是一名资深技术面试设计专家。请针对给定的【考察维度 + 候选人技能】"
    "直接生成一道中文【场景题】: 给一个具体、贴近真实工作的情境, "
    "让候选人现场推理决策, 而不是回顾经历或背概念。\n"
    + _GOOD_QUESTION_RUBRIC +
    "场景题额外要求: 情境要具体到能让人代入 (有触发条件、约束、目标), "
    "问的是\"你会怎么做/怎么排查/怎么权衡\"。\n"
    "示范 (好): \"线上一个高频接口 P99 从 200ms 突然涨到 2s, 但 QPS 没变、"
    "错误率也正常。你会按什么顺序排查? 为什么?\"\n"
    "示范 (差, 成了知识题): \"什么是数据库索引?\"\n"
    "只输出题目本身, 不要任何解释或前后缀。"
)

# Sprint H: 校招/社招出题基调 —— 拼在 llm_direct 的 system prompt 尾部,
# 让同一套 rubric 按 track 侧重不同。校招重概念/基础/理解, 社招重实战/深度/权衡。
_CAMPUS_TONE = (
    "\n本次是【校招】面试: 候选人多为应届/在校生, 生产实战经验有限。"
    "出题请侧重**基础概念的清晰度、核心原理与机制的理解、以及'为什么这样设计'"
    "的思考**, 不要求大规模生产环境的实战经历。可以问对某个机制的理解、"
    "基础概念之间的区别与联系、某个设计选择背后的原因。难度基调中等, "
    "重在考清楚候选人是否**真正理解**而非死记硬背。"
)
_LATERAL_TONE = (
    "\n本次是【社招】面试: 候选人有工作经验。"
    "出题请侧重**实战深度、系统设计权衡、生产环境的踩坑与取舍**。"
    "倾向问'你在真实项目里会怎么做/怎么权衡/遇到 X 时怎么处理', "
    "考察工程判断与深度, 而非教科书式的概念复述。难度基调偏高。"
)


def _track_tone(track: Track) -> str:
    return _CAMPUS_TONE if track == Track.CAMPUS else _LATERAL_TONE

_KNOWLEDGE_RAG_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "下面给出一道从题库中召回的候选题。你的任务是**润色措辞**, 不是换题:\n"
    "- 必须保持原题考察的**知识对象/主题完全不变** —— 原题考 MCP 就仍考 MCP, "
    "考单元测试就仍考单元测试, 绝不能改写成考别的东西。\n"
    "- 题目必须**自包含**: 候选人没读过任何资料原文, 仅凭题干就能明白在问什么。"
    "题库题源自文档切片, 常残留只有读过原文才懂的悬空指代 (如 \"Agent A\"、"
    "\"该协议\"、\"上述方法\"、\"这种模式\"、\"文中示例\") —— 必须把这类指代"
    "改写为明确的通用表述, 原题依赖的必要背景用一句话在题干内补齐 "
    "(例: \"在多 Agent 工作流程中, Agent A 的角色是什么?\" → "
    "\"在多 Agent 协作流程中, 负责接收用户请求并拆解分发任务的协调 Agent "
    "承担哪些职责?\")。补齐背景时不得改变考点, 也不得把答案写进题干。\n"
    "- 若提供了'题目主题域', 用它判断题干术语的真实所指并补齐限定语 "
    "(例: 主题域为 Agent Skill 时, 题干裸写的 \"Skill\" 应明确为 "
    "\"AI Agent 的 Skill 能力包\", 不要泛化成\"技能\"); 主题域只用于消歧, "
    "不要把主题域名称原样生硬拼进不需要它的题干。\n"
    "- 禁止为了'贴合岗位'把无关的岗位关键词 (如岗位名里的技术名词) 硬塞进题目; "
    "岗位信息只用来决定语言风格与难度基调, 不改变考点。\n"
    "- 若原题已经清晰且自包含, 可以原样输出。\n"
    "只输出最终题目本身, 不要任何解释或前后缀。"
)

_SCENARIO_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位与考察维度, 生成一道开放式中文【场景题】(给一个具体情境, "
    "让候选人现场推理决策, 而不是回顾经历)。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

_SCENARIO_RAG_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "下面给出一道从场景题库中召回的候选题, 请润色措辞让情境更自然, 但:\n"
    "- 保持原题的情境结构 (具体场景 + 问候选人怎么做), 不要改回知识题或经历题。\n"
    "- 保持原题考察的**核心问题不变**, 禁止把无关的岗位关键词硬塞进情境; "
    "岗位信息只用来决定语言风格, 不改变考点。\n"
    "- 情境必须**自包含**: 候选人没读过任何资料原文, 仅凭题干就能理解情境是什么、"
    "要回答什么。题库题源自文档切片, 常残留只有读过原文才懂的悬空指代 "
    "(如 \"该系统\"、\"上述方案\"、\"这种模式\") —— 必须改写为明确表述, "
    "情境依赖的必要背景用一句话补进题干; 补齐背景时不得改变考点, "
    "也不得把期望的处理方案写进题干。\n"
    "- 若提供了'题目主题域', 用它判断题干术语的真实所指并补齐限定语; "
    "主题域只用于消歧, 不要把主题域名称原样生硬拼进题干。\n"
    "只输出最终题目本身, 不要任何解释或前后缀。"
)

_PROJECT_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位、考察维度与候选人简历, 生成一道针对候选人具体项目/实习经历的中文深挖题。"
    "题目必须指向简历里的具体内容(项目、技术栈、角色或结果), 不要泛泛而问。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

_PROJECT_RAG_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "下面给出从候选人 Resume 中召回的若干相关片段。"
    "请围绕这些具体内容生成一道针对候选人项目/实习经历的中文深挖题。"
    "题目必须指向片段中的具体项目、技术栈、角色或结果, 不要泛泛而问, 也不要重复片段原文。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

_PROJECT_SECTION_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "下面给出候选人简历中的【一段具体项目/实习/工作经历】原文。"
    "请只针对这一段经历生成一道中文深挖题:\n"
    "- 题干必须点名该经历 (项目名/公司名), 让候选人不看任何材料也知道在问哪段经历\n"
    "- 深挖这段经历的具体细节: 技术决策、难点、候选人的角色、结果与量化指标\n"
    "- 不要把这段经历之外的项目扯进来, 不要泛泛而问, 不要重复原文\n"
    "- 若提供了候选人自我介绍, 可结合其中与这段经历相关的说法追问\n"
    "只输出题目本身, 不要任何解释或前后缀。"
)

# RAG 召回的候选题数量。当前只取 top-1 给 LLM 精修, 多召回纯为日后做
# diversity / 多轮选题留扩展位; 取 3 是个折中, 也方便日志里看到 runner-up。
_RAG_TOP_K = 3

# Sprint E: knowledge 大池 (无 topic 匹配) 检索的 COSINE 距离硬阈值。
# 超阈值 → 退纯 LLM 生成。实测: 相关 agent 题 0.45, 不相关 javaguide 题 0.50+,
# 0.49 分得开。仅作用于 topic is None 的大池 (小池距离跨 topic 不可比, 见
# _retrieve_seed_question docstring)。env KNOWLEDGE_BIGPOOL_MAX_DISTANCE 可调,
# 设为空 / 非法 / <=0 → None (关闭硬过滤, 回到旧行为)。
_DEFAULT_BIGPOOL_MAX_DISTANCE = 0.49


def _bigpool_max_distance() -> float | None:
    raw = os.environ.get("KNOWLEDGE_BIGPOOL_MAX_DISTANCE")
    if raw is None:
        return _DEFAULT_BIGPOOL_MAX_DISTANCE
    raw = raw.strip()
    if not raw:
        return None  # 显式空串 = 关闭硬过滤
    try:
        v = float(raw)
    except ValueError:
        return _DEFAULT_BIGPOOL_MAX_DISTANCE
    return v if v > 0 else None

_SELF_INTRO_TEXT = (
    "请用 2-3 分钟做个自我介绍, 重点讲你最近一段经历里你做的关键决策、"
    "你遇到的最大挑战, 以及最终的结果。"
)


# ---------- stage 配比 (硬编码 by (track, role_family); 5.9 起从 7 题升到 ~20-22 主问题) ----------

@dataclass(frozen=True)
class _StageSlot:
    """单 stage 内一个题目槽位: 哪个 competency, 走哪类题源。"""
    competency_key: str | None    # "tech" / "comm" / None (self_intro)
    category: QuestionCategory


@dataclass(frozen=True)
class _StageQuota:
    """一个 stage 的题数配比 (Sprint 5.9 起): tech/comm 分别要几题。
    SELF_INTRO stage 只用 self_intro_count=1, 其他 stage 全 0。"""
    stage: InterviewStage
    category: QuestionCategory
    tech_count: int = 0
    comm_count: int = 0
    self_intro_count: int = 0

    def to_slots(self) -> list[_StageSlot]:
        slots: list[_StageSlot] = []
        for _ in range(self.self_intro_count):
            slots.append(_StageSlot(None, self.category))
        for _ in range(self.tech_count):
            slots.append(_StageSlot("tech", self.category))
        for _ in range(self.comm_count):
            slots.append(_StageSlot("comm", self.category))
        return slots


# ----- 4 个 stage 配比 (track × tech-or-not) -----
# 设计意图:
# - 总主问题 ~20-22 道, 含追问 max 25-30 由 CompletionPolicy 控制
# - 校招: knowledge 重 (考基本功), project 轻 (实习经历少)
# - 社招: project + scenario 重 (考实战), knowledge 轻 (基本功默认有)
# - 技术岗 comm: 校招 1 题, 社招 2 题 (用户原话)
# - 非技术岗 comm: 占主体 (产品/HR 沟通能力是核心考察)

_TECH_CAMPUS: list[_StageQuota] = [
    _StageQuota(InterviewStage.SELF_INTRO, QuestionCategory.SELF_INTRO, self_intro_count=1),
    _StageQuota(InterviewStage.KNOWLEDGE, QuestionCategory.KNOWLEDGE, tech_count=11, comm_count=1),
    _StageQuota(InterviewStage.PROJECT,   QuestionCategory.PROJECT_EXPERIENCE, tech_count=5),
    _StageQuota(InterviewStage.SCENARIO,  QuestionCategory.SCENARIO, tech_count=3),
]  # 1 + 12 + 5 + 3 = 21

_TECH_LATERAL: list[_StageQuota] = [
    _StageQuota(InterviewStage.SELF_INTRO, QuestionCategory.SELF_INTRO, self_intro_count=1),
    _StageQuota(InterviewStage.PROJECT,   QuestionCategory.PROJECT_EXPERIENCE, tech_count=10, comm_count=1),
    _StageQuota(InterviewStage.SCENARIO,  QuestionCategory.SCENARIO, tech_count=5, comm_count=1),
    _StageQuota(InterviewStage.KNOWLEDGE, QuestionCategory.KNOWLEDGE, tech_count=4),
]  # 1 + 11 + 6 + 4 = 22 (lateral 顺序与 Sprint 5.5 一致: project 先, knowledge 最后)

_NON_TECH_CAMPUS: list[_StageQuota] = [
    _StageQuota(InterviewStage.SELF_INTRO, QuestionCategory.SELF_INTRO, self_intro_count=1),
    _StageQuota(InterviewStage.KNOWLEDGE, QuestionCategory.KNOWLEDGE, tech_count=3, comm_count=5),
    _StageQuota(InterviewStage.PROJECT,   QuestionCategory.PROJECT_EXPERIENCE, tech_count=2, comm_count=6),
    _StageQuota(InterviewStage.SCENARIO,  QuestionCategory.SCENARIO, comm_count=4),
]  # 1 + 8 + 8 + 4 = 21

_NON_TECH_LATERAL: list[_StageQuota] = [
    _StageQuota(InterviewStage.SELF_INTRO, QuestionCategory.SELF_INTRO, self_intro_count=1),
    _StageQuota(InterviewStage.PROJECT,   QuestionCategory.PROJECT_EXPERIENCE, tech_count=3, comm_count=8),
    _StageQuota(InterviewStage.SCENARIO,  QuestionCategory.SCENARIO, comm_count=6),
    _StageQuota(InterviewStage.KNOWLEDGE, QuestionCategory.KNOWLEDGE, tech_count=1, comm_count=3),
]  # 1 + 11 + 6 + 4 = 22


_TECH_ROLE_FAMILIES = frozenset({"backend", "frontend", "data_science"})
_NON_TECH_ROLE_FAMILIES = frozenset({"product", "hr"})


def _is_tech_role(role_family: str) -> bool:
    """role_family 不在已知列表时默认按"技术岗"处理 (沿用 backend 配比),
    避免 HR 写错字段名时整轮挂掉。"""
    if role_family in _NON_TECH_ROLE_FAMILIES:
        return False
    return True


_STAGE_TITLES: dict[InterviewStage, str] = {
    InterviewStage.SELF_INTRO: "自我介绍",
    InterviewStage.KNOWLEDGE: "基础知识",
    InterviewStage.PROJECT: "项目深挖",
    InterviewStage.SCENARIO: "场景题",
}


def _stage_config_for(track: Track, role_family: str) -> list[_StageQuota]:
    """按 (track, role_family) 取 stage 配比 4 组之一。"""
    is_tech = _is_tech_role(role_family)
    if track is Track.CAMPUS:
        return _TECH_CAMPUS if is_tech else _NON_TECH_CAMPUS
    return _TECH_LATERAL if is_tech else _NON_TECH_LATERAL


def _stages_for(
    track: Track, role_family: str,
) -> list[tuple[InterviewStage, list[_StageSlot]]]:
    """Sprint 5.9: stage 序列从 _stage_config_for 派生, slot 列表由 quota 展开。"""
    return [
        (q.stage, q.to_slots()) for q in _stage_config_for(track, role_family)
    ]


# ---------- 默认 aspect 模板 (per role_family, HR 不配 aspects 时用) ----------
#
# aspect = (name, description). description 给 Assessor LLM 看, 帮 prompt
# 判断"这条回答是否覆盖了此 aspect"。
# HR 可以在 UI 上以 default 为底, 增删改名.

_DEFAULT_ASPECTS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "backend": {
        "tech": [
            ("分布式系统设计", "对 CAP / 一致性 / 分区容错的权衡和实践"),
            ("性能优化", "P99 优化、慢查询、缓存命中率、热点 key 等具体案例"),
            ("数据库与缓存", "MySQL 索引、事务隔离、Redis 数据结构与使用模式"),
            ("故障定位 oncall", "incident 应对、链路追踪、复盘机制"),
            ("高并发与限流", "QPS 设计、限流方案、削峰填谷"),
            ("技术选型权衡", "成本 / 性能 / 可维护性 三角的取舍"),
            ("代码质量与测试", "单测、集成测试、CI/CD"),
            ("可观测性", "指标、日志、tracing、告警"),
        ],
        "comm": [
            ("跨职能沟通", "跟 PM/SRE/DBA/前端 推方案"),
            ("推动落地", "克服阻力把方案推到上线"),
            ("冲突解决", "技术争议怎么收"),
            ("知识传递", "复盘文档、code review、培训新人"),
        ],
    },
    "frontend": {
        "tech": [
            ("浏览器渲染原理", "DOM / 重排重绘 / 合成层"),
            ("框架原理", "React/Vue 响应式、虚拟 DOM diff"),
            ("状态管理", "Redux/MobX/Context 取舍"),
            ("打包与工程化", "Webpack/Vite、tree-shaking、lazy load"),
            ("移动端兼容与响应式", "多端适配、不同屏宽断点"),
            ("可访问性 a11y", "WCAG、语义化标签"),
            ("前端性能", "FCP/LCP/CLS、首屏优化"),
            ("前端安全", "XSS、CSRF、CSP"),
        ],
        "comm": [
            ("跨职能沟通", "跟 后端 / 设计 / 产品 推方案"),
            ("推动落地", "克服阻力把方案推到上线"),
            ("冲突解决", "技术争议怎么收"),
            ("知识传递", "复盘文档、code review、培训新人"),
        ],
    },
    "data_science": {
        "tech": [
            ("机器学习基础", "监督 / 非监督、损失函数、评估指标"),
            ("特征工程", "缺失值、编码、标准化、特征筛选"),
            ("模型选型与调优", "网格搜索、交叉验证、过拟合处理"),
            ("大规模数据处理", "Spark/Flink、分布式 ETL"),
            ("AB 测试与统计推断", "假设检验、显著性、功效"),
            ("可重现实验", "MLflow、数据版本管理、参数追溯"),
            ("业务指标转化", "把业务问题翻成 ML 目标"),
            ("线上模型监控", "drift 检测、retrain、降级策略"),
        ],
        "comm": [
            ("跨职能沟通", "跟 业务 / 后端 / 产品 推方案"),
            ("推动落地", "克服阻力把模型推到上线"),
            ("结论说服力", "用数据 + 故事说服 stakeholder"),
            ("知识传递", "实验文档、code review、培训新人"),
        ],
    },
    "product": {
        "tech": [
            ("数据驱动决策", "指标设计、A/B 测试解读"),
            ("用户体验思维", "用户旅程、痛点拆解"),
            ("竞品分析与差异化", "市场切入点、定位"),
        ],
        "comm": [
            ("跨部门协作", "跟 研发 / 设计 / 运营 推方案"),
            ("需求拆解与表达", "PRD、优先级排序"),
            ("用户研究与反馈", "访谈、问卷、行为分析"),
            ("决策权衡与说服", "数据 + 故事说服 stakeholder"),
            ("快速学习与适应", "新领域上手能力"),
            ("冲突处理与平衡", "需求争议、资源博弈"),
        ],
    },
    "hr": {
        "tech": [
            ("人才战略与规划", "团队规划、headcount、人才地图"),
            ("绩效与激励", "OKR、360、调薪、晋升体系"),
            ("招聘流程设计", "JD、面试官培训、offer 决策"),
        ],
        "comm": [
            ("候选人体验", "面试官培训、沟通话术、offer 沟通"),
            ("内部宣贯", "文化、政策、变革管理"),
            ("冲突调解", "员工纠纷、离职面谈"),
            ("信任建立", "薪酬保密、敏感信息处理"),
            ("跨职能影响力", "推动业务部门接受 HR 决策"),
        ],
    },
}


def default_aspects_for_role(role_family: str) -> list[ProfileAspect]:
    """Sprint 5.9: HR UI 友好的封装 —— 不必传 competencies, 内部用稳定的
    tech / comm 两个维度 (COMPETENCY_TECH_ID / COMPETENCY_COMM_ID) 调
    default_aspects_for. 用于 GET /jobs/aspects-template/{role_family}."""
    tech, comm = _build_competencies()
    return default_aspects_for(role_family, [tech, comm])


def default_aspects_for(
    role_family: str, competencies: list[Competency],
) -> list[ProfileAspect]:
    """Sprint 5.9: HR 不在新建 job 时配 aspect 时, 用 role_family 默认模板。
    把 (name, description) 模板挂到具体 competency_id 上。
    role_family 不在已知列表时默认用 backend 模板。
    competencies 期待至少有 1 个 name 含"技术"的 + 1 个 name 含"沟通"的。"""
    template = _DEFAULT_ASPECTS.get(role_family) or _DEFAULT_ASPECTS["backend"]
    aspects: list[ProfileAspect] = []
    for comp in competencies:
        key = "tech" if ("技术" in comp.name or "深度" in comp.name) else "comm"
        for (name, desc) in template.get(key, []):
            aspects.append(ProfileAspect(
                competency_id=comp.competency_id,
                name=name,
                description=desc,
            ))
    return aspects


# ---------- knowledge: 召回 + 精修 (复用 Sprint 3 路径) ----------

def _retrieve_seed_question(
    role_family: str,
    competency: Competency,
    jd_excerpt: str,
    *,
    category: QuestionCategory,
    exclude_ids: set[str] | frozenset[str] = frozenset(),
    topic: str | None = None,
    difficulty: str | None = None,
    max_distance: float | None = None,
) -> dict | None:
    """从 Milvus 召回候选题, 排除 exclude_ids 后取最相似的一道。
    失败 / 空 / 全被排除时返 None (调用方退到纯 LLM 生成路径)。
    Sprint 5.5: category 让 knowledge / scenario 各拉各的题源。
    Sprint B+D: 可选 topic / difficulty 硬过滤, 让"先匹配 topic 再按难度召回"
    成为单次 Milvus 调用 (而非召回后过滤)。

    exclude_ids = 本 plan 已用过的 source_question_id。此前用 rank % top_k
    轮转 (Sprint 5.9 patch), 但题库该 (topic, difficulty) 下只有少数 seed 时
    轮转会循环命中同一批, LLM 精修产出"同一道题的三种说法" (实战 bug)。
    排除后 seed 耗尽 → 返 None 走纯 LLM 生成, 宁可现场生成也不复读。
    top_k 随排除集变大, 保证后面的槽位仍有候选可挑。

    max_distance (Sprint E): COSINE 距离硬阈值, 最近的可用题超阈值 → 返 None
    (退纯 LLM 生成)。**只对无 topic 过滤的大池检索有意义** —— 大池 query 一致,
    距离可比 (相关 agent 题 0.45, 不相关 javaguide 题 0.50+, 阈值 0.49 分得开);
    topic 命中的小池距离受 query 措辞影响、跨 topic 不可比 (MCP 完全匹配也 0.6+),
    传阈值会把正确匹配误杀, 故调用方仅在 topic is None 时传。"""
    query_text = (
        f"考察维度: {competency.name} - {competency.description}\n"
        f"JD 摘要: {jd_excerpt}"
    )
    vec = embeddings.embed(query_text)
    if embeddings.is_stub_vector(vec):
        return None
    try:
        hits = vector_store.search_questions(
            embedding=vec,
            top_k=_RAG_TOP_K + len(exclude_ids),
            role_family=role_family,
            competency=competency.name,
            category=category.value,
            topic=topic,
            difficulty=difficulty,
        )
    except vector_store.MilvusNotConfigured:
        log.info("Milvus 未配置, %s 题走 fallback 路径", category.value)
        return None
    except Exception:
        # 网络抖动 / Milvus 报错 / schema drift 都不应让面试卡死, 静默退到现场生成
        log.exception("%s 召回失败, 走 fallback", category.value)
        return None
    for hit in hits:
        if hit["question_id"] in exclude_ids:
            continue
        # hits 按距离升序: 第一个非排除的即最近可用题, 它超阈值就没有更近的了
        if max_distance is not None and hit.get("distance", 0.0) > max_distance:
            log.info(
                "%s 大池最近题 dist=%.3f > %.2f, 退纯 LLM 生成",
                category.value, hit.get("distance", 0.0), max_distance,
            )
            return None
        return hit
    return None


def _format_prior_block(prior_texts: list[str] | None) -> str:
    """把已生成题文本拼成 prompt 块, 让 LLM 知道"别再出这些"。
    返回空串当没有 prior. 截顶 120 字防 prompt 爆 + 只拿非空。"""
    if not prior_texts:
        return ""
    bullets: list[str] = []
    for t in prior_texts:
        snippet = (t or "").strip()
        if not snippet:
            continue
        bullets.append(f"- {snippet[:120]}")
    if not bullets:
        return ""
    return (
        "本维度已生成的题目 (请生成一道考察角度/子方向不同的新题, 不要复述):\n"
        + "\n".join(bullets) + "\n\n"
    )


def _knowledge_question(
    job: JobContext, comp: Competency, fallback: str,
    *, used_source_ids: set[str] | None = None,
    prior_texts: list[str] | None = None,
    topic: str | None = None, difficulty: str | None = None,
    resume_skills: list[str] | None = None,
) -> tuple[str, str | None, str]:
    """生成一道 knowledge 题。
    返回 (题目文本, source_question_id 或 None, path)。
    path ∈ rag_refined / rag_direct / llm_generated / fallback_template /
    llm_direct_knowledge, 进 PlanTrace 作审计 (Sprint E/H)。

    Sprint H: job.question_source=="llm_direct" 时走纯 LLM 出题分支 (跳过题库
    召回, rubric+few-shot + 简历技能定向), 与下面的 RAG 路径完全隔离。

    used_source_ids: 本 plan 已用过的 seed, 召回时排除, 防同一道种子题被
    LLM 精修成多种说法反复出现 (prior_texts 挡不住这种"同源变体")。
    prior_texts 让 LLM prompt 告诉模型"避开这些已出过的题", 防 LLM cache 让
    同 (job, comp) 多次调用全返同一题 (实战 bug: 11 道 knowledge 文本全相同).
    Sprint B+D: topic / difficulty 透传到 Milvus 硬过滤; matched topic + 难度
    召回不到 → 自然退到 source_id=None 走 LLM 现场生成 (走老 fallback 路径).

    路径优先级:
    1. rag_refined: RAG 召回 (排除已用 seed, 可选 topic+difficulty 过滤) + LLM 精修
    2. rag_direct: 题库有题, LLM stub 时, 用候选题原文
    3. llm_generated: 题库无未用过的题, 纯 LLM 生成 (无 source_question_id)
    4. fallback_template: LLM 也 stub (无 source_question_id)
    """
    jd_excerpt = job.jd[:400]

    # Sprint H: 纯 LLM 出题分支 —— 跳过题库召回, 直接按维度+简历技能出题。
    if job.question_source == "llm_direct":
        return _llm_direct_question(
            _KNOWLEDGE_LLM_SYSTEM, job, comp, fallback,
            prior_texts=prior_texts, topic=topic,
            resume_skills=resume_skills, path="llm_direct_knowledge",
        )

    # Sprint E: 硬过滤只作用于无 topic 的大池 (topic 命中的小池距离跨 topic
    # 不可比, 加阈值会误杀正确匹配)。topic is None → 传阈值, 超阈值退 LLM 生成。
    hit = _retrieve_seed_question(
        job.role_family, comp, jd_excerpt,
        category=QuestionCategory.KNOWLEDGE,
        exclude_ids=used_source_ids or frozenset(),
        topic=topic, difficulty=difficulty,
        max_distance=_bigpool_max_distance() if topic is None else None,
    )
    prior_block = _format_prior_block(prior_texts)

    topic_block = f"主题(题目必须落在此范围内): {topic}\n" if topic else ""

    if hit is not None:
        source_id = hit["question_id"]
        seed_text = hit["text"]
        # 精修需要主题域做消歧: 题库题源自文档切片, 题干术语脱离主题域会歧义
        # (实测: "什么是 Skill 内容保护" 不给主题域会被精修成更模糊的"技能保护",
        # 给了 "Agent Skill" 才能补出正确背景)。优先 seed 自带 topic (chunk 级,
        # 最准), 无则用 slot 分配的 topic; 都没有就不加该行。
        seed_topic = hit.get("topic") or topic
        topic_line = (
            f"题目主题域(仅用于理解题干术语所指): {seed_topic}\n" if seed_topic else ""
        )
        prompt = (
            f"候选题目(题库召回): {seed_text}\n"
            f"{topic_line}"
            f"职位: {job.title}\n"
            f"考察维度: {comp.name} - {comp.description}\n"
            f"{prior_block}"
            "请按上述规则润色候选题目的措辞, 保持其考察的知识对象不变。"
        )
        adapted = llm.complete(_KNOWLEDGE_RAG_SYSTEM, prompt, max_tokens=240)
        if adapted and not llm.is_stub(adapted):
            return adapted, source_id, "rag_refined"
        return seed_text, source_id, "rag_direct"

    prompt = (
        f"职位: {job.title}\n"
        f"JD: {jd_excerpt}\n"
        f"{topic_block}"
        f"考察维度: {comp.name} - {comp.description}\n"
        f"{prior_block}"
        "请生成一道用于该维度的【基础知识】开放式面试题。"
    )
    text = llm.complete(_KNOWLEDGE_SYSTEM, prompt, max_tokens=200)
    if not text or llm.is_stub(text):
        return fallback, None, "fallback_template"
    return text, None, "llm_generated"


# ---------- Sprint H: 纯 LLM 出题共享分支 (knowledge / scenario 复用) ----------

def _llm_direct_question(
    system: str, job: JobContext, comp: Competency, fallback: str,
    *, prior_texts: list[str] | None, topic: str | None,
    resume_skills: list[str] | None, path: str,
) -> tuple[str, str | None, str]:
    """纯 LLM 出题 (question_source=llm_direct)。跳过题库召回, 按考察维度 +
    简历技能直接出题。source_question_id 恒 None (无题库溯源)。
    LLM 失败 → 轮换 fallback 模板 (与 RAG 路径同一套池化 fallback)。"""
    skills = resume_skills or []
    skills_block = (
        "候选人简历技能 (用于确定出题方向, 优先考这些): "
        + "、".join(skills[:20]) + "\n"
        if skills else ""
    )
    topic_block = f"主题(题目必须落在此范围内): {topic}\n" if topic else ""
    prompt = (
        f"职位: {job.title}\n"
        f"JD: {job.jd[:400]}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        f"{skills_block}"
        f"{topic_block}"
        f"{_format_prior_block(prior_texts)}"
        "请按上述好题标准, 直接出一道面试题。"
    )
    # Sprint H: 按 track 把校招/社招基调拼进 system (缓存 key 也随 track 区分)
    system_with_tone = system + _track_tone(job.track)
    text = llm.complete(system_with_tone, prompt, max_tokens=260)
    if not text or llm.is_stub(text):
        return fallback, None, "fallback_template"
    return text, None, path


# ---------- scenario: 召回 + 精修 (Sprint 5.5 新加) ----------

def _scenario_question(
    job: JobContext, comp: Competency, fallback: str,
    *, used_source_ids: set[str] | None = None,
    prior_texts: list[str] | None = None,
    resume_skills: list[str] | None = None,
) -> tuple[str, str | None, str]:
    """生成一道 scenario 题。返回 (文本, source_id, path), 与 _knowledge_question
    同形, 只是题源走 category=scenario 召回 + 场景题专用 LLM prompt。
    used_source_ids + prior_texts 同 knowledge.
    Sprint H: question_source=="llm_direct" 时走纯 LLM 出题分支。
    """
    jd_excerpt = job.jd[:400]

    if job.question_source == "llm_direct":
        return _llm_direct_question(
            _SCENARIO_LLM_SYSTEM, job, comp, fallback,
            prior_texts=prior_texts, topic=None,
            resume_skills=resume_skills, path="llm_direct_scenario",
        )

    hit = _retrieve_seed_question(
        job.role_family, comp, jd_excerpt,
        category=QuestionCategory.SCENARIO,
        exclude_ids=used_source_ids or frozenset(),
    )
    prior_block = _format_prior_block(prior_texts)

    if hit is not None:
        source_id = hit["question_id"]
        seed_text = hit["text"]
        # 同 knowledge: seed 自带 topic 作主题域消歧 (scenario slot 无 topic
        # 分配, 只有 seed 侧来源)。
        seed_topic = hit.get("topic")
        topic_line = (
            f"题目主题域(仅用于理解题干术语所指): {seed_topic}\n" if seed_topic else ""
        )
        prompt = (
            f"候选题目(场景题库召回): {seed_text}\n"
            f"{topic_line}"
            f"职位: {job.title}\n"
            f"考察维度: {comp.name} - {comp.description}\n"
            f"{prior_block}"
            "请按上述规则润色情境措辞, 保持原题考察的核心问题与场景题结构不变。"
        )
        adapted = llm.complete(_SCENARIO_RAG_SYSTEM, prompt, max_tokens=240)
        if adapted and not llm.is_stub(adapted):
            return adapted, source_id, "rag_refined"
        return seed_text, source_id, "rag_direct"

    prompt = (
        f"职位: {job.title}\n"
        f"JD: {jd_excerpt}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        f"{prior_block}"
        "请生成一道用于该维度的【场景题】(给具体情境, 让候选人现场决策, 不要回顾经历)。"
    )
    text = llm.complete(_SCENARIO_SYSTEM, prompt, max_tokens=240)
    if not text or llm.is_stub(text):
        return fallback, None, "fallback_template"
    return text, None, "llm_generated"


# ---------- project: lazy 占位 + resolve_lazy 回灌 ----------

def _retrieve_resume_chunks(
    candidate_id: str, competency: Competency,
) -> list[dict]:
    """从 Milvus 召回候选人 Resume 中与本维度相关的切片。"""
    query_text = f"考察维度: {competency.name} - {competency.description}"
    vec = embeddings.embed(query_text)
    if embeddings.is_stub_vector(vec):
        return []
    try:
        return vector_store.search_documents(
            embedding=vec,
            top_k=_RAG_TOP_K,
            kind=vector_store.DOC_KIND_RESUME,
            source_id=candidate_id,
        )
    except vector_store.MilvusNotConfigured:
        log.info("Milvus 未配置, project 题走 fallback 路径")
        return []
    except Exception:
        log.exception("project Resume 召回失败, 走 fallback")
        return []


def _project_question(
    job: JobContext,
    candidate: CandidateProfile,
    comp: Competency,
    fallback: str,
    *,
    intro_text: str = "",
    prior_texts: list[str] | None = None,
) -> tuple[str, list[str], str]:
    """生成一道项目深挖题。返回 (文本, chunk_ids, path)。
    path ∈ resume_rag / resume_llm / fallback_template (Sprint E trace 用)。
    Sprint 5.5: intro_text 是 task 4 才真正传入的候选人自我介绍全文,
    task 3 阶段默认空串 ——  prompt 加 intro_text 段落给 LLM 看, 但不强依赖。
    Sprint 5.9 patch: prior_texts 同 knowledge/scenario, 让 lazy resolve 5 道
    project 题时 LLM 不要返同一道."""
    chunks = _retrieve_resume_chunks(candidate.candidate_id, comp)

    intro_block = (
        f"候选人自我介绍:\n{intro_text}\n"
        if intro_text.strip()
        else ""
    )
    prior_block = _format_prior_block(prior_texts)

    if chunks:
        chunk_ids = [c["document_id"] for c in chunks]
        chunks_text = "\n---\n".join(c["text"] for c in chunks)
        prompt = (
            f"职位: {job.title}\n"
            f"JD: {job.jd[:300]}\n"
            f"考察维度: {comp.name} - {comp.description}\n"
            f"{intro_block}"
            f"候选人 Resume 相关片段:\n{chunks_text}\n"
            f"{prior_block}"
            "请围绕这些具体内容生成一道项目深挖题。"
        )
        text = llm.complete(_PROJECT_RAG_SYSTEM, prompt, max_tokens=260)
        if text and not llm.is_stub(text):
            return text, chunk_ids, "resume_rag"
        return fallback, chunk_ids, "fallback_template"

    projects_hint = (
        "\n".join(f"- {p}" for p in candidate.projects)
        if candidate.projects else "(未结构化, 直接读 resume 原文)"
    )
    prompt = (
        f"职位: {job.title}\n"
        f"JD: {job.jd[:300]}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        f"{intro_block}"
        f"候选人简历摘要:\n{candidate.resume[:800]}\n"
        f"候选人已识别项目要点:\n{projects_hint}\n"
        f"{prior_block}"
        "请围绕该考察维度, 生成一道针对其具体项目/实习经历的深挖题。"
    )
    text = llm.complete(_PROJECT_SYSTEM, prompt, max_tokens=220)
    if not text or llm.is_stub(text):
        return fallback, [], "fallback_template"
    return text, [], "resume_llm"


# ---------- project: Sprint F 简历语义分段定向深挖 ----------

def _rank_sections_for_job(
    sections: list[ResumeSection], job: JobContext,
) -> list[ResumeSection]:
    """按与 JD 的语义相关度对 deep-dive 段排序 (段多于题数时优先问最相关的)。
    embedding stub / 失败 → 保持文档顺序 (简历顺序本身就是候选人的自我排序)。
    向量只在这里做一次性排序, 不做逐题检索 —— 段分配是确定性轮询, 可复现。"""
    if len(sections) <= 1:
        return list(sections)
    query = f"{job.title}\n{job.jd[:400]}"
    qv = embeddings.embed(query)
    if embeddings.is_stub_vector(qv):
        return list(sections)

    def _cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    scored: list[tuple[float, int, ResumeSection]] = []
    for i, sec in enumerate(sections):
        sv = embeddings.embed(sec.text[:600])
        if embeddings.is_stub_vector(sv):
            return list(sections)
        scored.append((_cos(qv, sv), i, sec))
    # 相关度降序; 同分保持文档顺序 (i 升序) 保证确定性
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [sec for _, _, sec in scored]


def _project_question_for_section(
    job: JobContext,
    comp: Competency,
    section: ResumeSection,
    fallback: str,
    *,
    intro_text: str = "",
    prior_texts: list[str] | None = None,
) -> tuple[str, str]:
    """针对单个简历段定向生成一道深挖题。返回 (文本, path)。
    path ∈ resume_section / fallback_template。
    与 _project_question (混合切片召回) 的区别: 材料只有这一段经历原文,
    保证"一个项目一道题"; LLM 失败仍走轮换 fallback 模板。"""
    intro_block = (
        f"候选人自我介绍:\n{intro_text}\n" if intro_text.strip() else ""
    )
    prior_block = _format_prior_block(prior_texts)
    label = {"project": "项目", "internship": "实习", "work": "工作"}.get(
        section.type, "经历",
    )
    prompt = (
        f"职位: {job.title}\n"
        f"JD: {job.jd[:300]}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        f"{intro_block}"
        f"该段{label}经历 ({section.title}):\n{section.text[:1500]}\n"
        f"{prior_block}"
        "请只针对这段经历生成一道项目深挖题。"
    )
    text = llm.complete(_PROJECT_SECTION_SYSTEM, prompt, max_tokens=260)
    if not text or llm.is_stub(text):
        return fallback, "fallback_template"
    return text, "resume_section"


# fallback 模板池 —— LLM 完全不可用时按 used 轮换, 防止同 competency 多道题
# 全部落到同一句话 (实战: 6/23 冒烟面试 6 道 project 题全是同一句 fallback,
# 候选人会连续听到 6 遍一模一样的题)。池子耗尽后循环, 重复概率随池深下降。
_PROJECT_FALLBACK_TECH = (
    "请挑你简历里最有挑战的一段技术工作, 讲清楚你的角色、"
    "做的关键决策, 以及最终的结果与复盘。",
    "请讲一个你在项目里做过的重要技术选型: 当时有哪些备选方案, "
    "你怎么对比评估, 最后为什么这么选?",
    "请回忆一次你在项目中定位并解决疑难问题的经历: 现象是什么, "
    "你怎么一步步排查, 根因和最终修复是什么?",
    "请讲一段你为项目做过的性能或稳定性优化: 优化前的瓶颈在哪, "
    "你的改法是什么, 效果如何量化?",
    "请挑一个你参与的项目, 讲讲它的整体架构: 核心模块怎么划分, "
    "数据怎么流转, 哪个设计你现在回头看会改?",
    "请讲一次你在项目中接手/重构他人代码的经历: 原有实现的问题, "
    "你的改造思路, 以及如何保证不引入回归。",
)
_PROJECT_FALLBACK_COMM = (
    "请挑你简历里一次跨职能协作的经历, 讲清楚冲突点、"
    "你如何推动对齐, 以及最终是否落地。",
    "请讲一次你需要说服同事或上级接受你技术方案的经历: "
    "分歧在哪, 你怎么沟通, 结果如何?",
    "请回忆一次项目延期或需求临时变更的场景: 你怎么和相关方同步、"
    "重排计划, 最终怎么收场?",
    "请讲一次你在团队里推动规范或流程落地的经历: 阻力来自哪里, "
    "你做了什么让大家买账?",
)


def _project_fallback(comp: Competency, *, used: int = 0) -> str:
    """Project stage 最末 fallback —— 用维度别名挑模板池, 按 used 轮换。
    used = 本 round 该 competency 已出题数 (调用方传 len(priors))。"""
    pool = (
        _PROJECT_FALLBACK_TECH
        if "技术" in comp.name or "深度" in comp.name
        else _PROJECT_FALLBACK_COMM
    )
    return pool[used % len(pool)]


# ---------- 主入口: plan + resolve_lazy ----------

# Sprint 5.9: competency_id 改用稳定字符串而非随机 uuid。原因: HR 在新建 job
# 时配 ProfileAspect 必须知道这两个维度的 id, 否则只能等 Planner 跑完才有 id。
# 稳定字符串让 HR UI 创建 job 时就能挂 aspects, Planner / Assessor / coverage
# 一致用这俩 id 不会漂移。改前任何依赖随机 uuid 的代码会挂, 由 eval 锁住。
COMPETENCY_TECH_ID = "comp:tech"
COMPETENCY_COMM_ID = "comp:comm"


def _build_competencies() -> tuple[Competency, Competency]:
    """plan.competencies 顶层用的两个维度。
    weight 反映期望 overall 加权: 技术深度 > 沟通协作。
    Sprint 5.9: competency_id 用稳定字符串, 见上方常量说明。"""
    tech = Competency(
        competency_id=COMPETENCY_TECH_ID,
        name="技术深度",
        description="对岗位核心技术栈的理解深度与实践经验",
        weight=2.0,
    )
    comm = Competency(
        competency_id=COMPETENCY_COMM_ID,
        name="沟通协作",
        description="表达清晰度、跨职能协作经验、推动事情落地的能力",
        weight=1.0,
    )
    return tech, comm


def plan(job: JobContext, candidate: CandidateProfile) -> InterviewPlan:
    """Planner 入口: 按 job.track 出 stage 序列。
    knowledge / scenario 题 plan 阶段就生成;
    project 题在 plan 阶段只放 lazy 占位 (text=""), 由 resolve_lazy_questions
    在进入 project stage 时回灌。

    多题防重复的两层机制:
    - used_source_ids (plan 全局): RAG 召回排除已用过的 seed, 防同一道种子题
      被 LLM 精修成多种说法反复出现 (取代 Sprint 5.9 的 rank 轮转 —— 题库
      seed 少时轮转会循环命中同一批)。seed 耗尽退到纯 LLM 生成。
    - prior_texts (同 (stage, category, competency) 内): 已生成题文本喂进
      prompt, 防 LLM cache 让 11 道 knowledge 全用同一段文字. key 用 (stage,
      category, competency_id) 而不是只用 competency: 同一 competency 在不同
      stage (如 knowledge / scenario) 出题角度本来就不同, 不该互相约束。

    Sprint B+D: KNOWLEDGE stage 入口先算 matched_topics
    (HR aspect[comp:tech] + LLM 抽出的 resume skill → topic 语义匹配),
    每 tech knowledge slot 按 (matched topic × 难度) round-robin 分配 (topic,
    difficulty) 透传 Milvus 硬过滤; 不够补走原 RAG (topic=None). 未匹配的 skill
    落 skill_backlog 让 HR 扩库决策, 不"现场 LLM 生成新题"破坏公平性。"""
    tech, comm = _build_competencies()
    comp_by_key = {"tech": tech, "comm": comm}

    rounds: list[InterviewRound] = []
    # plan 全局: 已用过的 seed question_id, 召回时排除 (跨 stage 共享 ——
    # 同一道 seed 不该在 knowledge 和 scenario 各出一遍)
    used_source_ids: set[str] = set()
    # Sprint E: 出题全过程 trace, 与 plan 一起落库供 HR 端审计
    trace = PlanTrace()
    # Sprint H: llm_direct 模式一次性抽简历技能, 透传给 knowledge/scenario 出题;
    # rag 模式不需要 (topic 匹配内部另抽), 空列表即可, 不多花一次 LLM 调用。
    llm_direct_skills: list[str] = []
    if job.question_source == "llm_direct" and candidate.resume:
        try:
            llm_direct_skills = skill_extraction.extract_skills(candidate.resume)
        except Exception:
            log.exception("llm_direct skill_extraction 失败, 空技能继续")
    if job.question_source == "llm_direct":
        trace.extracted_skills = llm_direct_skills
    for idx, (stage, slots) in enumerate(_stages_for(job.track, job.role_family)):
        questions: list[Question] = []
        round_comps: list[Competency] = []
        # (category, competency_id) -> prior_texts_list
        bucket: dict[tuple[QuestionCategory, str | None], list[str]] = {}

        # Sprint B+D: KNOWLEDGE stage 入口预算 topic 分配 (tech slot 才用)。
        knowledge_assignments: list[tuple[str | None, str | None]] = []
        knowledge_tech_slot_idx = 0
        if stage is InterviewStage.KNOWLEDGE:
            tech_slot_count = sum(
                1 for s in slots if s.competency_key == "tech"
            )
            if tech_slot_count > 0:
                matched_topics, unmatched_skills, match_detail = (
                    _compute_knowledge_topic_matching(job, candidate, tech)
                )
                knowledge_assignments = _plan_knowledge_assignments(
                    matched_topics, tech_slot_count,
                )
                trace.aspect_queries = match_detail["aspect_queries"]
                trace.extracted_skills = match_detail["extracted_skills"]
                trace.matches = match_detail["matches"]
                trace.matched_topics = matched_topics
                trace.unmatched_skills = unmatched_skills
                trace.llm_matched_skills = match_detail["llm_matched_skills"]
                if unmatched_skills:
                    try:
                        db.record_skill_backlog(
                            unmatched_skills,
                            job_id=job.job_id,
                            candidate_id=candidate.candidate_id,
                        )
                    except Exception:
                        log.exception("record_skill_backlog 失败, 不阻塞 plan")

        for slot in slots:
            comp = comp_by_key.get(slot.competency_key) if slot.competency_key else None
            key = (slot.category, comp.competency_id if comp else None)
            priors = bucket.setdefault(key, [])

            # 取本 slot 的 topic/difficulty 分配 (仅 KNOWLEDGE+tech slot 非 None)
            slot_topic: str | None = None
            slot_difficulty: str | None = None
            if (
                stage is InterviewStage.KNOWLEDGE
                and slot.competency_key == "tech"
                and knowledge_tech_slot_idx < len(knowledge_assignments)
            ):
                slot_topic, slot_difficulty = knowledge_assignments[knowledge_tech_slot_idx]
                knowledge_tech_slot_idx += 1

            q, path = _build_question_for_slot(
                job, candidate, slot, comp,
                used_source_ids=used_source_ids, prior_texts=list(priors),
                topic=slot_topic, difficulty=slot_difficulty,
                resume_skills=llm_direct_skills,
            )
            questions.append(q)
            trace.questions.append(QuestionTrace(
                question_id=q.question_id,
                stage=stage,
                category=q.category,
                path=path,
                topic=slot_topic,
                difficulty=slot_difficulty,
                source_question_id=q.source_question_id,
            ))
            # 把生成的非空文本回灌进 bucket, 下一题就能看到 (project 题 text="",
            # 不喂入避免空白噪声; project 题去重交给 resolve_lazy 回灌时处理);
            # 命中的 seed 记入全局排除集, 后续槽位不再召回同一道
            if q.text:
                priors.append(q.text)
            if q.source_question_id:
                used_source_ids.add(q.source_question_id)
            if comp is not None and comp not in round_comps:
                round_comps.append(comp)
        rounds.append(InterviewRound(
            index=idx,
            title=_STAGE_TITLES[stage],
            stage=stage,
            competencies=round_comps,
            questions=questions,
        ))

    return InterviewPlan(
        job_id=job.job_id,
        rounds=rounds,
        competencies=[tech, comm],
        trace=trace,
    )


def _compute_knowledge_topic_matching(
    job: JobContext, candidate: CandidateProfile, tech_comp: Competency,
) -> tuple[list[str], list[str], dict]:
    """B+D 合并匹配: HR aspect(comp:tech) + LLM 抽 resume skill → matched topics.
    返回 (matched_topics_union, unmatched_skills, detail)。
    detail 是 Sprint E 可观测性明细: {aspect_queries, extracted_skills, matches},
    plan() 把它落进 PlanTrace 供 HR 端展示匹配全过程。
    HR 没配 aspect (job.aspects 为空) → 用 competency 自身当 query 兜底."""
    queries: list[str] = []
    tech_aspects = [
        a for a in (job.aspects or [])
        if a.competency_id == tech_comp.competency_id
    ]
    if tech_aspects:
        for a in tech_aspects:
            desc = (a.description or "").strip()
            queries.append(f"{a.name} - {desc}" if desc else a.name)
    else:
        queries.append(f"{tech_comp.name} - {tech_comp.description}")

    skills: list[str] = []
    if candidate.resume:
        try:
            skills = skill_extraction.extract_skills(candidate.resume)
        except Exception:
            log.exception("skill_extraction 失败, 走纯 aspect 匹配")
            skills = []

    all_queries = queries + skills
    matches = topic_match.match_topics_for_queries(all_queries)

    # Sprint E: LLM 兜底归类 —— 短技能词 embedding 距离够不着 topic 标签
    # ("LangChain 是 Agent 框架"是世界知识, 不在向量距离里), embedding 剩下
    # 的 unmatched skill 再走一次 LLM 归类; LLM 失败返 {} = 维持现状。
    emb_unmatched = [s for s in skills if not matches.get(s)]
    llm_matched: dict[str, list[str]] = {}
    if emb_unmatched:
        llm_matched = topic_match.llm_match_skills(emb_unmatched)
        matches.update(llm_matched)

    matched_union = topic_match.union_matched_topics(matches)
    unmatched_skills = [s for s in skills if not matches.get(s)]

    log.info(
        "knowledge topic match: aspect=%d skill=%d llm_rescued=%d → "
        "matched_topics=%s unmatched_skills=%s",
        len(queries), len(skills), len(llm_matched),
        matched_union, unmatched_skills,
    )
    detail = {
        "aspect_queries": queries,
        "extracted_skills": skills,
        "matches": matches,
        "llm_matched_skills": sorted(llm_matched),
    }
    return matched_union, unmatched_skills, detail


_DIFFICULTY_RR = ("easy", "medium", "hard")


def _plan_knowledge_assignments(
    matched_topics: list[str], slot_count: int,
) -> list[tuple[str | None, str | None]]:
    """slot_count 个 tech knowledge slot 的 (topic, difficulty) 分配。

    策略: 外层难度循环 (easy → medium → hard), 内层 matched_topics 循环。
    这样保证 (a) 每个 topic 优先各拿一道 easy 覆盖广度; (b) hard 在尾部, 超额
    砍掉时先牺牲 hard, 保留入门题。不够 slot_count 时补 (None, None) 走 fallback
    (原 RAG 路径无 topic 限制)。"""
    out: list[tuple[str | None, str | None]] = []
    if matched_topics:
        for diff in _DIFFICULTY_RR:
            for topic in matched_topics:
                if len(out) >= slot_count:
                    return out
                out.append((topic, diff))
    while len(out) < slot_count:
        out.append((None, None))
    return out


def _build_question_for_slot(
    job: JobContext,
    candidate: CandidateProfile,
    slot: _StageSlot,
    comp: Competency | None,
    *,
    used_source_ids: set[str] | None = None,
    prior_texts: list[str] | None = None,
    topic: str | None = None,
    difficulty: str | None = None,
    resume_skills: list[str] | None = None,
) -> tuple[Question, str]:
    """按槽位生成一道题。project 题永远占位, knowledge/scenario 直接生成,
    self_intro 用固定文本。返回 (Question, path) —— path 进 PlanTrace (Sprint E)。
    used_source_ids 让 RAG 召回排除已用 seed (防同源变体), prior_texts 让 LLM
    不复读已生成的题 (实战 bug: tech+campus 11 道 knowledge 全相同).
    Sprint B+D: topic / difficulty 仅 knowledge tech slot 非 None, 透传 Milvus
    硬过滤。其他 slot 类型 (self_intro / scenario / project / comm) 不受影响。"""
    cat = slot.category

    if cat is QuestionCategory.SELF_INTRO:
        return Question(
            competency_id=None,
            text=_SELF_INTRO_TEXT,
            type=QuestionType.OPEN,
            category=cat,
        ), "self_intro"

    if cat is QuestionCategory.KNOWLEDGE:
        assert comp is not None, "knowledge 题必须挂 competency"
        text, source_id, path = _knowledge_question(
            job, comp,
            fallback=_knowledge_fallback(comp, used=len(prior_texts or [])),
            used_source_ids=used_source_ids, prior_texts=prior_texts,
            topic=topic, difficulty=difficulty,
            resume_skills=resume_skills,
        )
        return Question(
            competency_id=comp.competency_id,
            text=text,
            type=_question_type_for(comp),
            category=cat,
            source_question_id=source_id,
        ), path

    if cat is QuestionCategory.SCENARIO:
        assert comp is not None, "scenario 题必须挂 competency"
        text, source_id, path = _scenario_question(
            job, comp,
            fallback=_scenario_fallback(comp, used=len(prior_texts or [])),
            used_source_ids=used_source_ids, prior_texts=prior_texts,
            resume_skills=resume_skills,
        )
        return Question(
            competency_id=comp.competency_id,
            text=text,
            type=QuestionType.SITUATIONAL,
            category=cat,
            source_question_id=source_id,
        ), path

    if cat is QuestionCategory.PROJECT_EXPERIENCE:
        assert comp is not None, "project 题必须挂 competency"
        # lazy 占位: text 空, 进 stage 时 resolve_lazy_questions 回灌
        return Question(
            competency_id=comp.competency_id,
            text="",
            type=_question_type_for(comp),
            category=cat,
            lazy=True,
        ), "lazy_pending"

    raise AssertionError(f"未知 category: {cat}")


def _question_type_for(comp: Competency) -> QuestionType:
    return (
        QuestionType.TECHNICAL
        if "技术" in comp.name or "深度" in comp.name
        else QuestionType.BEHAVIORAL
    )


# knowledge / scenario fallback 同样池化轮换 (同 _PROJECT_FALLBACK_* 的动机):
# campus track 单 stage 12 道 knowledge, LLM 不可用时单模板会重复 12 遍。
_KNOWLEDGE_FALLBACK_TECH = (
    "在你做过的系统里, 你认为最关键的技术权衡是什么? "
    "举一个你做过的取舍来说明。",
    "挑一个你最熟悉的技术组件 (框架/中间件/协议), 讲讲它解决什么问题, "
    "底层大致怎么实现?",
    "你怎么理解缓存? 什么场景该用, 用了之后会引入哪些新问题?",
    "讲讲你对并发的理解: 进程、线程、协程的区别, 以及各自适用的场景。",
    "一个 HTTP 请求从浏览器发出到返回, 中间大致经历了哪些环节?",
    "数据库索引为什么能加速查询? 什么情况下索引会失效?",
)
_KNOWLEDGE_FALLBACK_COMM = (
    "当你和非技术同事(产品/业务/SRE)就方案产生分歧时, 你通常如何推进?",
    "你怎么向完全不懂技术的人解释你正在做的事情? 举个你真实讲过的例子。",
    "接手一个陌生模块时, 你会按什么顺序熟悉它? 先看什么后看什么?",
)


def _knowledge_fallback(comp: Competency, *, used: int = 0) -> str:
    pool = (
        _KNOWLEDGE_FALLBACK_TECH
        if "技术" in comp.name or "深度" in comp.name
        else _KNOWLEDGE_FALLBACK_COMM
    )
    return pool[used % len(pool)]


_SCENARIO_FALLBACK_TECH = (
    "你是核心服务的 oncall, 凌晨 3 点收到 P99 告警从 200ms 涨到 4s, "
    "业务量没显著变化。你的前 10 分钟做什么? 为什么按这个顺序?",
    "一次发布后错误率缓慢爬升, 回滚后仍未恢复。你怎么判断问题在代码、"
    "配置还是数据? 按什么顺序验证?",
    "你负责的服务依赖的第三方接口突然大面积超时, 重试把自己也拖垮了。"
    "现场你会先做什么止血, 事后怎么改造?",
)
_SCENARIO_FALLBACK_COMM = (
    "线上 incident 进行中, SRE / 业务 PM / 运营三方都在群里追问 ETA, "
    "你刚定位到根因还没修。接下来 15 分钟你怎么沟通? 给谁什么信息?",
    "需求评审上, 产品坚持一个你认为技术上不可行的方案, 会上只有你反对。"
    "你当场怎么说, 会后怎么跟进?",
    "你发现同事的方案有隐患但对方已经开发过半, 上线时间很紧。"
    "你会怎么提出来, 怎么和 TA 一起决定改不改?",
)


def _scenario_fallback(comp: Competency, *, used: int = 0) -> str:
    pool = (
        _SCENARIO_FALLBACK_TECH
        if "技术" in comp.name or "深度" in comp.name
        else _SCENARIO_FALLBACK_COMM
    )
    return pool[used % len(pool)]


def resolve_lazy_questions(
    plan: InterviewPlan,
    job: JobContext,
    candidate: CandidateProfile,
    *,
    intro_text: str = "",
) -> InterviewPlan:
    """回灌 plan 里所有 lazy 且未生成 (text=="") 的 project 题。
    Sprint 5.5 task 3: 简单沿用 _project_question 现有 RAG 路径;
    Sprint 5.5 task 4: intro_text 由 Orchestrator 在 project stage 入口传入,
    让生成的题真正反映候选人自我介绍内容。

    Sprint F: candidate.sections 有 project/internship/work 段时改走
    **按段轮询定向深挖** —— 第 i 道 lazy 题绑定第 (i % 段数) 个段, 段按与
    JD 的相关度排序 (段多于题数时只问最相关的前几个)。prompt 只喂该段原文,
    真正做到"一个项目一道题"。没有 sections (老数据 / 分段失败) 时保持
    原 RAG 召回路径不变。

    lazy 字段不被回写: 生成后 lazy 仍 True 作 HR 审计 (这题是 lazy 来的),
    判"已生成"用 text != ""。
    返回新的 InterviewPlan (model immutable, 通过重建)。"""
    comp_by_id = {c.competency_id: c for c in plan.competencies}
    new_rounds: list[InterviewRound] = []
    touched = 0
    # question_id -> (path, chunk_ids, section_title), 回灌后同步 trace (Sprint E/F)
    resolved_paths: dict[str, tuple[str, list[str], str | None]] = {}

    # Sprint F: deep-dive 段 + JD 相关度排序, 整个 resolve 过程用同一份顺序
    deepdive = [
        s for s in (candidate.sections or [])
        if s.type in RESUME_DEEPDIVE_TYPES and s.text.strip()
    ]
    ranked_sections = _rank_sections_for_job(deepdive, job) if deepdive else []
    section_cursor = 0

    # Sprint 5.9 patch: 同一 round 内 (project comp) 多个 lazy 题去重 ——
    # 同 plan() 里一样, 拿 prior_texts 喂 LLM "不要重复".
    for r in plan.rounds:
        new_qs: list[Question] = []
        # competency_id -> list of already-resolved texts in this round
        prior_by_comp: dict[str, list[str]] = {}
        for q in r.questions:
            if not q.lazy or q.text:
                new_qs.append(q)
                # 已有 text 的 lazy 题也算"出过", 后续题应避开
                if q.text and q.competency_id:
                    prior_by_comp.setdefault(q.competency_id, []).append(q.text)
                continue
            if q.category is not QuestionCategory.PROJECT_EXPERIENCE:
                # task 3 只 resolve project 题; 别类 lazy 留给未来扩展
                new_qs.append(q)
                continue
            comp = comp_by_id.get(q.competency_id) if q.competency_id else None
            if comp is None:
                log.warning(
                    "lazy project 题 %s competency_id 找不到, 跳过", q.question_id,
                )
                new_qs.append(q)
                continue
            priors = prior_by_comp.setdefault(comp.competency_id, [])
            section_title: str | None = None
            if ranked_sections:
                # Sprint F: 按段轮询定向深挖 (一段一题)
                section = ranked_sections[section_cursor % len(ranked_sections)]
                section_cursor += 1
                text, path = _project_question_for_section(
                    job, comp, section,
                    fallback=_project_fallback(comp, used=len(priors)),
                    intro_text=intro_text,
                    prior_texts=list(priors),
                )
                chunk_ids = []
                if path == "resume_section":
                    section_title = section.title
            else:
                text, chunk_ids, path = _project_question(
                    job, candidate, comp,
                    fallback=_project_fallback(comp, used=len(priors)),
                    intro_text=intro_text,
                    prior_texts=list(priors),
                )
            if text:
                priors.append(text)
            new_qs.append(q.model_copy(update={
                "text": text,
                "source_chunk_ids": chunk_ids,
                # lazy 故意不动 —— 静态信号, 保留作审计
            }))
            resolved_paths[q.question_id] = (path, chunk_ids, section_title)
            touched += 1
        new_rounds.append(r.model_copy(update={"questions": new_qs}))

    # Sprint E: 同步更新 trace 里对应题的 path (lazy_pending → 实际路径) +
    # chunk_ids (+ Sprint F section_title)。老 plan (trace=None) 跳过。
    new_trace = plan.trace
    if new_trace is not None and resolved_paths:
        new_trace = new_trace.model_copy(update={
            "questions": [
                qt.model_copy(update={
                    "path": resolved_paths[qt.question_id][0],
                    "source_chunk_ids": resolved_paths[qt.question_id][1],
                    "section_title": resolved_paths[qt.question_id][2],
                }) if qt.question_id in resolved_paths else qt
                for qt in new_trace.questions
            ],
        })

    log.info("resolve_lazy_questions: 回灌 %d 道 project 题", touched)
    return plan.model_copy(update={"rounds": new_rounds, "trace": new_trace})
