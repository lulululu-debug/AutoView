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
from dataclasses import dataclass

from src import embeddings, llm, vector_store
from src.schemas import (
    CandidateProfile,
    Competency,
    InterviewPlan,
    InterviewRound,
    InterviewStage,
    JobContext,
    ProfileAspect,
    Question,
    QuestionCategory,
    QuestionType,
    Track,
)

log = logging.getLogger(__name__)

_KNOWLEDGE_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "根据给定职位与考察维度, 生成一道开放式、可深挖、贴合岗位的中文【基础知识】面试题。"
    "只输出题目本身, 不要任何解释或前后缀。"
)

_KNOWLEDGE_RAG_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "下面给出一道从题库中召回的候选题, 请基于该题目, 必要时小幅改写让题目更贴合"
    "本次职位的 JD 与考察维度。改写应保留原题的考察意图, 不要彻底换题。"
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
    "下面给出一道从场景题库中召回的候选题, 请基于该题目, 必要时小幅改写让情境更"
    "贴合本次职位与考察维度。改写应保留原题的情境结构 (具体场景 + 问候选人怎么做),"
    "不要把它改回知识题或经历题。"
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

# RAG 召回的候选题数量。当前只取 top-1 给 LLM 精修, 多召回纯为日后做
# diversity / 多轮选题留扩展位; 取 3 是个折中, 也方便日志里看到 runner-up。
_RAG_TOP_K = 3

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
) -> dict | None:
    """从 Milvus 召回 top-1 候选题, 失败 / 空时返 None。
    Sprint 5.5: category 让 knowledge / scenario 各拉各的题源。"""
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
            top_k=_RAG_TOP_K,
            role_family=role_family,
            competency=competency.name,
            category=category.value,
        )
    except vector_store.MilvusNotConfigured:
        log.info("Milvus 未配置, %s 题走 fallback 路径", category.value)
        return None
    except Exception:
        # 网络抖动 / Milvus 报错 / schema drift 都不应让面试卡死, 静默退到现场生成
        log.exception("%s 召回失败, 走 fallback", category.value)
        return None
    if not hits:
        return None
    return hits[0]


def _knowledge_question(
    job: JobContext, comp: Competency, fallback: str,
) -> tuple[str, str | None]:
    """生成一道 knowledge 题。
    返回 (题目文本, source_question_id 或 None)。

    路径优先级:
    1. RAG 召回 + LLM 精修: 题库有题 + embed/Milvus/LLM 都正常
    2. RAG 召回 + 直接复用: 题库有题, LLM stub 时, 用候选题原文 (仍有 source_question_id)
    3. 纯 LLM 生成: 题库无题, LLM 正常 (无 source_question_id)
    4. fallback 模板: LLM 也 stub (无 source_question_id)
    """
    jd_excerpt = job.jd[:400]
    hit = _retrieve_seed_question(
        job.role_family, comp, jd_excerpt, category=QuestionCategory.KNOWLEDGE,
    )

    if hit is not None:
        source_id = hit["question_id"]
        seed_text = hit["text"]
        prompt = (
            f"候选题目(题库召回): {seed_text}\n"
            f"职位: {job.title}\n"
            f"JD 摘要: {jd_excerpt}\n"
            f"考察维度: {comp.name} - {comp.description}\n"
            "请基于候选题目, 必要时小幅改写让题目更聚焦本职位。"
        )
        adapted = llm.complete(_KNOWLEDGE_RAG_SYSTEM, prompt, max_tokens=240)
        if adapted and not llm.is_stub(adapted):
            return adapted, source_id
        return seed_text, source_id

    prompt = (
        f"职位: {job.title}\n"
        f"JD: {jd_excerpt}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        "请生成一道用于该维度的【基础知识】开放式面试题。"
    )
    text = llm.complete(_KNOWLEDGE_SYSTEM, prompt, max_tokens=200)
    if not text or llm.is_stub(text):
        return fallback, None
    return text, None


# ---------- scenario: 召回 + 精修 (Sprint 5.5 新加) ----------

