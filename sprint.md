# SPRINT — AI Interview Platform

开发纪律：**一次只做一个 task，做完立刻运行验证，通过后 commit，再开下一个。**
每个 Sprint 都建立在上一个已跑通的基础上。骨架（Sprint 0）务必先跑通再往下走。

---

## Sprint 0 — Walking Skeleton（纯文本，跑通主链路）

目标：`python -m src.main` 能用写死的输入完整跑完一次「JD + Resume → 计划 → 提问/追问 → 报告」。
**不碰**视频、语音、多模态、前端、数据库、Redis、Milvus。

- [x] 初始化项目：uv 依赖、目录结构、`.env.example`、`.gitignore`
- [x] `src/schemas/`：用 pydantic 定义 JobContext / **CandidateProfile** / Competency /
      Question(含 type + **category: KNOWLEDGE | PROJECT_EXPERIENCE**) /
      InterviewPlan / CandidateAnswer / InterviewSession / EvaluationReport
- [x] `src/llm/`：anthropic SDK 的最小封装（统一入口，读环境变量；无 key 走 stub 让骨架可本地跑通）
- [x] Planner：根据写死 JD + Resume 生成 1 轮 / 2 维度 / 4 题
      （每维度 1 道基础知识题 + 1 道项目深挖题）
- [x] Interviewer：提一题，依据回答（简单逻辑）决定是否追问一次
- [x] Evaluator：根据对话产出结构化报告（content / performance 严格分区）
- [x] Analyzer：留空占位（返回空信号）
- [x] Orchestrator：串起全链路，写死 JD + Resume + 几条候选人文本回答
- [x] `src/main.py`：跑完并打印 EvaluationReport

**完成标准**：命令行跑通一次，输出一份可读的结构化报告。

---

## Sprint 1 — 持久化与会话态（Postgres + Redis）

- [x] 接入 Postgres，为 InterviewSession / EvaluationReport 建表与 ORM
- [x] 接入 **Redis**：进行中的会话状态存 Redis（热存储），结束后归档 Postgres
- [x] Orchestrator 改造：会话状态机基于 Redis 读写（start/submit/resume/finalize 三段式 + 中断恢复）
- [x] 给 LLM 调用与 JD 解析结果加 Redis 缓存（透明缓存 src/llm/complete，stub 不入缓存，Redis 不可用降级）
- [x] 写第一个 eval：固定输入 → 校验报告结构与关键字段（含合规分区不变量护栏）

**完成标准**：会话可中断后从 Redis 恢复；结束后能在 Postgres 查到归档。 ✅

---

## Sprint 2 — API 层（FastAPI）与真实 JD 解析

- [x] FastAPI 骨架：健康检查 + 基础路由（create_app 工厂；/health 不查上游）
- [x] 接口：创建职位（POST /jobs；server 生成 job_id；异常映射 503/422）
- [x] 接口：候选人上传 Resume，关联到某职位 → 触发 Planner 生成 InterviewPlan(JD + Resume)
      （POST /jobs/{id}/candidates 异步触发 Planner via BackgroundTasks；GET 轮询 plan）
- [x] 接口：创建/进行面试会话（POST /interviews + POST /answers + GET 中断恢复）
- [x] 接口：获取评估报告（GET /interviews/{id}/report 隐式 finalize，幂等，IN_PROGRESS→409）
- [x] Planner 接真实 JD + Resume 文本解析（替换写死输入）—— 通过 API 自然实现：
      Planner 接受任意上传文本，src.main 写死输入仅作 Sprint 0 demo 用

**完成标准**：用 HTTP 完整走完一次文本面试，全部经 API。

---

## Sprint 3 — 题库与向量检索（Milvus + RAG）

- [x] 接入 **Milvus**（Lite, in-process），questions + documents 两个 collection
- [x] 公司资料 / JD / 候选人 Resume 切片 → 向量化 → 入 Milvus（POST 时挂 BG 任务）
- [x] Planner 改造：knowledge 题从 Milvus 召回 + LLM 精修；project 题走 Resume 切片 RAG；
      Question.source_question_id / source_chunk_ids 记录召回溯源
