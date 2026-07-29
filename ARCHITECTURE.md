# ARCHITECTURE — AI Interview Platform

## 1. 目标与定位

一个多 agent 的 AI 视频面试平台。HR 上传 JD / 岗位要求 / 公司资料后，系统：

1. 自动生成结构化面试计划（多阶段、考察维度、题库召回）
2. 自动执行多阶段面试并基于回答质量动态追问
3. 自动产出结构化评估报告 + 支持 HR 人工复核

终态是「招聘端 + 候选人端」双边 AI 面试基础设施。

**设计原则**：核心价值与最大风险都在 agent 编排（计划质量、追问逻辑、报告结构），
而非实时媒体层。因此先用纯文本把 agent 链路做扎实，媒体层与多模态作为输入/输出
适配器后接。

**双 track 设计**（Sprint 5.5 起）：`JobContext.track` 区分 `campus`（校招）/
`lateral`（社招）。校招重基础知识考察，社招重项目深挖与场景题。track 影响 Planner
的 stage 配额，不改 agent 内核。

---

## 2. 多 Agent 分解

```
   JD/Resume         ┌──────────┐
   track + 资料  → │ Planner  │ → InterviewPlan
                    └────┬─────┘    (多 stage; 项目题占位等懒生成)
                         │
                         ▼
   ┌────────────────────────────────────────────────────────────┐
   │  面试主循环（Orchestrator 编排）                          │
   │                                                            │
   │    Interviewer ──下一题──→ 候选人 ──回答──→ Assessor      │
   │         ▲                                       │          │
   │         │             AnswerAssessment          │          │
   │         └─── 追问 / 进下一题 / 结束面试 ←──────┘          │
   │              (FollowUpPolicy + CompletionPolicy 决策)     │
   │                                                            │
   │    * Project stage: Interviewer 调 Planner 懒生成题目    │
   └────────────────────────────────────────────────────────────┘
                         │
                         ▼ 面试结束
                    ┌───────────┐
                    │ Evaluator │ → EvaluationReport
                    └───────────┘   (内容/表现严格分区
                                     + competency_coverage)

   扩展层（Sprint 7）：
   ┌─────────────────────────┐
   │  Multimodal Analyzer    │ 音视频帧 → 软信号 → Evaluator 表现维度
   └─────────────────────────┘   （仅参考，见 §7 合规约束）
```

所有 agent 之间不直接互相调用，统一由 Orchestrator 传递上下文。

### 2.1 Planner Agent（规划）
- **输入**：`JobContext`（含 track）+ `CandidateProfile` + Milvus 题库/资料召回
- **输出**：`InterviewPlan`——若干 stage，每 stage 含 Competency 与 Question
- **职责**：把岗位要求映射成可考察的能力维度，按 track 配置 stage 占比，为每
  stage 生成题目

**Track-aware stage 配比**（Sprint 5.5 起；Sprint 6.5 F5 收敛为主问题 12 +
追问预留 3 = `CompletionPolicy.max_total_questions` 15，plan 必须能在 cap 内问完）：
- **校招**：self_intro(1) → knowledge(6) → project(3 占位) → scenario(2) = 12 题
- **社招**：self_intro(1) → project(6 占位) → scenario(3) → knowledge(2) = 12 题
- 改配比或 cap 必须两边同看，并跑 sim 批次复验（见 EVALUATION.md）

**懒生成（Lazy Generation）**：项目深挖题在 Planner 阶段先**占位**（text 为空，
`lazy=True`），候选人答完 self_intro 后，Interviewer 在进入 project stage 时把
`intro_text` + Resume RAG 一起喂给 Planner 实时生成。理由：项目题真正能"针对候选人
刚说过的"才有价值。

### 2.2 Interviewer Agent（面试官 / 追问）
- **输入**：当前 `InterviewPlan` + 已有对话历史 + 候选人最新回答 + 最近一次
  `AnswerAssessment`（来自 Assessor）+ `FollowUpPolicy`