def _scenario_question(
    job: JobContext, comp: Competency, fallback: str,
) -> tuple[str, str | None]:
    """生成一道 scenario 题。
    与 _knowledge_question 同形, 只是题源走 category=scenario 召回 +
    场景题专用 LLM prompt。
    """
    jd_excerpt = job.jd[:400]
    hit = _retrieve_seed_question(
        job.role_family, comp, jd_excerpt, category=QuestionCategory.SCENARIO,
    )

    if hit is not None:
        source_id = hit["question_id"]
        seed_text = hit["text"]
        prompt = (
            f"候选题目(场景题库召回): {seed_text}\n"
            f"职位: {job.title}\n"
            f"JD 摘要: {jd_excerpt}\n"
            f"考察维度: {comp.name} - {comp.description}\n"
            "请基于候选题目, 必要时小幅改写让情境更贴合本职位。保持场景题结构。"
        )
        adapted = llm.complete(_SCENARIO_RAG_SYSTEM, prompt, max_tokens=240)
        if adapted and not llm.is_stub(adapted):
            return adapted, source_id
        return seed_text, source_id

    prompt = (
        f"职位: {job.title}\n"
        f"JD: {jd_excerpt}\n"
        f"考察维度: {comp.name} - {comp.description}\n"
        "请生成一道用于该维度的【场景题】(给具体情境, 让候选人现场决策, 不要回顾经历)。"
    )
    text = llm.complete(_SCENARIO_SYSTEM, prompt, max_tokens=240)
    if not text or llm.is_stub(text):
        return fallback, None
    return text, None


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
) -> tuple[str, list[str]]:
    """生成一道项目深挖题。
    Sprint 5.5: intro_text 是 task 4 才真正传入的候选人自我介绍全文,
    task 3 阶段默认空串 ——  prompt 加 intro_text 段落给 LLM 看, 但不强依赖。"""
    chunks = _retrieve_resume_chunks(candidate.candidate_id, comp)

    intro_block = (
        f"候选人自我介绍:\n{intro_text}\n"
        if intro_text.strip()
        else ""
    )

    if chunks:
        chunk_ids = [c["document_id"] for c in chunks]
        chunks_text = "\n---\n".join(c["text"] for c in chunks)
        prompt = (
            f"职位: {job.title}\n"
            f"JD: {job.jd[:300]}\n"
            f"考察维度: {comp.name} - {comp.description}\n"
            f"{intro_block}"
            f"候选人 Resume 相关片段:\n{chunks_text}\n"
            "请围绕这些具体内容生成一道项目深挖题。"
        )
        text = llm.complete(_PROJECT_RAG_SYSTEM, prompt, max_tokens=260)
        if text and not llm.is_stub(text):
            return text, chunk_ids
        return fallback, chunk_ids

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
        "请围绕该考察维度, 生成一道针对其具体项目/实习经历的深挖题。"
    )
    text = llm.complete(_PROJECT_SYSTEM, prompt, max_tokens=220)
    if not text or llm.is_stub(text):
        return fallback, []
    return text, []