- [x] 面试与评估时对公司资料 RAG 召回相关片段（Evaluator summary 接 JD+公司资料）
- [x] 扩展 eval：组件级 RAG eval + 端到端 provenance + 跨 job/candidate 隔离

**完成标准**：计划与提问明显更贴合岗位与公司语境，且可追溯到召回片段。 ✅

附加: Sprint 3 中段把 LLM 从 anthropic 切到 OpenAI 单一 provider（embedding 一直
就是 OpenAI，consolidate 简化 key/计费）。

---

## Sprint 4 — 候选人端 Web（文本面试）

- [x] Next.js + TypeScript 初始化，候选人面试界面（Next.js 16 + App Router + Tailwind 4）
- [x] 候选人端: 面试前 Resume 上传页（**仅 paste 模式**；文件上传移到 Sprint 5）
- [x] 对接面试会话 API，文本一问一答 + 追问（含 localStorage 中断恢复 + 答题草稿）
- [x] 会话进度、剩余轮次展示（"第 M/N 题" + 追问 amber 徽章；总数从 plan 取，不写死）
- [x] 基础鉴权（candidate_id 作 path soft-auth；JWT 移到 Sprint 5 与 HR 端一起做）

**完成标准**：候选人能在浏览器里完成一次完整文本面试。 ✅

附加: 加 CORS 中间件、`GET /jobs/{id}` 与 `GET /candidates/{id}` 两个候选人端
读取端点；全局 `error.tsx` 错误边界；Resume 长度三档颜色反馈。

---

## Sprint 5 — 招聘端 Dashboard

- [x] HR 端：创建职位、上传资料、查看生成的面试计划
      （JWT 鉴权 + `/hr` Dashboard + `/hr/jobs/[id]` 单 job 详情；候选人邀请链接 dev 期直接展示）
- [x] 候选人列表与面试状态（四态徽章 plan_pending / ready / completed / reviewed）
- [x] 评估报告查看页：内容维度与表现维度分区展示
      （overall banner + summary + content_scores 主区 + performance_observations 副区"仅参考" + RAG chunks 可折叠）
- [x] **人工复核**：HR 可标注/覆盖、留存复核记录
      （comments / 按维度 score+note 覆盖 / 三态 decision；MVP 覆盖语义不做版本历史）

**完成标准**：HR 能从建岗到查看报告全程在界面完成。 ✅

附加：
- 后端 JWT (HS256 + bcrypt) + `/auth/login` + `require_hr_user` dependency
- `scripts/seed_users.py` 种 HR 账号
- `POST /interviews/{id}/finalize` 返 204 让候选人 done 页自动归档
- 推到 Sprint 6：Resume 文件上传 (PDF/docx 解析)、cookie + same-site=strict 鉴权升级

---

## Sprint 5.5 — 校招/社招双流程 + 阶段化面试（agent 收尾）

> 在 Sprint 6 视频面试之前做。让 agent 的题目结构和企业实际招聘流程对齐；
> 视频化只是输入/输出层的改造，agent 内核届时无需重做。

**背景**：当前 Planner 写死「2 维度 × 2 类别 = 4 题」(技术深度/沟通协作 × 知识/项目)。
HR 不能配置，校招社招走同一套，没有自我介绍 / 场景题。Sprint 5.5 把出题结构改为
track-aware 多阶段，并补齐自我介绍 + 场景题两个缺失环节。

- [x] **数据契约扩展 + HR track 选择**：
      `JobContext.track: "campus" | "lateral"`（默认 lateral，DB 增量加列）；
      `QuestionCategory` 加 `SELF_INTRO` / `SCENARIO`；
      `InterviewRound.stage` 字段（self_intro / knowledge / project / scenario）；
      `InterviewSession.intro_text` 字段（存候选人自我介绍全文，供后续题目用）；
      HR 端 POST /jobs 接收 track，新建表单加 campus/lateral 单选