- **输出**：下一个 `Question`，或基于回答的 `FollowUp`，或面试结束信号
- **职责**：面试对话的主循环。结合 Assessor 的评估与 stage-aware policy 决策
  "是否追问 / 进下一题 / 提前结束"

**Stage 特殊处理**：
- `self_intro`：不触发追问启发式（30 字也不追问），完整内容落到
  `InterviewSession.intro_text` 供后续题目用
- `knowledge`：max_followups=1（默认）
- `project` / `scenario`：max_followups=2（默认）

**追问决策**（Sprint 5.6 启发式升级 → Sprint 8.3 校准 + 信念驱动）：
- **真 LLM 路径**（assessment 带 `calibrated_sufficiency`，Platt 校准分）：
  `calibrated >= min_calibrated_to_stop`（默认 0.9269 ≡ raw 0.6）且
  `confidence >= 阈值` → 停；想追问还要过两道门——全局追问预算
  （`total_followup_budget`，默认 5）与信念方差门（该 competency 的
  belief `variance < 0.03 且 mean >= 0.8` = 证据足且已确立为佳 → 不再耗预算，
  预算流向高方差维度）
- **启发式路径**（LLM 失败/超时，calibrated 为 None）：raw
  `min_sufficiency_to_stop` 双阈值，行为与 5.6 一致，门不生效——**双路径
  并存**，启发式不删
- 每题硬上限 `max_followups_per_question`（stage 默认表 self_intro=0 /
  knowledge=1 / project=2 / scenario=2，HR 可整体覆盖）；F1 预算守卫：
  追问只花「问完剩余正题后的盈余」，不许挤掉尾部题
- 该题 competency 有 doubted claim（CandidateModel）时，追问 prompt 注入
  澄清目标（走既有配额，不加问数）

### 2.3 Assessor Agent（单题在线评估，Sprint 5.6 起）
- **输入**：当前 Question + 最新 CandidateAnswer + Session 上下文 + Competency 定义
- **输出**：`AnswerAssessment`（sufficiency / confidence / missing_signals[] /
  strengths[] / concerns[] / followup_goal / stop_reason）
- **职责**：判断"这道题已有的证据是否足以评估对应的 competency"

**为什么独立成 agent（不挂在 Evaluator）**：
- Evaluator 只在面试结束时跑一次，是同步重调用（综合所有题、RAG context、报告合成）
- Assessor 每 turn 跑一次，是实时轻调用，走小模型（gpt-4o-mini），必要时降级
- 并发模型 / 延迟需求 / 模型选型完全不同，强行合并会污染抽象

**延迟与降级护栏**（Sprint 5.6 设计约束）：
- 用 `gpt-4o-mini` 而非 `gpt-4o`；重复 prompt 命中 LLM cache
- 所有 LLM 调用 try/except + 10s 超时；异常时 fallback 到原启发式
- 前端 session 页显式展示"分析中..."状态，避免误以为卡死

**校准纪律**（已通过并持续生效）：核心金标 50 条（29 suf / 13 insuf / 8 amb）
+ 对抗扩展 8 条（正确废话上限 + 防矫枉过正守卫），`sim/calibrate_assessor`
全过才允许 prompt 变更合入;`ASSESSOR_ENABLED` 默认 true。Sprint 8.3 起
真 LLM 路径额外产出 `calibrated_sufficiency`（Platt 映射,
`assessor/calibration.py` 常数随代码版本固定）;Sprint 8.5 起题带 rubric 时
逐项判定产出 `rubric_hits`;`via` 字段标注走了哪条路径（llm / heuristic /
rule_duplicate）供审计。

### 2.4 Evaluator Agent（最终评估）
- **输入**：完整 `InterviewSession`（对话历史 + assessments[]）+ Plan +
  公司资料 RAG +（扩展）多模态 Signal
- **输出**：`EvaluationReport`——按 competency 打分 + 证据 + 软信号 +
  `competency_coverage`（Sprint 5.7 起）