def _project_fallback(comp: Competency) -> str:
    """Project stage 最末 fallback —— 用维度别名挑模板, 避免空文本上线。"""
    if "技术" in comp.name or "深度" in comp.name:
        return (
            "请挑你简历里最有挑战的一段技术工作, 讲清楚你的角色、"
            "做的关键决策, 以及最终的结果与复盘。"
        )
    return (
        "请挑你简历里一次跨职能协作的经历, 讲清楚冲突点、"
        "你如何推动对齐, 以及最终是否落地。"
    )


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
    在进入 project stage 时回灌。"""
    tech, comm = _build_competencies()
    comp_by_key = {"tech": tech, "comm": comm}

    rounds: list[InterviewRound] = []
    for idx, (stage, slots) in enumerate(_stages_for(job.track, job.role_family)):
        questions: list[Question] = []
        round_comps: list[Competency] = []
        for slot in slots:
            comp = comp_by_key.get(slot.competency_key) if slot.competency_key else None
            q = _build_question_for_slot(job, candidate, slot, comp)
            questions.append(q)
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
    )


def _build_question_for_slot(
    job: JobContext,
    candidate: CandidateProfile,
    slot: _StageSlot,
    comp: Competency | None,
) -> Question:
    """按槽位生成一道题。project 题永远占位, knowledge/scenario 直接生成,
    self_intro 用固定文本。"""
    cat = slot.category

    if cat is QuestionCategory.SELF_INTRO:
        return Question(
            competency_id=None,
            text=_SELF_INTRO_TEXT,
            type=QuestionType.OPEN,
            category=cat,
        )

    if cat is QuestionCategory.KNOWLEDGE:
        assert comp is not None, "knowledge 题必须挂 competency"
        text, source_id = _knowledge_question(
            job, comp,
            fallback=_knowledge_fallback(comp),
        )
        return Question(
            competency_id=comp.competency_id,
            text=text,
            type=_question_type_for(comp),
            category=cat,
            source_question_id=source_id,
        )

    if cat is QuestionCategory.SCENARIO:
        assert comp is not None, "scenario 题必须挂 competency"
        text, source_id = _scenario_question(
            job, comp,
            fallback=_scenario_fallback(comp),
        )
        return Question(
            competency_id=comp.competency_id,
            text=text,
            type=QuestionType.SITUATIONAL,
            category=cat,
            source_question_id=source_id,
        )

    if cat is QuestionCategory.PROJECT_EXPERIENCE:
        assert comp is not None, "project 题必须挂 competency"
        # lazy 占位: text 空, 进 stage 时 resolve_lazy_questions 回灌
        return Question(
            competency_id=comp.competency_id,
            text="",
            type=_question_type_for(comp),
            category=cat,
            lazy=True,
        )

    raise AssertionError(f"未知 category: {cat}")


def _question_type_for(comp: Competency) -> QuestionType:
    return (
        QuestionType.TECHNICAL
        if "技术" in comp.name or "深度" in comp.name
        else QuestionType.BEHAVIORAL
    )


def _knowledge_fallback(comp: Competency) -> str:
    if "技术" in comp.name or "深度" in comp.name:
        return (
            "在你做过的系统里, 你认为最关键的技术权衡是什么? "
            "举一个你做过的取舍来说明。"
        )
    return (
        "当你和非技术同事(产品/业务/SRE)就方案产生分歧时, 你通常如何推进?"
    )


def _scenario_fallback(comp: Competency) -> str:
    if "技术" in comp.name or "深度" in comp.name:
        return (
            "你是核心服务的 oncall, 凌晨 3 点收到 P99 告警从 200ms 涨到 4s, "
            "业务量没显著变化。你的前 10 分钟做什么? 为什么按这个顺序?"
        )
    return (
        "线上 incident 进行中, SRE / 业务 PM / 运营三方都在群里追问 ETA, "
        "你刚定位到根因还没修。接下来 15 分钟你怎么沟通? 给谁什么信息?"
    )


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

    lazy 字段不被回写: 生成后 lazy 仍 True 作 HR 审计 (这题是 lazy 来的),
    判"已生成"用 text != ""。
    返回新的 InterviewPlan (model immutable, 通过重建)。"""
    comp_by_id = {c.competency_id: c for c in plan.competencies}
    new_rounds: list[InterviewRound] = []
    touched = 0

    for r in plan.rounds:
        new_qs: list[Question] = []
        for q in r.questions:
            if not q.lazy or q.text:
                new_qs.append(q)
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
            text, chunk_ids = _project_question(
                job, candidate, comp,
                fallback=_project_fallback(comp),
                intro_text=intro_text,
            )
            new_qs.append(q.model_copy(update={
                "text": text,
                "source_chunk_ids": chunk_ids,
                # lazy 故意不动 —— 静态信号, 保留作审计
            }))
            touched += 1
        new_rounds.append(r.model_copy(update={"questions": new_qs}))

    log.info("resolve_lazy_questions: 回灌 %d 道 project 题", touched)
    return plan.model_copy(update={"rounds": new_rounds})