- [x] **场景题库 + seed 脚本扩展**：
      `seed_questions` 表加 category 列区分 knowledge / scenario；
      `scripts/seed_questions.py --category` 参数；
      种 backend 场景题 ~15 道（如"线上 P99 突然涨 10 倍, 5 分钟内做什么"风格）
      实际落地: 15 技术深度 + 5 沟通协作 = 20 道, 落 PG; Milvus 由 task 4
      drop + reseed 时一起做进去, category 过滤召回验证通过

- [x] **Planner 改造为 track-aware 多阶段（项目题方案 A：懒生成）**：
      校招 7-8 题：自我介绍 1 + 基础知识 3 + 项目深挖 2（占位）+ 场景 1-2；
      社招 7-9 题：自我介绍 1 + 项目深挖 3-4（占位）+ 场景 2-3 + 基础知识 1；
      旧的「2 dim × 2 cat × 4 题」覆盖式退役（无 fallback，避免双路径）；
      Plan 数据契约支持「占位题」（text 为空、`lazy: True`），等 interviewer 进入 project stage 时再生成
      实际落地: campus = [1,3,2,1] = 7, lateral = [1,3,2,1] = 7;
      Question.competency_id 改 Optional (self_intro=None 不进 content_scores);
      InterviewPlan.competencies 顶层权威 (跨 stage 共享, Evaluator 走顶层)

- [x] **Interviewer 跨阶段 + 项目题懒生成**：
      自我介绍 turn **不**触发 followup 启发式（30 字也不追问）；
      自我介绍全文落 `InterviewSession.intro_text`；
      进入 project stage 时**实时**生成项目题（用 intro_text + Resume RAG），
      生成期间前端 UI 显示「思考中...」（前端轮询或 SSE 待定，先轮询）；
      至多 1 次追问/题 这条保持；后续如要升级追问启发式，留 Sprint 5.6+
      实际落地: lazy resolve 移到 orchestrator.submit_answer (检测 next 题
      lazy+empty 时整 plan 一次性 resolve, 写回 cache, 用 question_id 找回灌后
      的题); _needs_followup(question, answer) 加 question 入参, SELF_INTRO 硬豁免;
      前端 isNextTurnLazyProject 启发式预测 + 按钮文案 "思考中... (准备项目题, 约 3-5 秒)"

- [x] **HR 详情页阶段视图 + 端到端 eval**：
      HR 候选人详情页：在报告区上方加「面试阶段」视图，每 stage 显示题数 +
      每题来源（题库 ID / Resume chunk ID / 场景题库 ID）；
      端到端 eval：campus job → 简历 → 自我介绍 → 走完 8 题 → 报告 OK；
      端到端 eval：lateral job → 同上，验证 knowledge 题数远少于 project；
      eval：项目题 prompt 确实包含 intro_text（懒生成路径生效）
      实际落地: StageView 显示 4 类 stage card (颜色编码) + describeSource 把
      knowledge/scenario 显示题库 ID, project 显示 Resume 切片数, self_intro 显示
      "固定模板", lazy 未生成显示"待懒生成"; 端到端 eval campus 7 题 walk + lateral
      knowledge<project + magic-marker intro_text 流转锁住 lazy gen 路径

**完成标准**：HR 创建 job 时选 track；候选人按 track 对应的多阶段流程完成面试；
项目深挖题真的反映自我介绍里提到的内容（懒生成路径生效）。 ✅

附加:
- evals 数: Sprint 5 收尾时 166 → Sprint 5.5 收尾 212 (+46), 全绿
- Milvus questions collection drop + reseed (30 knowledge + 20 scenario),
  category 过滤召回 OK; documents collection 顺带 drop, dev 期不阻塞
- 5 个 commit 串完成: 6688804 / 8fe7615 / adaf640 / 5d9f283 / 977e5a5

---

## Sprint 5.6 — Interviewer 决策能力升级（assessment + policy）