- **职责**：把对话证据化、结构化，总结报告。Sprint 3-7 起 summary 接 JD/公司资料
  RAG，让评估有岗位语境

**打分主路径**（Sprint 6.5 修复 + 8.5 升级）：维度分 = 100 × mean(该维度
被问过的题的题级质量)；题级质量 = 0.7 × best sufficiency + 0.3 × best
rubric 命中率（题无 rubric / 无有效 hits 时退纯 sufficiency，老 plan 兼容）。
只对实际被问过的题求均值——系统提前结束不反罚候选人，覆盖缺口由
coverage + evidence_insufficient 表达。字数启发式仅作 assessments 全缺时
的保底（已知饱和于 95，不删）。

**多裁判 panel**（Sprint 8.5,**默认关**）：`EVAL_PANEL_ENABLED` 开启后
3 judge 中位数替代公式分、极差过阈标 judge_disagreement 转人工复核;
未过报告级 MAE 校准门禁（`sim/calibrate_evaluator`）不许开。
CandidateModel 的 contradicted 条目会进 summary 输入（如实提及,不替 HR 裁决）。

**合规分区**（见 §7）：`content_scores` 主区 + `performance_observations` 副区
"仅参考"。`overall` 严格只由 `content_scores` 加权得出，不依赖软信号。

### 2.5 Multimodal Analyzer Agent（多模态分析，Sprint 7 扩展）
- **输入**：音视频流 / 帧 / 转写文本
- **输出**：语言、语气、视线等 `Signal`，附置信度
- **职责**：产出*参考证据*，不参与自动打分。详见第 7 节。

### 2.6 Orchestrator（编排器）
- 管理 `InterviewSession` 生命周期与状态机（start / submit / resume / finalize 三段式）
- 在各 agent 间传递上下文（Interviewer ↔ Assessor ↔ Evaluator 不直接互调）
- 负责持久化（Postgres 归档）与会话态缓存（Redis 热存储 + TTL）
- 触发 Planner 懒生成（进 project stage 时,注入 intro_text + 存疑 claim）
- **独家写入**内部状态（blackboard 模式）：competency 信念更新（8.3）、
  CandidateModel 沉淀与 stage reflection（8.4）、integrity_flags（8.1）
- per-session Redis 锁（Sprint 9）：submit/finalize 是读改写序列,并发同会话
  请求拒 409

### 2.7 横切基础设施（agent 可读,orchestrator 写）

- **决策 Trace**（Sprint 8.2,`src/trace`）：contextvars 旁路收集——每个
  追问/结束/评估决策的实际阈值数值 + 全部 LLM 请求响应（request_hash 与
  LLM 缓存 key 同源）。Redis 与 session 同 TTL,finalize 归档 PG
  `decision_traces`;`REPLAY_MODE=1` 按 hash 确定性回放,miss 抛
  ReplayDivergence;golden trace diff 是改 prompt/policy 的回归门禁
- **Competency 信念**（Sprint 8.3,`src/beliefs.py`）：高斯共轭
  （μ0=0.5, σ0²=0.25, σ_obs²=0.09）,方差只随观测数单调降、均值顺序不变;
  只吃校准路径的观测,stub 环境恒空
- **CandidateModel**（Sprint 8.4,`src/candidate_model.py`）：strengths/
  concerns 沉淀为 SkillClaim（claimed/verified/doubted/contradicted + 原文
  evidence 双轨）;合并/矛盾全确定性规则,stage 收尾一次 reflection LLM
  蒸馏（失败跳过）;消费点 = lazy 出题 / 追问澄清 / summary 矛盾提示
- 以上全部是**内部数据**：不进总分、不进 HR UI 明细、不见候选人,仅决策 + 审计

---

## 3. 核心数据结构（src/schemas/，pydantic）

