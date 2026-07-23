"""
核心数据契约 —— 四个 agent 的输入输出全部基于这些类型对齐。

链路: JobContext + CandidateProfile -> Planner -> InterviewPlan
      -> Interviewer 循环(Question / CandidateAnswer / FollowUp)
      -> InterviewSession -> Evaluator -> EvaluationReport

Question.category 区分四类题(Sprint 5.5 起从两类扩到四类):
- KNOWLEDGE          基础知识考察, 由 JobContext 驱动
- PROJECT_EXPERIENCE 项目/实习内容考察, 由 CandidateProfile.resume 驱动
- SELF_INTRO         自我介绍, 永远 0 追问, 答案落 InterviewSession.intro_text
- SCENARIO           场景题, 由场景题库召回 + LLM 精修

JobContext.track 与 InterviewRound.stage 配套 (Sprint 5.5):
- track="campus" 校招: self_intro -> knowledge(重) -> project(lazy gen) -> scenario(轻)
- track="lateral" 社招: self_intro -> knowledge(轻) -> project(重) -> scenario(重)

Signal 为多模态扩展预留, 骨架阶段恒为空。
合规约束(见 ARCHITECTURE.md 第 7 节)在类型层面体现:
EvaluationReport 把 content_scores(内容维度) 与 performance_scores(表现维度) 分开,
软信号只进 performance_scores, 且 overall 不依赖软信号。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def _new_id() -> str:
    return uuid4().hex


# ---------- 输入: 职位 + 候选人 ----------

class Track(str, Enum):
    """招聘类型(Sprint 5.5 起)。
    决定 Planner 的 stage 序列 + 各 stage 题数配比;
    lateral 是历史默认(老 Job 没 track 字段时退到 lateral)。"""
    CAMPUS = "campus"      # 校招: 自我介绍 + 知识(重) + 项目(轻, lazy) + 场景(轻)
    LATERAL = "lateral"    # 社招: 自我介绍 + 知识(轻) + 项目(重, lazy) + 场景(重)


class JobContext(BaseModel):
    """HR 上传的原始资料, Planner 的输入之一。"""
    job_id: str = Field(default_factory=_new_id)
    title: str
    jd: str                                  # 职位描述原文
    requirements: list[str] = []             # 岗位要求(可由 jd 解析填充)
    company_materials: str = ""              # 公司资料(后期做 RAG 切片)
    role_family: str = "backend"             # Sprint 3-5: 题库召回按 role_family + 维度过滤
                                             # Sprint 5.9: 新建 job 时 HR 选取值, 决定 Planner stage 配比
                                             # 推荐取值: backend / frontend / data_science / product / hr
                                             # (schema 不限制字符串, 未列出的走 backend fallback 配比)
    track: Track = Track.LATERAL             # Sprint 5.5: 校招 / 社招; 老 Job 缺这列默认 lateral
    # Sprint H: 出题来源。"rag"(默认) = 题库召回 + LLM 精修 (现有流程);
    # "llm_direct" = 跳过题库, 按考察维度 + 简历技能纯 LLM 出题 (rubric+few-shot)。
    # 两套并存互不影响; 项目深挖题两模式共用同一套 (本就是 LLM 直接生成)。
    question_source: str = "rag"
    # Sprint 5.7: HR 可在新建 job 高级折叠区覆盖默认 policy;
    # None 表示用 stage 默认 / schema 默认值, 让 HR 不动也能用。
    followup_policy: "FollowUpPolicy | None" = None
    completion_policy: "CompletionPolicy | None" = None
    # Sprint 5.9: HR 定义本岗位考察的 aspect 列表 (per competency 分组);
    # 空列表时 Planner 用 role_family 默认模板; 非空时 HR 配置生效。
    # Assessor 在每 turn 看着这个列表标 covered_aspects, 整轮算 richness。
    aspects: list[ProfileAspect] = []


class ResumeSection(BaseModel):
    """简历语义分段 —— Sprint F。
    text 必须是 resume 原文的连续子串 (LLM 只定位边界, 不改写内容 ——
    简历是候选人原始材料, 改写/漏抄都破坏保真与公平)。
    列表顺序 = 文档顺序; project/internship/work 段是项目深挖题的出题单元。
    source 记切分来源作审计: llm_anchor / heuristic / whole_text。"""
    type: str = "other"      # 合法值见 RESUME_SECTION_TYPES
    title: str = ""          # 段标题, 如 "智能法律咨询系统"; 进 PlanTrace 给 HR 看
    text: str
    source: str = "heuristic"


# ResumeSection.type 的合法值; 分段器与 evals 共用
RESUME_SECTION_TYPES = (
    "personal_info", "education", "project", "internship",
    "work", "skills", "award", "other",
)
# 可用于项目深挖出题的段类型
RESUME_DEEPDIVE_TYPES = ("project", "internship", "work")


class CandidateProfile(BaseModel):
    """候选人面试前上传的简历/资料, Planner 的输入之二。
    与 JobContext 一起决定面试计划: resume 用于生成项目/实习深挖题。

    job_id Optional 是有意为之: 走 API 路径时由 path param 注入(必填),
    走 src.main / evals 这种纯内存路径时不需要(planner 不消费 job_id),
    持久化到 PG 时若仍为 None 会被 save_candidate 显式拒绝。"""
    candidate_id: str = Field(default_factory=_new_id)
    job_id: str | None = None                # 关联职位; API 落库时必填, 见 db.save_candidate
    resume: str                              # Resume 原文(后期可结构化解析)
    projects: list[str] = []                 # 已识别的项目/实习要点(可由 resume 解析填充)
    sections: list[ResumeSection] = []       # Sprint F: 语义分段, ingest 后台回填


# ---------- 面试计划 ----------

class Competency(BaseModel):
    """单个考察维度。"""
    competency_id: str = Field(default_factory=_new_id)
    name: str                                # 如 "系统设计能力"
    description: str
    weight: float = 1.0                      # 维度权重(用于内容维度加权)


class ProfileAspect(BaseModel):
    """候选人画像子维度 —— Sprint 5.9 起加。
    比 Competency 细一档: 一个 competency 下有多个 aspect, 每答一道题
    Assessor 标"这道题覆盖了哪些 aspect", 整轮面试结束时算 richness =
    已覆盖 aspect 数 / 总 aspect 数。

    HR 在新建 job 时定义 (代码不写死 role_family 模板, 仅给默认建议);
    aspect_id 内部用, name 给 HR / Assessor LLM 看, description 帮 LLM
    判断"这条答案是否覆盖了此 aspect"。"""
    aspect_id: str = Field(default_factory=_new_id)
    competency_id: str                       # 归属哪个 competency
    name: str                                # 如 "分布式系统设计"
    description: str                         # 给 Assessor 判断用的语义描述


class QuestionType(str, Enum):
    BEHAVIORAL = "behavioral"
    TECHNICAL = "technical"
    SITUATIONAL = "situational"
    OPEN = "open"


class QuestionCategory(str, Enum):
    """题目类别 —— 与 type(题目风格) 正交, 表示"考察什么"。
    Sprint 5.5 从 2 类扩到 4 类, 与 InterviewStage 一一对应。"""
    KNOWLEDGE = "knowledge"                  # 基础知识考察, JD 驱动
    PROJECT_EXPERIENCE = "project_experience"  # 项目/实习内容考察, Resume 驱动
    SELF_INTRO = "self_intro"                # 自我介绍, 永远 0 追问
    SCENARIO = "scenario"                    # 场景题, 场景题库召回 + LLM 精修


class InterviewStage(str, Enum):
    """面试阶段(Sprint 5.5 起)。
    Orchestrator 按 track 配的序列推进, 每 stage 跑完才进下一个。
    与 QuestionCategory 一一对应, 但 stage 是 round 级、category 是题级,
    一个 round 通常只装一类 category, lazy gen 的 round 例外。"""
    SELF_INTRO = "self_intro"
    KNOWLEDGE = "knowledge"
    PROJECT = "project"
    SCENARIO = "scenario"


class Question(BaseModel):
    """一道面试题。
    Sprint 5.5 起:
    - competency_id 改 Optional: self_intro 题不挂任何 competency (用 None);
      Evaluator 聚合时 q.competency_id == comp.competency_id 对 None 自动 False,
      所以 self_intro 不污染任何 DimensionScore。
    - 新增 lazy: 标志该题"计划走懒生成路径"(project stage 用), plan 时设死;
      `text == ""` 才是动态"是否已生成"的信号, 两个 signal 正交。
      生成后 lazy 保留 True 作 HR 审计可见性, 不被回写覆盖。"""
    question_id: str = Field(default_factory=_new_id)
    # self_intro 题 None; 其他题挂某个 competency
    competency_id: str | None = None
    text: str
    type: QuestionType = QuestionType.OPEN
    category: QuestionCategory = QuestionCategory.KNOWLEDGE
    # Sprint 5.5: True 表示"计划懒生成"(plan 阶段只占位 text=""),
    # 进入对应 stage 时 orchestrator 调 planner.resolve_lazy_questions 回灌 text。
    # 静态信号: 生成后不清零, 用 text != "" 判已生成。
    lazy: bool = False
    # Sprint 3-5 溯源 (knowledge 题): 从 SeedQuestion 召回 + LLM 精修时, 记录原题 id;
    # None 表示走的是 fallback / 现场生成路径, 没有题库来源。
    source_question_id: str | None = None
    # Sprint 3-6 溯源 (project 题): 从 Resume 切片召回时, 记录用到的 document_id 列表;
    # 空列表表示走的是 fallback / 现场生成路径, 没有 RAG 切片来源。
    source_chunk_ids: list[str] = []


class InterviewRound(BaseModel):
    """一轮面试: 一组维度与对应题目。
    Sprint 5.5 起加 stage; 老 Plan JSON 缺该字段默认 KNOWLEDGE
    (老链路是 knowledge + project 混在单 round, 用 knowledge 作占位防解析失败)。"""
    round_id: str = Field(default_factory=_new_id)
    index: int                               # 第几轮(从 0 开始)
    title: str
    competencies: list[Competency]
    questions: list[Question]
    stage: InterviewStage = InterviewStage.KNOWLEDGE


class QuestionTrace(BaseModel):
    """单题的出题过程记录 —— Sprint E 可观测性。
    path 取值:
    - self_intro         固定模板
    - rag_refined        题库召回 + LLM 精修
    - rag_direct         题库召回, LLM stub 直接用种子原文
    - llm_generated      题库无(未用过的)题, 纯 LLM 现场生成
    - fallback_template  LLM 也不可用, 硬编码模板
    - lazy_pending       project 占位, 等 resolve
    - resume_section     (resolve 后) 简历语义分段, 单 section 定向深挖 (Sprint F)
    - resume_rag         (resolve 后) Resume 切片 RAG + LLM 生成
    - resume_llm         (resolve 后) 无切片, resume 全文 + LLM 生成
    """
    question_id: str
    stage: InterviewStage
    category: QuestionCategory
    path: str
    topic: str | None = None          # knowledge tech slot 的 topic 分配
    difficulty: str | None = None     # 同上
    source_question_id: str | None = None
    source_chunk_ids: list[str] = []  # project 题命中的 resume 切片
    section_title: str | None = None  # Sprint F: resume_section 路径针对的段标题


class PlanTrace(BaseModel):
    """plan 生成全过程的审计记录 —— Sprint E 可观测性。
    与 plan 一起落 PG/Redis, 只在 HR 端展示 (候选人端 plan 接口必须剥掉)。
    matches 保留 query → topics 的逐条明细, matched_topics 是并集。"""
    aspect_queries: list[str] = []
    extracted_skills: list[str] = []
    matches: dict[str, list[str]] = {}
    matched_topics: list[str] = []
    unmatched_skills: list[str] = []
    # Sprint E: 这些 skill 是 embedding 没够着、由 LLM 兜底归类命中的
    llm_matched_skills: list[str] = []
    questions: list[QuestionTrace] = []


class InterviewPlan(BaseModel):
    """Planner 的输出, Interviewer 的依据。
    Sprint 5.5: 加 competencies 顶层作权威 competency 列表 (跨 stage 共享);
    round.competencies 仍保留为该 stage 涉及的子集 (用于 HR stage 视图展示),
    但 Evaluator / 聚合一律走 plan.competencies。
    老 Plan JSON 缺 competencies 时默认 [], Evaluator 会得到空 content_scores ——
    实际触发是 5.5 之后新生成的 plan, 老 plan 走完 finalize 不重跑就无影响。
    Sprint E: trace 记录出题全过程 (topic 匹配明细 + 每题来源路径), 老 plan
    为 None; 候选人端接口返回前必须置 None 防泄漏。"""
    plan_id: str = Field(default_factory=_new_id)
    job_id: str
    rounds: list[InterviewRound]
    competencies: list[Competency] = []
    trace: PlanTrace | None = None


# ---------- 面试过程 ----------

class CandidateAnswer(BaseModel):
    """候选人对某题的回答。骨架阶段仅 text;
    后期音视频通过 media_ref 引用,不改本结构。"""
    answer_id: str = Field(default_factory=_new_id)
    question_id: str
    text: str
    media_ref: str | None = None             # 后期: 音视频存储引用
    asked_at: datetime = Field(default_factory=datetime.utcnow)


class FollowUp(BaseModel):
    """Interviewer 基于回答产出的追问。"""
    followup_id: str = Field(default_factory=_new_id)
    parent_question_id: str
    text: str
    reason: str                              # 为何追问(便于审计与调试)


# ---------- Sprint 5.6: Assessor + FollowUpPolicy ----------

class AnswerAssessment(BaseModel):
    """单题在线评估结果 —— Sprint 5.6 起 Assessor 在每答一题后产出。

    合规约束 (CLAUDE.md):
    - sufficiency / confidence 是 LLM-as-judge 的中间产物, 校准前不可见;
      绝对不向 HR UI 暴露这俩数字, 也不展示给候选人。
    - missing_signals / strengths / concerns / followup_goal 是自然语言字段,
      Sprint 5.7 可在 HR 详情页"面试过程"区域展示, 但 sufficiency/confidence 不展示。
    - AnswerAssessment 既不进 EvaluationReport.content_scores (内容维度) 也不进
      performance_observations (软信号), 它是"第三类"数据, 仅作追问决策 + 内部诊断。

    字段:
    - sufficiency: 回答相对题目要求的"信号充分度", 0=完全没说到点, 1=超充分
    - confidence: Assessor 自己对该判断的把握度, 0=瞎猜, 1=很笃定
    - missing_signals: 缺哪些信号, 自然语言列表 (如"缺量化数据"/"没讲为什么")
    - strengths: 回答里的亮点
    - concerns: 让 Assessor 担心的地方 (与 missing 互补: missing 是没说, concerns
      是说了但有疑问, 如"对方说从 800ms 降到 350ms 但没说怎么测")
    - followup_goal: 如果决定追问, 应当追什么. 拼进 _followup_text 的 prompt
    - stop_reason: 不建议追问时的理由 (如 sufficient_signals / low_value /
      diminishing_returns). 空串 = 没意见, 由 Policy 决定。
    """
    question_id: str
    sufficiency: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    missing_signals: list[str] = []
    strengths: list[str] = []
    concerns: list[str] = []
    followup_goal: str = ""
    stop_reason: str = ""
    # Sprint 5.9: 该回答覆盖了 JobContext.aspects 里的哪些 aspect_id;
    # Assessor LLM 在 prompt 里拿到该题归属 competency 下的 aspect 候选列表,
    # 然后判定回答里实际触达了哪些。整轮所有 covered_aspects 的并集 / 全 aspect
    # = profile_richness. 老 AnswerAssessment 缺该字段时默认 [] (不影响 richness 计算)。
    covered_aspects: list[str] = []


class FollowUpPolicy(BaseModel):
    """追问策略 —— Sprint 5.6 起 Interviewer 用本结构 + AnswerAssessment 决策。

    Sprint 5.6 阶段 stage 默认值在 Interviewer 内硬编码, 不读 JobContext;
    Sprint 5.7 起允许 JobContext.followup_policy 覆盖 (HR 在新建 job 高级折叠
    区配置)。

    决策语义 (per question):
    - 已有 followups >= max_followups_per_question -> 停 (硬上限)
    - assessment.sufficiency >= min_sufficiency_to_stop AND
      assessment.confidence >= min_confidence_to_stop -> 停 (拿到足够信号)
    - 否则追问

    阈值取值 0.0-1.0 与 AnswerAssessment 字段对齐。

    Sprint 6.5 F1: min_sufficiency_to_stop 0.7 -> 0.6。Assessor prompt 加分类别
    评分锚点后 sufficiency 量表整体下移 (虚高 0.9+ 回归 0.65-0.85), 阈值必须
    跟着重校 —— 新锚点里 0.4-0.6 = "有经历但缺量化/深度" (该追问), ≥ 0.65 =
    有效证据 (不该追)。改 Assessor 量表必须连带复核本阈值, 复验走 sim 批次。"""
    max_followups_per_question: int = 1
    min_sufficiency_to_stop: float = 0.6
    min_confidence_to_stop: float = 0.5

    @classmethod
    def for_stage(cls, stage: "InterviewStage") -> "FollowUpPolicy":
        """stage 默认配额表 (Sprint 5.6 硬编码; 5.7 HR 覆盖留口子)。
        self_intro 0 追问 (Interviewer 已硬豁免, 这里 max=0 是双保险);
        knowledge 1; project 2 (深挖更重要); scenario 2。"""
        max_table = {
            InterviewStage.SELF_INTRO: 0,
            InterviewStage.KNOWLEDGE: 1,
            InterviewStage.PROJECT: 2,
            InterviewStage.SCENARIO: 2,
        }
        return cls(max_followups_per_question=max_table.get(stage, 1))


class CompletionPolicy(BaseModel):
    """面试结束策略 —— Sprint 5.7 起 Interviewer 用本结构 + competency_coverage
    决策是否提前结束面试。

    决策语义 (next_turn 末尾):
    - 已答题数 >= max_total_questions -> done (硬上限, 防无限循环兜底)
    - mandatory competency 全部 coverage >= min_competency_coverage -> done
      (提前结束, 信号足够不必继续)
    - 还有未答的 plan 题 -> 返回下一题 (信号不够, 继续走完计划)
    - 题答完但 coverage 不达标 -> done, Evaluator 标 evidence_insufficient

    **绝不做动态补题**: 题库由 plan + lazy gen 一次确定, coverage 不够也
    不能让 LLM 现场生成新题 (公平性 + 可复现性双坍方)。

    字段:
    - min_competency_coverage: 每维度 coverage 的最低门槛, 与
      FollowUpPolicy.min_sufficiency_to_stop 对齐让 mental model 一致
    - max_total_questions: 硬上限, 含 followup 在内 (sprint 5.5 默认 plan ~7-8 题,
      留 buffer 给追问)
    - mandatory_competencies: 空数组 = plan.competencies 全部 mandatory (默认);
      非空时只检查列出的 competency_id, 让 HR 可以挑哪些维度必须达标。

    Sprint 5.7 起允许 JobContext.completion_policy 覆盖默认值。

    Sprint 5.9: 加 min_total_questions + min_profile_richness, 决策升级为
    "至少答足 min_total 题 + richness >= min_profile_richness → 提前 done";
    max_total_questions 是含追问的硬上限。默认值在 task 89 Planner 升到
    25-30 题预算时一起翻 (min_total=25 / max_total=30 / min_richness=0.6);
    在那之前默认仍是 Sprint 5.7 的 7-题预算 (min_total=0 / max_total=15)
    以兼容老 e2e eval。"""
    # Sprint 6.5 F5: 0.7 -> 0.6, 随 Assessor 量表重锚同步 (新量表下 0.6+ 即
    # 有效证据; 0.7 会让 medium 档永远"不覆盖", insufficient flag 沦为全员噪声)。
    min_competency_coverage: float = Field(default=0.6, ge=0.0, le=1.0)
    # Sprint 6.5 F5 第二轮: 提前结束前每个 mandatory competency 至少要有
    # 几道**不同的题**被评估过。coverage 取 max() 对单发幸运分敏感 ——
    # 对抗批次坐实 copy-paste 靠一道 knowledge 教科书答案触发提前结束逃过
    # 追问; >= 2 让提前结束至少建立在两个独立数据点上, strong 多答 1-2 题
    # 的代价换稳健。
    min_assessed_per_mandatory: int = Field(default=2, ge=1)
    min_total_questions: int = Field(default=0, ge=0)
    max_total_questions: int = Field(default=15, gt=0)
    min_profile_richness: float = Field(default=0.0, ge=0.0, le=1.0)
    mandatory_competencies: list[str] = []


class TurnRole(str, Enum):
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"


class Turn(BaseModel):
    """对话历史的一个回合。"""
    role: TurnRole
    text: str
    ref_id: str | None = None                # 关联的 question/followup/answer id
    at: datetime = Field(default_factory=datetime.utcnow)


class SessionStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class InterviewSession(BaseModel):
    """一次面试的完整状态。Interviewer 读写, Evaluator 消费。
    骨架阶段在内存; Sprint 1 起热存 Redis, 结束归档 Postgres。

    Sprint 5.5 起加 intro_text: 候选人 self_intro 阶段的回答全文,
    会喂给 project stage 的 lazy 出题 prompt + 回灌 Evaluator 作软信号上下文。
    老 Session JSON 缺该字段默认空字符串。

    Sprint 5.6 起加 assessments: 每答一题, Assessor (启用时) 跑一次产出一条
    AnswerAssessment 追加进来。Redis 热存随 session 走; PG 列 + HR UI 留 5.7。
    ASSESSOR_ENABLED=false 时本列表恒空。

    Sprint 6-5 起加 media_ref: 面试录像归档引用 (本地路径 / 未来对象存储 URI)。
    finalize 时单点写入 (避免与 submit_answer 竞争 Redis 读改写), 仅作 HR
    复核素材溯源 —— **绝不**被任何打分路径消费 (§7 只录不判)。"""
    session_id: str = Field(default_factory=_new_id)
    plan_id: str
    job_id: str
    status: SessionStatus = SessionStatus.CREATED
    current_round: int = 0
    history: list[Turn] = []
    answers: list[CandidateAnswer] = []
    intro_text: str = ""
    assessments: list[AnswerAssessment] = []
    media_ref: str | None = None


# ---------- 多模态信号(扩展, 骨架恒空) ----------

class SignalKind(str, Enum):
    LANGUAGE = "language"                    # 语言(基于转写文本)
    TONE = "tone"                            # 语气/韵律(基于音频)
    GAZE = "gaze"                            # 视线/表情(基于视频)


class Signal(BaseModel):
    """多模态软信号。仅作为参考证据, 带置信度。
    合规约束: 绝不进入 overall 计算, 只出现在 performance_scores。"""
    kind: SignalKind
    value: str                               # 描述性, 非分数
    confidence: float                        # 0~1
    source: str                              # 来源说明(便于审计)


# ---------- 评估报告 ----------

class DimensionScore(BaseModel):
    competency_id: str
    score: float                             # 0~100
    evidence: list[str]                      # 支撑该评分的对话证据


class PerformanceObservation(BaseModel):
    """表现维度观察, 来源于软信号。与内容维度严格分离。"""
    kind: SignalKind
    observation: str
    confidence: float
    note: str = "参考信息, 不计入总分, 建议人工复核"


class User(BaseModel):
    """HR / admin 用户 —— Sprint 5-1 起。
    密码 hash 永远不出现在 pydantic 层, 只走 ORM。"""
    user_id: str
    username: str
    role: str  # "hr" | "admin"


class ReviewDecision(str, Enum):
    """HR 复核的最终结论。这里只表"建议", 是否真的录用由企业自己的流程决定。"""
    RECOMMEND = "recommend"
    REJECT = "reject"
    BORDERLINE = "borderline"


class DimensionOverride(BaseModel):
    """HR 在复核时对某个内容维度的分数 / 备注覆盖。
    不直接改 EvaluationReport.content_scores, 而是单独留档 —— 保留 AI 原始
    结论的可审计性, 同时让人工判断有自己的存储位置。"""
    competency_id: str
    score: float                              # 0~100, 与 DimensionScore 同口径
    note: str = ""


class ReviewRecord(BaseModel):
    """Sprint 5-2 起: HR 对某份 EvaluationReport 的复核结论 + 注释 + 维度覆盖。

    设计:
    - 一份 report 当前只允许一条 review (PATCH 同 report_id 覆盖, 不做版本历史)。
      真要追溯时, 可以加 created_at + 列出所有版本, 现在 MVP 不做。
    - dimension_overrides 与 EvaluationReport.content_scores 解耦, 保留 AI
      原始结论。HR 端 UI 同时展示两套, 让差异显式。
    - decision 是 HR 给的"建议", 不是最终结论 (真录用流程在企业内部)。
    """
    record_id: str = Field(default_factory=_new_id)
    report_id: str
    reviewer_id: str                         # users.user_id, 哪个 HR 复核的
    comments: str = ""
    dimension_overrides: list[DimensionOverride] = []
    decision: ReviewDecision
    reviewed_at: datetime = Field(default_factory=datetime.utcnow)


class SeedQuestion(BaseModel):
    """种子题库中的一道题 —— Sprint 3 起。
    Planner 按维度从题库召回 (Milvus) 后再由 LLM 精修, 替换原来的现场生成。
    PG 是真理之源, Milvus 仅作检索副本。

    question_id 用内容哈希 (sha256(role+competency+text)[:16]), 让脚本可重跑:
    同内容 = 同 id = upsert 不重复。

    Sprint 5.5 起加 category 区分 knowledge / scenario 两类题源:
    - KNOWLEDGE: 知识考察, 历史 default; 老题库 ALTER 加列时全落到这一类
    - SCENARIO: 场景题(线上故障、设计权衡等), Sprint 5.5 新加
    SELF_INTRO / PROJECT_EXPERIENCE 不进种子库 (前者每场现拿候选人答案, 后者
    走 Resume RAG 现场生成); 调用方/CLI 自行约束写入值。"""
    question_id: str
    role_family: str                         # "backend" / "frontend" / "data_science" / ...
    competency: str                          # "技术深度" / "沟通协作" / ...
    text: str
    source: str = "llm_generated"            # llm_generated / fallback_template / human_curated / reviewed_llm_derived
    category: QuestionCategory = QuestionCategory.KNOWLEDGE
    # Sprint C: 知识库审核后入库的题源信息。Milvus 副本不带这些字段, 因为
    # pymilvus 不支持 ALTER (改要 drop+reseed 现有题库); Planner 召回不变,
    # 这些字段仅在 PG 用于审计 / 多 dataset 撞车时的离线分析。
    dataset_id: str = "default"              # 数据集隔离 (javaguide-basis-smoke / ...)
    source_draft_id: str | None = None       # 反查 QuestionDraft, 审计 prompt 演进
    key_points: list[str] = []               # 评分要点 (Sprint F+ Evaluator 可用)
    difficulty: str = ""                     # easy / medium / hard (审核题继承自 draft)
    qtype: str = ""                          # concept / compare / scenario / followup


class TurnResult(BaseModel):
    """Orchestrator 的一次推进结果。
    start_session / submit_answer / resume_session 都返回这个,
    调用方据此决定下一步: 还要继续答(prompt 非空) 还是已结束(done=True)。"""
    session_id: str
    done: bool                               # True 表示面试已走完, 接下来该 finalize
    prompt: str | None = None                # 下一句面试官话: question 或 follow-up
    ref_id: str | None = None                # 对应 history 里 interviewer turn 的 ref_id


class EvaluationReport(BaseModel):
    """Evaluator 的输出。
    内容维度(content_scores) 与表现维度(performance_observations) 分区。
    overall 只基于 content_scores 加权, 不依赖任何软信号。

    Sprint 5.7 起加 competency_coverage: 每维度证据充分性聚合 ∈ [0, 1],
    由 max(sufficiency) over session.assessments 同 competency 求得;
    任一 mandatory 维度 < CompletionPolicy.min_competency_coverage 时,
    summary 自动加 "证据不充分, 建议人工面谈" 前缀句 + needs_human_review=True。
    老 Report 缺该字段时默认空 dict, 兼容 5.7 之前归档的报告。"""
    report_id: str = Field(default_factory=_new_id)
    session_id: str
    content_scores: list[DimensionScore]                    # 内容维度: 进总分
    performance_observations: list[PerformanceObservation] = []  # 表现维度: 仅参考
    overall: float                                          # 仅由 content_scores 得出
    summary: str
    needs_human_review: bool = True                         # 默认需人工复核
    # Sprint 3-7 RAG 溯源: 评估时召回的 JD/公司资料 document_id 列表;
    # 空列表表示没用 RAG (Milvus 未配置 / 召回为空 / embed stub)
    rag_context_chunk_ids: list[str] = []
    # Sprint 5.7: 每维度证据充分性, key=competency_id value ∈ [0, 1]
    competency_coverage: dict[str, float] = {}


# ---------- Sprint A 知识库: L1 chunk 层 (反向出题的底座) ----------

class KnowledgeChunk(BaseModel):
    """通用 md 知识库切片 —— Sprint A 起。
    HR 上传的 md 文档 (JavaGuide / 前端资料 / 内部 wiki) 经 ingest_md_corpus
    脚本切分后落进 knowledge_chunks 表, 是 L1 知识层。L2 SeedQuestion 由它
    LLM 反向派生 (Sprint B), 中间表 question_chunk_link 关联回此处。

    chunk_id 用 sha256(text)[:16]: 跨文件 / 跨 dataset 同内容 = 同 id, 天然
    去重; 但 dataset_id 不进 PK, 同 chunk 在两个 dataset 都出现时按后写覆盖
    (sprint A smoke test 不会触发, sprint E 真去重时再改 PK 策略)。

    切分策略 (探查 docs/java/basis 后定): 切到 H3 叶子标题, H4 内容算进所属
    H3 chunk; heading_path 是 ["H2", "H3"] 两层。
    is_starred 提取作者用 ⭐️ 标的精选题, 给 Sprint B 反向出题优先级用。"""
    chunk_id: str
    source_repo: str                 # "javaguide" / "frontend-wiki" / ...
    source_commit: str               # 固定 commit, 可复现
    dataset_id: str = "default"      # Planner 召回隔离 (Sprint E 接入)
    file_path: str                   # 相对 root 的 posix 路径
    doc_title: str = ""              # frontmatter.title; 空表示无 frontmatter
    doc_tags: list[str] = []         # frontmatter.tag, 扁平 list
    domain: str = ""                 # file_path 第一段, e.g. "java"
    topic: str = ""                  # file_path 第二段, e.g. "basis"
    heading_path: list[str] = []     # ["基础概念与常识", "⭐️ JVM vs JDK vs JRE"]
    is_starred: bool = False         # 叶子 heading 以 "⭐️" 起 (作者精选)
    text: str
    char_count: int = 0              # 抽样统计用
    content_hash: str                # 全 sha256(text), 增量更新比对
    quality_tag: str = "ok"          # ok / navigation / low_value / oversize


class SkillBacklog(BaseModel):
    """Sprint B+D: 候选人 resume 抽出的 skill 没匹配上任何 dataset.topic 时, 落表。
    HR 后续用 SQL 看 backlog 决定要不要专门 ingest 新 dataset (扩库)。

    幂等键 sha256(skill)[:16]: 同 skill 重复落只一行, count 字段记累计被多少候选人提到。
    候选人 / job 上下文存最后一次出现的 (用于审计), 真正的频次看 count。"""
    skill_id: str                    # sha256(normalized skill)[:16]
    skill: str                       # 原文 (大小写保持)
    count: int = 1                   # 累计次数, upsert 时 +=1
    last_job_id: str = ""            # 最后一次出现的 job 上下文
    last_candidate_id: str = ""      # 同上


class Dataset(BaseModel):
    """数据集元数据 —— Sprint D-lite。
    一个 dataset_id 一行, 记录人写的 topic (e.g. "JAVA 基础") + 源信息。

    与 KnowledgeChunk/QuestionDraft/SeedQuestion 的 dataset_id 一一对应。
    HR ingest md 时先 upsert 这条, 再灌 chunk; 召回路径可按 topic / difficulty
    过滤 (Milvus 同字段同步).

    Sprint upload: 加 category 让 HR 选 dataset 整批是 knowledge 还是 scenario,
    决定 derive 用哪套 prompt + SeedQuestion 落库时 category 一并继承。
    project_experience / self_intro 不开放 HR 选 (前者用 resume RAG 现场生成,
    后者固定文本)."""
    dataset_id: str                  # 主键, e.g. "javaguide-basis-smoke"
    topic: str                       # HR 写的主题词, e.g. "JAVA 基础"
    description: str = ""            # 可选: 数据集详情/备注
    source_repo: str = ""            # 通常等于 ingest 的 --source-name
    source_commit: str = ""          # ingest 时固定的 git commit (可复现)
    category: str = "knowledge"      # knowledge / scenario (面试阶段类型, 与 qtype 正交)


class DerivedQuestion(BaseModel):
    """LLM 反向出题单题结构 —— Sprint B 中间产物, 不入库。
    derive_chunk() 调 LLM, 解析 JSON 后产出 list[DerivedQuestion],
    CLI 再拼上 chunk_id / dataset_id / prompt_version 落进 QuestionDraft。"""
    question_text: str
    qtype: str                       # concept / compare / scenario / followup
    difficulty: str                  # easy / medium / hard
    key_points: list[str] = []       # 评分要点 3-5 条, Evaluator 后续可用


class QuestionDraft(BaseModel):
    """L2 题库待审核条目 —— Sprint B 起。
    审核流: derive 写入 (status=pending) → HR 审核 (Sprint C) → approved 的
    INSERT 进 seed_questions 留 source_draft_id 溯源, rejected 留底不入题库。

    draft_id = sha256(chunk_id + prompt_version + question_text)[:16]
    保证: 同 chunk 同 prompt 同题文本只入一条; 改 prompt 重跑会得到不同
    draft_id (即便题文本巧合相同) 便于审计 prompt 演进。"""
    draft_id: str
    chunk_id: str                    # 派生源 chunk; Sprint E 真去重前一对多关系
    dataset_id: str                  # 跟 chunk 的 dataset_id 一致, 按 dataset 跑/筛
    question_text: str
    qtype: str
    difficulty: str
    key_points: list[str] = []
    prompt_version: str              # sha256(system_prompt + category)[:8], 改 prompt 或 category 自动失效
    llm_model: str                   # 实际调用的模型名 (gpt-4o-mini / ...)
    review_status: str = "pending"   # pending / approved / rejected
    category: str = "knowledge"      # 继承自 Dataset.category, approve 时透传到 SeedQuestion