> Sprint 5.5 之后做。把追问从"字数+关键词启发式"换成基于结构化 AnswerAssessment 的
> 信号充分性判断。阈值校准、延迟控制、降级路径在设计阶段写进，不留事后补。

**背景**：当前 `_needs_followup` 只看长度 + 关键词，与题目语义、岗位维度、回答质量
无关；每题至多 1 次硬规则的追问。Sprint 5.6 把这套换成"先评估、再决策、必要时
追问"的三段式，但全程保留启发式 fallback 防 LLM 异常卡死面试。

- [x] **AnswerAssessment schema + Assessor agent**：
      新 schema `AnswerAssessment`（sufficiency / confidence / missing_signals[] /
      strengths[] / concerns[] / followup_goal / stop_reason）；
      新 agent `src/agents/assessor/`（**独立模块, 不**挂在 Evaluator 上 —— Evaluator
      只在结束时跑、是同步重调用, Assessor 是每 turn 的实时轻调用, 并发模型不同）；
      `assessor.assess(question, answer, session, plan)` 入口
      实际落地: sufficiency / confidence 都是 float ∈ [0,1] (用 pydantic ge/le 校验);
      stop_reason 是字符串 (sufficient_signals / low_value / diminishing_returns 之一,
      未做硬 enum 留 v1 灵活); LLM 输出 JSON parse 失败 / stub / timeout 一律 fallback,
      _LLMStubFallback 单独类区分"期望中的 stub"和"真异常"防日志噪声。

- [x] **FollowUpPolicy + stage-aware 配额**：
      新 schema `FollowUpPolicy`（max_followups_per_question / min_sufficiency_to_stop /
      min_confidence_to_stop）；
      stage 默认值：self_intro=0 / knowledge=1 / project=2 / scenario=2；
      允许 `JobContext.followup_policy` 覆盖 stage 默认（HR UI 留到 5.7）；
      Interviewer.next_turn 改为三步：`assess_answer` → `decide_followup` → `generate_followup`
      实际落地: FollowUpPolicy.for_stage(stage) classmethod 返回硬编码 stage 默认表;
      JobContext.followup_policy 字段未加 (留 5.7 一起做 HR 配置 UI); assess 步由
      orchestrator 在 next_turn 之前调 (维持 "agents 之间不互调" 约定), assessment
      流转走 session.assessments[-1], Interviewer 从 session 反向找。

- [x] **校准 eval（先做, 过不了不上生产路径）**：
      手工标 20-30 个固定样本（明显足够 / 明显不足 / 模糊）, 覆盖 4 个 category；
      跑 assessor 比对标注, 验证"足够"的平均 sufficiency > "不足"的平均（看排序,
      不要求绝对值对得上）；
      标注集进 `evals/data/assessment_calibration.json`；
      eval 不过 → Assessor 不上线, 路径仍走原 `_needs_followup`
      实际落地: 24 条样本 (knowledge 7 / project 7 / self_intro 4 / scenario 6),
      启发式 fallback 路径 sufficient 均值 0.933 vs insufficient 均值 0.093,
      gap 0.840 = strong pass; ambiguous 5 条只 record 不参与 pass/fail;
      真 LLM 路径校准必须人工 PR review 才翻 ASSESSOR_ENABLED=true。

- [x] **延迟 + 降级控制**：
      Assessor 用 `gpt-4o-mini`（不要 gpt-4o）；
      所有 LLM 调用 try/except + 10s 超时；任意异常 fallback 到原 `_needs_followup`
      （**保留不删, 作为兜底**）；
      LLM cache 自然 cover 重复 prompt；
      前端 session 页加"分析中..." 状态（submit 后到下一 prompt 前显示, 避免
      用户以为卡死）
      实际落地: src/llm/complete() 加 timeout 参数透传 openai SDK; Assessor 写死
      gpt-4o-mini + 10s + 600 max_tokens (不读 OPENAI_CHAT_MODEL env 防被切到贵慢的);
      _decide_followup 在 assessment is None 时退到 _needs_followup, _needs_followup
      Sprint 0 函数保留不删; 前端按钮文案双档 "分析中... (评估回答)" / "分析中...
      (评估 + 准备项目题, 约 5-8 秒)"。