### 3.1 配置与契约
- `JobContext` — JD + 岗位要求 + 公司资料 + `track`（campus/lateral）+
  `role_family` + `question_source` + `aspects[]`（画像子维度,5.9）+
  `followup_policy` / `completion_policy`（HR 高级覆盖）+
  `owner_user_id`（6.8 多租户隔离,agent 内核不消费）
- `Competency` — 单个考察维度（名称、描述、权重）;`ProfileAspect` — 更细一档
  的画像子维度（richness 计算用）
- `Question` — 题目（competency_id, text, type, `category`：SELF_INTRO /
  KNOWLEDGE / PROJECT_EXPERIENCE / SCENARIO，`source_question_id` /
  `source_chunk_ids` 召回溯源，`lazy: bool`，`rubric[]` 二元评审 checklist
  随 plan 固定,8.5）
- `InterviewRound` — 一个 stage（`stage`：self_intro/knowledge/project/scenario），
  含 Competency 与 Question
- `InterviewPlan` — stage 列表，与 candidate_id 在 PG 关联
- `CandidateProfile` — 候选人资料（resume + projects + job_id soft-auth）
- `SeedQuestion` — 题库种子（PG 真理之源，Milvus 检索副本）

### 3.2 面试过程
- `CandidateAnswer` — 候选人回答（text，后期可加 media_ref）
- `FollowUp` — 追问（parent_question_id, text, reason / followup_goal）
- `Turn` — 对话历史单元（role: interviewer/candidate, text, ref_id）
- `InterviewSession` — 完整会话状态（plan_id, status, history, answers,
  `intro_text`, `assessments[]`, `media_ref` 录像溯源 6-5,
  `integrity_flags[]` 8.1, `beliefs{}` 8.3, `candidate_model` 8.4）
- `AnswerAssessment`（5.6 起,持续扩展）— 单题评估（sufficiency, confidence,
  missing_signals[], strengths[], concerns[], followup_goal, stop_reason,
  covered_aspects[] 5.9, `via` 路径审计 8.2, `calibrated_sufficiency` 8.3,
  `rubric_hits[]` 8.5）
- `CompetencyBelief`（8.3）/ `SkillClaim` + `CandidateModel`（8.4）/
  `LLMCallRecord` + `DecisionSpan` + `DecisionTrace`（8.2）— 内部决策与审计
  数据,见 §2.7
- `FollowUpPolicy` — 追问策略（max_followups_per_question,
  min_sufficiency_to_stop / min_confidence_to_stop,
  `min_calibrated_to_stop` 8.3, `total_followup_budget` +
  `min_variance_to_probe` + `min_established_mean` 信念门 8.3）
- `CompletionPolicy`（5.7）— 结束策略（min_competency_coverage,
  min_assessed_per_mandatory F5, min/max_total_questions,
  min_profile_richness 5.9, mandatory_competencies[],
  `use_belief_lcb` 灰度开关 8.3）
- `TurnResult` — orchestrator 推进结果（session_id, done, prompt, ref_id）

### 3.3 评估与复核
- `Signal` — 多模态软信号（kind, value, confidence, source）
- `DimensionScore` — 单维度评分（competency_id, score, evidence[]）
- `PerformanceObservation` — 表现维度观察（仅参考，不进总分）
- `EvaluationReport` — content_scores + performance_observations + overall +
  summary + `rag_context_chunk_ids` + `competency_coverage`（Sprint 5.7）
- `ReviewRecord`（Sprint 5）— HR 人工复核（comments, dimension_overrides,
  decision: recommend/borderline/reject）
- `DimensionOverride` — HR 在维度上的分数/备注覆盖

### 3.4 鉴权与租户
- `User`（Sprint 5）— HR / admin 账号，`hashed_password` 不出 pydantic 层;
  Sprint 6.8 起 HR 可自助注册（可选邀请码）,岗位挂 `owner_user_id`,
  候选人/plan/session/report 经 job 派生归属,越权一律 404,admin 全量
- `CandidateProfile.injection_suspected`（8.1）— 简历净化标记,只标不拦

---

## 4. 技术栈

| 层 | 选型 | 引入阶段 |
|----|------|----------|
| Agent 核心 | Python 3.11+ | Sprint 0 |
| LLM SDK | **openai**（Chat + Embedding 单 provider）| Sprint 3 |
| Agent 编排 | 自研轻量编排 | Sprint 0 |
| 数据结构 | pydantic v2 | Sprint 0 |
| 持久化 | Postgres（SQLAlchemy 2.0 + psycopg3）| Sprint 1 |
| 会话态 / 缓存 | Redis（redis-py）| Sprint 1 |
| API 层 | FastAPI | Sprint 2 |
| 向量检索 / RAG | Milvus Lite（dev/eval 单进程）/ Milvus standalone（docker,多进程,已落地）| Sprint 3 / 9 |
| 任务队列 | RQ（Redis broker + 独立 worker,默认关;不可用退 BackgroundTasks）| Sprint 9 |
| 鉴权 | JWT（HS256）+ bcrypt + httpOnly cookie（cookie 优先 Bearer 兜底,5.8）+ 自助注册（6.8）| Sprint 5–6.8 |
| 外部文档源 | 飞书 OpenAPI（tenant/user token,凭证 Fernet 加密可前端配置）| Sprint 6.7 |
| 候选人前端 | Next.js 16 + TypeScript + Tailwind 4 | Sprint 4 |
| HR 前端 | Next.js 同上（登录态走 httpOnly cookie,非 localStorage）| Sprint 5 |
| 实时媒体 | 回合制：HTTP audio（TTS）+ WS 流式 STT；WebRTC 仅真口型数字人层引入 | Sprint 6 |
| 数字人 | 三态视频循环（Tier B）→ 自托管 LiveTalking + MuseTalk（Tier A）；纯文字保底 | Sprint 6 |
| 语音 TTS/STT | provider 按 region 路由：火山（国内）/ Azure Speech（海外） | Sprint 6 |
| 多模态分析 | 视觉 / 音频模型 | Sprint 7 |

注：Sprint 3 中段把 LLM 从 anthropic 切到 OpenAI 单一 provider（embedding 一直是
OpenAI，consolidate 简化 key 管理与计费）。

### 4.1 Redis 的角色
- **会话状态**：进行中的 `InterviewSession` + `InterviewPlan` + `DecisionTrace`
  是强状态，Redis 做热存储（同 TTL 24h），Postgres 做持久归档
- **缓存**：LLM 调用结果（7d TTL）+ embedding 结果（30d TTL）+ TTS 音频，
  key 含 model 名，换模型自然不撞老缓存
- **任务队列 broker**（Sprint 9 落地）：RQ 队列走**专用 raw 连接**
  （任务体是 pickle 二进制,业务连接的 decode_responses=True 会解码炸）
- **并发锁**（Sprint 9）：per-session `SET NX` 锁,submit/finalize 读改写序列
  的互斥;Lua token 比对释放,TTL 兜底防死锁

### 4.2 Milvus 的角色
- **题库语义检索**：按 role_family + competency + category 召回最相关题目，Planner
  在召回基础上让 LLM 精修
- **公司资料 / JD / Resume RAG**：对上传材料切片，面试题生成与最终评估时召回相关
  片段。Question 上记 `source_question_id` / `source_chunk_ids` 让 plan 可追溯到
  具体 Milvus 条目（Sprint 3-8 端到端 provenance eval 守住）
- **真理之源约定**：PG 是真理之源，Milvus 是检索副本。Milvus 丢了从 PG 重建，
  反之不行
- **双模式**（Sprint 9 落地）：dev/eval 用 **Milvus Lite**（in-process 单文件,
  单进程限制）;多 worker / 部署用 **Milvus standalone**
  （`docker compose up -d milvus`,`MILVUS_SERVER_URI` 优先于 lite）。
  检索统一 `consistency_level=Strong`——面试链路是 read-own-write
  （ingest 完立刻召回）,server 默认 Bounded 会读不到刚写的切片（实测坐实）