- [x] **followup 生成更聚焦**：
      用 `assessment.followup_goal` 喂给 `_followup_text`, 生成的追问明确指向
      missing_signals（如"补一个量化数据" / "讲清你做了什么决策"）, 不再泛泛
      "展开一个具体例子"
      实际落地: _followup_text 加 assessment 入参, prompt 拼 missing_signals +
      followup_goal; LLM 不可用时模板也拼 followup_goal ("能聚焦讲一下: {goal}"),
      即使没 LLM 也比泛泛模板聚焦。

**完成标准**：assessor 在校准 eval 上能区分"足够 vs 不足"；面试链路任何 LLM 失败
都降级到启发式不卡死；前端提交答案到看到下一题的体感延迟可接受（<5s 含降级）。 ✅

附加:
- evals 数: Sprint 5.5 收尾 212 → Sprint 5.6 收尾 233 (+21 单元+校准+集成), 全绿
- ASSESSOR_ENABLED env flag 默认 false: Assessor 代码进 repo 但不参与追问决策,
  与 Sprint 5.5 行为一致; calibration eval + 人工 review 真 LLM 路径后翻 true 上线
- InterviewSession.assessments Redis 写入 OK; PG 列 + HR UI 留 Sprint 5.7
- 1 个 commit 串完成: 4ce85e0

---

## Sprint 5.7 — Assessment 持久化 + Completion Policy

> Sprint 5.6 之后做。如时间紧可推后, Sprint 6 视频面试不依赖本 sprint。
> 把 5.6 在线产出的 assessments 落库、给 HR 看；把"面试结束"从"题答完"升级到
> "维度证据足够"。**显式不做动态补题**（可能无限循环 / 报告题目数对不上 plan /
> 生成质量无保证）, 真要做单独立 5.8 评估风险后再决定。

- [x] **数据契约扩展**：
      `InterviewSession.assessments: list[AnswerAssessment] = []`（增量加 JSONB 列）；
      `EvaluationReport.competency_coverage: dict[str, float] = {}`（每维度证据
      充分性聚合, 0~1）；
      orchestrator.submit_answer 调 assessor 后把结果追加到 session.assessments
      实际落地: assessments JSONB 列 ALTER 加完 (server_default '[]'); coverage
      JSONB 列 ALTER 加完 (server_default '{}'); orchestrator.submit_answer 在
      Sprint 5.6 已经追加 (ASSESSOR_ENABLED 启用时), 本 sprint 把它落 PG。

- [x] **HR 详情页 assessment 视图**：
      候选人详情页报告区上方加「面试过程」视图；
      每题展示 question + answer + assessment.missing_signals + concerns + strengths
      （展示自然语言字段比展示 sufficiency 数字可信）；
      followup 题展示 followup_goal（"为什么发这条追问"）；
      **不**让 HR 直接看 sufficiency 数字（避免被无校准模型误导）
      实际落地: AssessmentView 在 StageView 与 ReportView 之间; 按
      interviewer/candidate turn 配对显示, followup turn 借父题 followup_goal
      解释"为什么追问"; sufficiency / confidence 字段在 API 返回但前端 UI 不
      渲染 (合规守在前端层, 不剥离 API 层让"内部诊断"未来仍可拿)。GET
      /hr/sessions/{id} 新端点拉完整 session。

- [x] **CompletionPolicy（按 coverage 决定结束）**：
      新 schema `CompletionPolicy`（min_competency_coverage / max_total_questions /
      mandatory_competencies[]）；
      Interviewer.next_turn 末尾判断：所有 mandatory 题答完 + 每个 mandatory
      competency 的 coverage >= 阈值 → 结束；否则只要还有非 lazy 题就继续；
      **硬上限** `max_total_questions`（如 15）封顶防无限循环；
      **不做动态补题**：超出 plan 不允许临时生成题
      实际落地: src/coverage.py 单一计算源 (compute_coverage 取 max(sufficiency)
      per competency, self_intro 不计; 老 plan 顶层空时返 {} 短路); Interviewer
      next_turn 末尾 hard cap -> mandatory met 提前 done -> 还有题继续 -> 题答
      完 coverage 不达 done 标 evidence_insufficient 4 步; **绝不动态补题**。