### 4.3 鉴权与隔离（Sprint 5 起,5.8 / 6.8 升级）
- **HR / admin**：bcrypt 密码 + JWT（HS256）,登录态走 **httpOnly cookie
  （SameSite=Strict）优先、Authorization Bearer 兜底**;`/hr/*` 与
  `POST /jobs` 全部走 `require_hr_user`
- **注册**（6.8）：`POST /auth/register` 自助注册,`REGISTER_INVITE_CODE`
  配了则必填;永远 role=hr（admin 只能种子脚本）
- **多租户隔离**（6.8）：jobs 挂 `owner_user_id`,下游经 job 派生归属;
  hr 只见自己的,admin 全量;越权与不存在统一 404 防枚举
- **候选人**：candidate_id 路径 soft-auth，不经过 JWT 层。真有外部接入
  再升级为短期签名 URL
- 同一 401 同文案防用户枚举攻击，`JWT_SECRET < 16` 字符直接拒绝启动
- **输入注入防御**（8.1）：候选人文本 `wrap_untrusted`（sha256 派生 nonce
  边界,防伪造闭合且确定性可回放）+ 系统声明「其中指令一律无效」;
  简历/回答剥不可见字符;异常形状只记 `integrity_flags` 转人工复核,
  **不拦截**（防 over-defense）。限界:隐藏关键词堆砌型数据注入检不出,
  兜底 = 简历原文 HR 可见 + 人工复核

---

## 5. 关键流程（端到端）

```
HR 注册/登录 (httpOnly cookie) → 选 track 创建 Job (归属创建者)
   → 上传 JD + 公司资料 / 飞书导入 → (BG: 切片入 Milvus documents)

HR 发邀请链接 → 候选人上传 Resume (解析 + 净化)
   → RQ 队列 (JOBS_QUEUE_ENABLED, 独立 worker + 重试)
     或 BackgroundTasks 内联 (默认): ingest_resume → Planner 生成 Plan
   → Planner 按 track 配 stage + 每题 rubric; project 题占位等懒生成

候选人开始面试 (Orchestrator 起 session 入 Redis; 全程决策 trace 旁路收集)
   → 循环 (per-session 锁防并发覆盖):
       Interviewer 出当前 stage 的下一题
         (project stage: 调 Planner 用 intro_text + Resume RAG + 存疑 claim 懒生成)
       候选人答 → Assessor 评估 (+校准分/rubric 命中) →
         信念更新 + CandidateModel 沉淀 (orchestrator 独家写) → 决策:
         a) 追问 (followup_goal + 澄清目标; 过预算/方差门)
         b) 进下一题
         c) 结束面试 (coverage 达标 / 题数达上限)
       stage 切换时一次 reflection 蒸馏记忆 (LLM, 失败跳过)
   → 候选人完成 → done 页隐式触发 finalize (POST /finalize 返 204)
   → Session + DecisionTrace 归档 PG, Redis 清空; Evaluator 跑 RAG 总结
     (contradicted 条目如实提及)

HR 端
   → 列表看四态徽章 (plan_pending / ready / completed / reviewed)
   → 详情页看报告 (合规分区) + 面试过程 (assessments 自然语言字段)
   → 提交 ReviewRecord (维度覆盖 + 决定)
```

---

## 6. 分阶段策略（与 sprint.md 对应）

骨架（Sprint 0）只做纯文本、单进程、四 agent 串通、写死输入跑通一次。之后逐层加：