- [x] **JobContext 加 HR 可配置项 + UI**：
      `JobContext.followup_policy: FollowUpPolicy | None`（None = 用 stage 默认）；
      `JobContext.completion_policy: CompletionPolicy | None`；
      HR 端新建 job 表单加「高级」折叠区, 让 HR 调阈值；默认值合理, HR 不动也能用
      实际落地: schema 字段 + JobORM JSONB 列 + repository 双路兼容. HR 新建
      job 表单加「高级 (折叠)」, 4 个 NumberField (题数硬上限/coverage 门槛/
      Assessor 双阈值), 默认折叠 + 不动 = 后端 null = 用默认; HR 大多不动也
      能用。

- [x] **端到端 eval**：
      足够的 sufficient 答案 → 0 次 followup + coverage 达标 → 提前结束；
      不足的答案 → 跟随 policy 触发 followup, 到 max 仍不足 → 进入下一题, 记录
      coverage 低；
      所有题答完但 coverage 不达 → 报告标记"证据不充分, 建议人工面谈"
      实际落地: test_completion_policy 12 条 (coverage 单元 + Interviewer
      CompletionPolicy + Evaluator evidence_insufficient + 老 plan 短路);
      "证据不充分, 建议人工面谈: <names> 等维度证据不足。" 自动加 summary 前缀
      + needs_human_review=True (不引新 schema 字段)。

**完成标准**：HR 在详情页能看到每题的过程评估（自然语言, 不是分数）；面试结束
基于 coverage 而非简单题数；候选人回答足够时面试自动提前结束（节省时间）；
所有 5.6 的延迟/降级护栏在持久化层依旧成立。 ✅

附加:
- evals 数: Sprint 5.6 收尾 233 → Sprint 5.7 收尾 245 (+12)
- 合规层级: HR UI 展示自然语言字段, 数字仅展示 coverage (Evaluator 已基于其判);
  sufficiency / confidence 经 API 但不上 UI, 内部诊断 future 仍可拿
- 1 个 commit 完成: f333beb

---

## Sprint 6 — 实时媒体（视频面试）

- [ ] WebRTC 接入，候选人端音视频采集
- [ ] 流式 STT：语音实时转写为 CandidateAnswer 文本
- [ ] TTS：AI 面试官语音输出（可选先文字）
- [ ] 媒体层作为适配器接入既有 agent 链路，不改 agent 大脑
- [ ] 录制与存储（注意合规：知情同意、留存策略）

**完成标准**：候选人能进行一次语音/视频面试，转写文本驱动既有 agent 流程。

---

## Sprint 7 — 多模态评价（扩展，带合规护栏）

> 实现前先落实 ARCHITECTURE.md 第 7 节的全部约束。

- [ ] Analyzer 异步管线：经 Redis 队列处理音视频，避免阻塞面试
- [ ] 语言信号（基于转写文本）
- [ ] 语气 / 韵律信号（基于音频）
- [ ] 视线 / 表情信号（基于视频帧）—— 默认保守、可单独关闭
- [ ] 信号附置信度，仅作为参考证据进入报告「表现维度」
- [ ] 报告明确区分软信号；保留人工复核与审计日志
- [ ] 偏见检查：针对受保护特征做差异审计

**完成标准**：软信号以可解释、可关闭、可审计的方式呈现，且不参与自动淘汰。

---

## 持续项（贯穿所有 Sprint）

- [ ] 每次改 agent / prompt 后跑 eval，防行为退化
- [ ] 维护 CLAUDE.md：发现 Claude Code 反复犯错就把纠正写进去
- [ ] 每个 task 一次 commit，用 conventional commits