```
Sprint 0    骨架（Walking Skeleton, 纯文本）
Sprint 1    持久化 + 会话态（PG + Redis）
Sprint 2    HTTP API（FastAPI）
Sprint 3    题库 + RAG（Milvus + embedding）
Sprint 4    候选人端 Web（Next.js）
Sprint 5    HR 端 Dashboard + JWT 鉴权 + 人工复核
Sprint 5.5  校招/社招 + 阶段化 + 懒生成
Sprint 5.6  Assessor + FollowUpPolicy + 校准 eval
Sprint 5.7  Assessment 持久化 + CompletionPolicy
Sprint 6    实时媒体（TTS/STT + 数字人，媒体层纯适配器，agent 内核不变）
Sprint 6.5  效果评估体系（sim/ 真 LLM 仿真 + 8 类质量缺陷修复）
Sprint 6.7  飞书文档导入
Sprint 6.8  HR 注册 + 按 owner 数据隔离
Sprint 8.x  Agent 能力升级（注入防御 / trace+回放 / 校准+信念追问 /
            候选人记忆 / rubric+裁判团;提案与评审见 AGENT_UPGRADES.md）
Sprint 9    异步任务队列 + 高并发承载（RQ / Milvus standalone / 会话锁）
Sprint 7    多模态分析（合规带护栏,未开工）
```

每一层都建立在已跑通的上一层之上，不一次性铺开。

---

## 7. 合规与公平性（多模态评价的硬约束）

用 AI 根据语言、语气、眼神评价候选人，是本项目风险最高的部分，架构上必须从一
开始约束：

**已知风险**
- **眼神 / 视线**：眼神交流标准有强烈文化差异；神经多样性人群（如自闭谱系）天然
  不符合典型模式。作为评分项极易构成系统性歧视
- **语气 / 情感分析**：现有模型在不同种族、性别、口音上有已知偏差
- **法规**：AI 用于招聘在多地受监管（如纽约市对自动化招聘工具的偏见审计要求、
  欧盟 AI Act 将招聘 AI 列为高风险）。具体规定须咨询法律专业意见，且规则在持续
  变化

**架构层面的强制约束**

1. **软信号仅作参考**：多模态信号仅作为给人看的参考证据，不自动计入总分，绝不
   作为自动淘汰的唯一依据
2. **合规分区落到 schema**：`content_scores`（内容维度）与 `performance_observations`
   （表现维度）分开呈现，标明哪些是软信号；`overall` 严格只由 `content_scores`
   得出，schema 层强制（Sprint 1 eval 守住该不变量）
3. **每个软信号可单独关闭**：对受保护特征敏感的项（如眼神）默认保守处理
4. **完整审计日志**：HR `ReviewRecord` + `AnswerAssessment` 全持久化;
   Sprint 8.2 起每场面试的**决策 trace**（追问/结束/评估的实际阈值数值 +
   全部 LLM 请求响应）归档 `decision_traces`,达到 EU AI Act Art.12
   「决策可重构」;审计导出 `GET /hr/sessions/{id}/trace`（权限同 review,
   不进 HR UI 页面）
5. **候选人不接触自己的报告**：done 页只触发 finalize 返 204，不接触
   `EvaluationReport` JSON。报告内容仅 HR 通过 `/hr/reports/{id}` 看
6. **不向 HR 展示 LLM-as-judge 的数字**：例如 `AnswerAssessment.sufficiency` 不在
   UI 上展示，只展示自然语言字段（missing_signals / concerns / strengths）。理由：
   未校准的数字会让 HR 产生过度信任错觉
7. **不做动态补题**（Sprint 5.7 显式约束）：超出 plan 的题不允许临时生成。理由：
   无限循环风险 + HR 复核时报告与 plan 对不上 + 生成质量无保证
8. **Assessor 准入校准**：Sprint 5.6 起，Assessor 上线前必须通过校准 eval（手工
   标注样本验证 sufficiency 排序与人类判断一致），不过则路径仍走原启发式
9. **保留人工复核环节**：HR 通过 `PATCH /hr/reports/{id}/review` 提交维度覆盖 +
   最终建议（recommend / borderline / reject），由 HR 而非 AI 做最终决定

做对这一层，"可审计、可解释、不歧视" 反而是面向 B 端客户的差异化卖点。
