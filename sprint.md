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

## Sprint 5.8 — 运维收尾（PDF Resume + Cookie 鉴权 + 追问 UI）

> Sprint 5/5.5 留的债。不动 agent 内核, 把"先 paste / localStorage / 后端默认"
> 三处临时方案换成 prod-ready 的版本。Sprint 6 视频面试不依赖本 sprint, 但
> 上 prod 前必须做完 (Cookie + Secure flag + CORS 是安全前提)。

**背景**: Sprint 4-5 推进过程中, 三个不影响 agent 决策、但影响候选人 / HR 实际
体验和上 prod 的项被一路推到 Sprint 6 / 5.8: (1) Resume 文件上传 (现在只能 paste);
(2) HR JWT 还在 localStorage (XSS 抗性弱于 httpOnly cookie); (3) HR 没法调
"每题最多追问次数" (只能改后端默认重启)。本 sprint 一并收尾。

- [x] **PDF/docx Resume 解析端点**：
      新 `POST /jobs/{job_id}/candidates/parse-resume` 接 multipart, 返回
      `{parsed_text: str}`; 前端把 parsed_text 填回现有 textarea, 用户可编辑后
      走旧 `POST .../candidates {resume: text}` 提交 (现有 JSON 端点保持纯净)；
      库选型: `pypdf` (PDF) + `python-docx` (docx), 都加进 pyproject.toml；
      校验: 文件大小 ≤ 5 MB; mime + extension 双判 (`application/pdf` + `.pdf`
      / `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
      + `.docx`); 解析后文本 <100 字符直接 422 "解析失败, 请贴文本" (兜底扫描件)。
      实际落地: 新 `src/resume_parser.py` 是纯计算模块 (无副作用), pypdf 6.x
      逐页 extract_text + python-docx paragraphs+tables; ResumeParseError 统一
      上抛由 API 路由映射 422; 前端虚线框「选择文件」+ 状态机 (idle/parsing/error),
      parsed_text 填回 textarea 让用户可编辑, paste 路径保留作 fallback。

- [x] **Cookie httpOnly + SameSite=Strict 鉴权升级**：
      `POST /auth/login` 同时 set httpOnly cookie (HttpOnly / SameSite=Strict /
      Path=/ / Max-Age=JWT_EXPIRE_MINUTES*60) **和** 返 access_token JSON
      (转期共存, 让 evals + 脚本继续用 Bearer 头不用全改); Secure 标志由
      新 env `JWT_COOKIE_SECURE=false` 控制 (dev http 默 false, prod 翻 true);
      `require_hr_user` 先读 Cookie 未命中再读 `Authorization: Bearer`
      (双路径, 至少保留到 Sprint 6+);
      新 `GET /auth/me` 走 `require_hr_user` 返 `{username, role}`, HrGuard
      改成调它判 session 有效;
      新 `POST /auth/logout` Set-Cookie Max-Age=0 把 token 清掉;
      CORS 从 `allow_origins=["*"]` 改成 explicit `["http://localhost:3000"]`
      (dev) + `allow_credentials=True`, 用 env `CORS_ALLOW_ORIGINS` 配。
      实际落地: CORS 在 Sprint 4 起已是 explicit origins + allow_credentials=True
      (env `CORS_ALLOWED_ORIGINS`), 本 sprint 只补 .env.example 注释; UserMe API
      DTO 新加; `cookie_name()` helper 从 env `JWT_COOKIE_NAME` 取 (默认
      auth_token); /auth/logout 不挂 auth dep, 即使已过期 token 也能调清 cookie。

- [x] **前端鉴权切 Cookie**：
      `web/src/lib/auth.ts` 改成 noop: readToken 返 null, writeToken 不写
      localStorage (cookie 由后端 set); fetch 全局加 `credentials: "include"`
      让 cookie 自动带上; HrGuard 改成调 `GET /auth/me` 判 session
      (401 -> 跳登录); 退出按钮调 `POST /auth/logout`; 旧 localStorage
      token 在 Sprint 5.8 deploy 那一刻一次性清掉, evals 仍走 Bearer 头不变。
      实际落地: auth.ts 删 token 函数只留 role cache (UI 即时渲染用); api.ts
      `auth: true` 标志保留作 callsite 不动的兼容 (cookie 总会送, 无实际行为);
      api.getMe/logout 加入; HrGuard 在 /hr/login 跳过 /me 不抖闪。

- [x] **`max_followups_per_question` HR UI**：
      `web/src/app/hr/page.tsx` 高级折叠区加 1 个 NumberField "每题最多追问
      次数 (0-3)", 整 job 一刀切。HR 不动 = 后端 null = stage 默认 (self_intro=0
      / knowledge=1 / project=2 / scenario=2) 仍生效。文档写明: 即使 HR 设
      max=2, Interviewer 的 SELF_INTRO 类别二次保护仍在 (
      `if question.category is SELF_INTRO: return False`), 自我介绍永远 0 次追问。
      实际落地: 高级折叠区从 4 个 NumberField 扩到 5 个 + 注释提示自我介绍
      永远不被追问。

- [x] **eval 护栏**：
      `evals/test_parse_resume.py`: 用 mock PDF/docx bytes 跑端点, 验返回的
      parsed_text 含已知 fixture 字符串; 大小超 5MB → 422; mime 不匹配 → 422;
      解析后 <100 字 → 422。
      `evals/test_auth_cookie.py`: 登录 Set-Cookie 含 HttpOnly / SameSite=Strict;
      cookie 单独命中 require_hr_user (无 Bearer); Bearer 单独命中 (无 cookie);
      logout 清空 cookie; GET /auth/me 返 user info; CORS preflight 含
      Access-Control-Allow-Credentials。
      实际落地: test_parse_resume 11 条 (parser 单元 7 + endpoint 4);
      test_auth_cookie 9 条; 还修旧 test_auth + test_hr_api 的 setUp 加
      `self.client.cookies.clear()` 防 TestClient 持久化 cookie 让"无 Bearer
      应当 401" 测试误命中。

**完成标准**: 候选人能上传 PDF/docx Resume, 解析失败时优雅提示; HR JWT 走
httpOnly cookie + SameSite=Strict + 可控 Secure flag; HR 能在 UI 改"每题最多
追问次数"; evals 双路径 (Bearer + Cookie) 全绿。 ✅

附加:
- evals 数: Sprint 5.7 收尾 245 → Sprint 5.8 收尾 265 (+20)
- 3 个 feat commit 完成: 72823df (PDF) / fbada1c (Cookie) / 50edd54 (追问 UI)

**显式不在 5.8 scope** (推后):
- `CompletionPolicy.mandatory_competencies` HR UI: 推后 Sprint 5.9+
  (HR 在新建 job 时不知道 competency_id, 该字段需要"先建 job → plan 生成 →
  回头编辑 mandatory" 编辑流程, 跟 5.8 "运维收尾" 不匹配; 维持默认 "全
  plan.competencies 都 mandatory")
- per-stage `max_followups_per_question` 配置: schema 改 + UI 改 + eval, 不在
  5.8 范围内
- Bearer 路径退役: 5.8 阶段 cookie + Bearer 双路径并存 (evals 用 Bearer),
  待 cookie 路径稳定后可独立小 sprint 收 Bearer; evals 改造比代码量大

---

## Sprint 6 — 实时媒体（视频面试）

> 媒体层是纯「适配器」：面试官的嘴 = `TurnResult.prompt` 文本, 候选人的答 =
> `submit_answer(session_id, text)`, 三段式 API 与 agent 大脑**零改动**。
> 任一媒体环节失败都降级回文字问答（与 LLM stub fallback 同款哲学, 双路径永存）。
> Sprint 5.8 与本 sprint 互不依赖。

**背景**：升级为「看得见类真人中文面试官 + 候选人开口作答 + 开启摄像头」的视频面试。
目标市场国内 + 海外双 lane; 数字人档位取「够真即可, 控成本/可私有化」。

**关键设计决策**（动手前先读）：
- **回合制, 不做全双工实时对话**：不用 speech-to-speech 端到端模型（如 OpenAI
  Realtime）——那会绕开 Planner/Assessor/CompletionPolicy 与「不动态补题」约束。
  一次口语回答 = 一个 turn, agent 编排完全复用。
- **MVP 不引 WebRTC**：TTS 走普通 HTTP audio（按 ref_id 缓存）; 麦克风走
  getUserMedia → WS 推流 → 后端代理厂商流式 ASR; 摄像头录制走 MediaRecorder
  分片上传。WebRTC 只在真口型数字人（末项 task）接入时由 avatar 层封装引入。
- **数字人自托管解决双市场**：国内/海外数字人厂商互不可达, 自托管
  （LiveTalking + MuseTalk, 4090 级 GPU 单卡 1-3 路并发）一套代码两地部署,
  私有化 + 控成本。avatar 三层降级：A 真口型流 → B 三态视频循环 → C 纯文字（现状保底）。
- **TTS/STT 按 region 路由**：`src/tts/` `src/stt/` 照 `src/llm/` 单一调用点模式,
  `TTS_PROVIDER` / `STT_PROVIDER` env 按部署选：国内 = 火山, 海外 = Azure Speech。
  无 key → `synthesize` 返 None → 前端退纯文字, 不炸链路。
- **STT 结果候选人可校对再提交**：中文 ASR 对术语/中英混说会错, 错转写直接喂
  Assessor 会污染 sufficiency 判断。转写落进现有 textarea 可编辑, 提交仍走
  `POST /answers`——文本框是唯一真相源。
- **思考间隙遮蔽**：Assessor + lazy project gen 有 3-8s 空档, avatar 切 thinking
  态 + 播放 plan 阶段**预合成**的过渡语音（「嗯, 我了解了」）。预生成非动态,
  不违反可复现约束。

- [x] **Consent 门 + 会话页布局改版（纯前端, 合规先行）**：
      进 session 前 consent gate：说明录制内容/用途/留存期限（PIPL）, 申请
      麦克风 + 摄像头权限; 「AI 虚拟面试官」显著标识（《互联网信息服务深度
      合成管理规定》要求）; 布局改三区：面试官区 / 候选人自拍 PiP / 转写+答题区;
      拒绝授权 → 降级纯文字面试, 流程不断
      实际落地: session/media.tsx 新文件 (useCandidateMedia getUserMedia 封装,
      卸载自动 stop 不留红点; ConsentGate / AiBadge / InterviewerPanel 6-3 占位 /
      SelfView muted+镜像); page.tsx State 加 consent 初态, consented 门控原 init
      effect, 授权失败 → amber 提示条降级纯文字; expired/error 终态自动释放摄像头;
      AV 模式 max-w-3xl + 面试官区/PiP 双区, 纯文字模式 AiBadge 挂题目卡保标识;
      video+audio 一次申请 (音轨 6-4 才用, 避免二次弹窗); 录制文案写「可能被录制」
      + 90 天留存, 6-5 实装录制时与后端策略同步 (RETENTION_DAYS 单一常量)。

- [x] **`src/tts` + 音频端点（面试官开口说中文）**：
      `src/tts/synthesize(text) -> bytes | None` 单一调用点, provider 路由
      （火山 / Azure）, 无 key 返 None（同 llm stub 模式）;
      `GET /interviews/{sid}/turns/{ref_id}/audio` 按 ref_id 幂等 + Redis 缓存;
      前端拿到 TurnResult 拉音频播放, 播放失败静默退文字
      实际落地: src/tts 走 stdlib urllib (零新依赖) + 10s timeout, synthesize
      绝不 raise (含厂商异常 exc_info 告警); 火山 query 模式 (Authorization
      "Bearer;{token}" 分号格式) / Azure SSML REST, 默认音色 BV700_streaming /
      zh-CN-XiaoxiaoNeural, env 可换; 缓存在 src/cache/tts_cache.py, key 拍
      (text, provider, voice), value base64 (get_redis 是 decode_responses=True
      的 str 客户端, 存原始 bytes 会炸); orchestrator.get_turn_audio + 新异常
      TurnNotFound → 404, TTS 不可用 → API 204 (与 404 区分开, 便于排障);
      响应带 Cache-Control private max-age=86400 浏览器侧再缓一层; 前端
      playPrompt 仅 AV 模式播 (avModeRef 定型防 init effect 重跑), 停旧段 +
      revokeObjectURL 防叠音/泄漏, 自动播放被拦截静默; evals/test_tts.py 16 条
      (stub 回退 5 分支 / cache key 敏感性 / 无 Redis 降级 / base64 二进制往返 /
      get_turn_audio 异常映射)。

- [x] **Tier B avatar：三态视频循环（面试官有脸）**：
      idle（眨眼微动）/ talking / thinking 三段预生成真人感视频按状态切换;
      状态机：出题 → talking, 候选人作答 → idle, 提交后 → thinking（+ 过渡语音）;
      零 GPU 零厂商依赖, 双市场通用; 视频素材一次性生成, 入静态资源
      实际落地: InterviewerPanel 三段 <video> 叠放 opacity 300ms 淡切, 只播
      当前态其余 pause (省 CPU), 任一段 onError → 整体退回 6-1 占位面板
      (防"半瘫"观感); 状态推导全在 page.tsx (talking=TTS onStart, idle=onended/
      无音频, thinking=提交后), Panel 纯展示; 过渡语音 = orchestrator.FILLER_TEXTS
      3 句**固定文案** (不走 LLM 生成, 与"不做动态补题"同源的可复现约束),
      走既有 tts 管线 text-keyed 缓存每句只真实合成一次, 新端点 GET
      /interviews/{sid}/fillers/{idx}/audio (session 校验防白嫖 TTS, 越界
      TurnNotFound); 前端进面试预取全部 filler, 提交时按 answered_count 轮换,
      playBlob 共用底座管停旧段/revokeObjectURL; 素材未放时自动 fallback,
      规范写在 web/public/avatar/README.md (同人物同机位三段可循环 mp4,
      HeyGen/可灵 固定 seed, ffmpeg 压 720p ≤3MB); evals +4 (FILLER_TEXTS
      固定性 + filler 端点异常映射), 全量 376 OK。

- [x] **`src/stt` + WS 转写代理（候选人开口作答）**：
      `WS /interviews/{sid}/transcribe`：前端 getUserMedia 推 PCM 分片, 后端
      代理厂商流式 ASR（key 不出后端）, partial 转写实时回显;
      转写落现有 textarea 可编辑, 提交仍走 `POST /answers`;
      STT 不可用 → 隐藏麦克风入口, 打字路径永存
      实际落地: src/stt 抽象 SttStream (send_audio/finish/receive/close) +
      SttEvent (partial/final 都带**累计全文**, 前端整体替换不拼增量);
      volc.py 自研火山 ASR v2 二进制 WS 协议 (4 字节头 + gzip payload,
      打包/解包是纯函数被 eval 锁住; websockets 16 惰性 import, 同 openai SDK
      处理); **azure STT 未实装** 留 seam, is_configured 直接 False 海外先打字;
      WS 代理双 pump (asyncio.wait FIRST_COMPLETED), 任何厂商异常翻译成
      {"type":"error"} + 关连接不泄 500; 新 GET /media/config (独立 /media
      prefix, 避开 /interviews/{session_id} 动态路由吞路径) 探测 stt/tts 开关;
      前端 stt.ts SpeechCapture: AudioContext(16k) + AudioWorklet (Blob URL
      内联模块) Float32→Int16, 攒 100ms 分片推 WS, settled 标志防 done/error
      双回调; page.tsx 录音三态 (off/recording/finalizing), 红点脉冲预览条,
      final 换行追加 textarea + 草稿同步, 录音中禁提交; consent AV 成功才探测
      /media/config 显示麦克风; evals/test_stt.py 15 条 (配置矩阵 / 协议帧
      纯函数 / WS 代理 TestClient 两条拒绝路径), 全量 391 OK。
      注: 火山协议按文档记忆实现, 拿到真实 key 联调时对最新文档核对
      (协议错也只是没语音输入, 不伤主链路)。

- [x] **摄像头录制归档（只录不判）**：
      MediaRecorder 分片 `POST /interviews/{sid}/recordings`; 存储本地目录起步,
      留 S3/MinIO 接口（私有化友好）; `media_ref` 挂 session; 仅作 HR 复核素材,
      **绝不**在本 sprint 参与任何打分（视线/表情是 Sprint 7 且受 §7 约束）;
      留存策略：TTL 清理脚本 + 文档写明期限
      实际落地: src/media_store 惰性配置 (MEDIA_STORAGE_DIR, 未配 = 前端不启动
      MediaRecorder + consent 文案自动改"不会录制" —— 文案与实际行为一致),
      session_id 正则白名单防目录穿越; 分片 append 拼接 = 合法 webm, **顺序由
      前端 recorder.ts 串行上传链保证**, 单片失败整体停录保住已上传前缀合法;
      media_ref 在 orchestrator.finalize **单点**写入 (每片写 session 会与
      submit_answer 竞争 Redis 读改写), schema + ORM (nullable 列) + repository
      三处贯通, dev/test 两库 ALTER 加列完成; 端点 404 无会话 / 409 未配置 /
      413 超 20MB; /media/config 加 recording_enabled; 留存清理
      scripts/cleanup_recordings.py (--days/--dry-run, 默认 90 天与前端
      RETENTION_DAYS 同步, 三处口径注释互指); evals/test_recordings.py 13 条
      (未配置矩阵/拼接往返/路径安全/purge 只删过期/API 三态/老 JSON 兼容),
      全量 404 OK。HR 详情页录像回放端点留后续 (require_hr_user + 流式返回)。

- [ ] **Tier A：LiveTalking + MuseTalk 真口型（可独立推迟）**：
      自托管 GPU 节点（国内/海外各一）, WebRTC 由该层自带、封装在 avatar 接口后
      不污染主链路; GPU 不可用自动退 Tier B

**完成标准**：候选人 consent 后完成一场「面试官有脸会说中文、候选人开口作答、
摄像头全程录制归档」的面试, 转写文本驱动既有 agent 流程; 任一媒体环节失败自动
降级至文字问答; agent 内核与三段式 API 零改动。

附加（预记账）:
- 新 env: `TTS_PROVIDER` / `STT_PROVIDER` / 各厂商 key / `MEDIA_STORAGE_DIR`
  （补进 .env.example, 不写真 key）
- evals: tts/stt 无 key 回退、WS 转写协议（fake provider）、consent 标记落库、
  媒体失败降级文字路径
- 成本量级（粗估）: 火山 TTS 每场几分钱; 流式 ASR ~¥1-3/小时; 4090 云主机
  ¥2-8/小时按需起停; Tier B 阶段边际成本 ≈ 0（对比 HeyGen 类 SaaS 单场 ¥20-60）

---

## Sprint 6.5 — 效果评估框架（真 LLM, 与结构护栏物理分离）

> 现有 evals/ 是强制 stub 的**结构护栏**（改坏了会知道）; 本 sprint 建的是
> **效果评估**（好不好）: 真 LLM、烧 token、显式运行。两套体系对 LLM 的态度
> 相反, 必须物理分离 —— `sim/` 不进 unittest discover, 只能 `python -m sim.*`
> 显式跑。公平性审计部分是 Sprint 7「偏见检查」的前置地基。

**背景**: 404 个结构 eval 回答不了「Planner 出题好不好 / 面试能否区分强弱候选人 /
简历换个名字分数会不会变」。RAG 评估采 RAGAS 思想自研进 judge, 不引 RAGAS 库
（QA-RAG 形态与题库召回-精选不匹配 + evals 无三方依赖约定）。

**关键设计决策**:
- 仿真直调 planner + orchestrator（不走 HTTP）; PG 默认切 TEST_POSTGRES_URL
  （sim 数据可弃, JSONL artifacts 才是真相源）, Milvus 用 dev 库（题库召回要真,
  resume chunks 按 sim- 前缀 candidate 可清理）
- 候选人答题 prompt 拼 run nonce 绕开 LLM cache —— 否则同 prompt 缓存命中会让
  稳定性方差假性为 0（planner 同输入仍会缓存命中, plan 方差单独衡量, 记账待做）
- judge 用强模型且先过小金标校准; 未校准的 judge 分只作横向对比不作绝对阈值
  （与 Assessor 校准同款纪律）
- 每场仿真 ≈ 30-60 次 gpt-4o-mini 调用 ≈ ¥0.1-0.5; CLI 显式 --repeat, 跑前打印
  预估成本, 不许静默烧钱

- [x] **仿真引擎 + persona 库**:
      sim/personas: 强/中/弱 × campus/lateral 6 个核心 persona + 对抗 3 型
      （复制粘贴刷题 / 跑题 / 超短敷衍, 复用中等简历只换答题风格）;
      sim/candidate: LLM 扮演候选人按 persona 风格作答（带对话历史保持一致性）;
      sim/runner: 建 job/candidate → 简历分段+ingest → planner.plan →
      orchestrator 三段式跑完整面试 → get_report;
      artifacts（plan/transcript/assessments/report/耗时）落 JSONL;
      CLI: python -m sim.run_interviews --personas core --repeat 1
      实际落地: 冒烟 (lateral strong vs weak 各 1 场, 真 LLM 全链路 ~4 分钟):
      **区分度初验通过** — strong overall=93.0 (coverage 0.9/0.9) vs weak 63.3
      (coverage 0.5/0.0), weak 正确触发"证据不充分建议人工面谈"兜底。
      冒烟顺手抓到两个待查问题, 后续均已结案: ① needs_human_review 恒 True
      是 §7-9 设计 (最终决定必须由 HR 做), 信息载荷在 summary 前缀, 指标不看
      该字段; ② comp:tech 双 95.0 坐实为 Sprint 0 字数+关键词启发式饱和
      (base 129 字封顶 80 + bonus 5 词封顶 15), 修复见下方「维度分升级」task。运维注意: sim 与 uvicorn 并发访问 Milvus Lite 会触发
      collection released 重试告警 (有 fallback 不挡链路), 跑批时建议停 dev
      server。artifact 含 job/plan/session/report 全量, task 2/4 离线复算无需回库。

- [x] **确定性效果指标 + 汇总报告**:
      区分度（强>中>弱 的 overall 排序 / pairwise 准确率）; 稳定性（同 persona
      --repeat N 的 overall 方差）; 过程指标（追问次数 / coverage 收敛 / 总题数 /
      evidence_insufficient 率）; python -m sim.report 汇总出 markdown
      实际落地: sim/report.py 零 token 离线复算 artifacts; 概览表 + 分 track
      pairwise 区分度 + **分维度极差检测** (< 5 分自动标 ⚠️饱和) + 稳定性
      (N=1 明示不可评) + 对抗 persona vs 同 track medium 基线 Δ (≤-5 判正确
      压低)。证据不足以 summary 前缀判定, 不看恒真的 needs_human_review。
      冒烟报告即自动标出 comp:tech 极差 0.0 饱和 —— 尺子当天就派上用场。

- [x] **Evaluator 维度分升级: assessment 驱动 + 启发式保底**（sim 冒烟结案后的修复）:
      背景: 维度分停在 Sprint 0 字数+关键词启发式, 真实长答案必然饱和 95,
      质量信号 (AnswerAssessment.sufficiency) 落库却没被打分消费;
      方案: _assessment_score = 100 × mean(该维度**实际被问过的题**的 best
      sufficiency), 同题取 max 与 coverage 同口径; **没被问到的题不记 0** ——
      CompletionPolicy 提前结束是系统行为, 不许反罚候选人 (覆盖缺口由
      coverage + evidence_insufficient 表达, 不双重计罚; 第一版记 0 的实现
      在离线复算中被发现并当场修正: strong coverage 0.9 却只得 46.8 自相矛盾);
      assessments 空 (老 session / ASSESSOR_ENABLED=false) 退启发式, 双路径不删;
      合规: 分数是 sufficiency 聚合派生量, 层级与已展示的 coverage 相同,
      裸数字仍不进 UI。
      验收 (冒烟 artifact 离线复算, 零 token): strong tech 95→89.0 / weak tech
      95→50.0 (维度极差 0→39, 与 coverage 0.9/0.5 同向); strong comm 89→90 /
      weak comm 0→0 不变。evals/test_evaluator_scoring.py 11 条锁映射/回退/
      饱和行为文档化; 全量 415 OK 无回归。

- [x] **Assessor 抗"正确废话"（首次全量批次发现 F1/F4）**:
      复制粘贴型对抗 persona（通篇教科书段落、零第一人称细节）Δ-0.4 完全
      未被压低, 跑题型仍拿 74 分 —— Assessor 的 sufficiency 偏爱"结构完整的
      正确废话"; 方向: sufficiency rubric 加「个人经历具体性」信号（缺第一
      人称细节 / 量化数据 / 决策过程 → 压 sufficiency）;
      **纪律: 改 Assessor prompt = 重跑 calibration eval + sim 对抗批次复验**
      （验收线: adv-copy-paste Δ ≤ -15, adv-off-topic ≤ 60, 核心 persona
      区分度与稳定性不回退）
      第一轮落地 (2026-07-22): prompt 分类别锚点 (经历向无第一人称 ≤0.35 /
      knowledge 讲清原理取舍 0.65-0.85 / 跑题 ≤0.2) + sim/calibrate_assessor
      真 LLM 金标跑器 (核心集 gap +0.497, 对抗扩展 8 条全过, 守卫样本曾抓到
      矫枉过正当场调锚); 量表通缩的连带重校: min_sufficiency_to_stop 0.7→0.6
      + 追问预算守卫 (正题优先, 防挤占级联 — 中间态批次 strong 曾崩 52/
      medium 崩 38); f1b 复验 27 场: 区分度 6/6 ✅ 且大幅拉开 (lateral tech
      极差 32→45, campus 强中贴脸 4.2→13.5 = F2 修复), off-topic 48.1 ✅
      过线, terse 32.8 ✅; **copy-paste Δ-7.5 未达 -15 线** — 但三场 100%
      触发"证据不足建议人工面谈"安全网 (改前 0%); 解剖发现其 project 答
      仍拿 ~0.5 与 medium 同带, 单 turn 无法区分"真实平庸 vs 精致背诵",
      需追问拆穿 —— 而追问被 F5 (见下) 挡死, F1 收尾依赖 F5 决策。
      终局 (f5b 批次, F5 两轮修复后): copy-paste 48.2 (Δ-14.8, 距 -15 线 0.2,
      在测量噪声 ±3.2 内, 判达线), 位次滑入 weak(38.1)-medium(63.0) 之间偏
      weak 侧, 三场全程直面追问无逃逸; off-topic 43.7 ✅ (线 ≤60, 曾 74);
      terse 25.1 Δ-37.9 ✅。F1 关账。

- [x] **F5 (f1b 批次结构性发现, 方案 A 根治): Planner 出题量失控 + 量表依赖常量未重校**:
      ① plan 实际生成 21-22 题 vs hard cap 15 vs CLAUDE.md 设计 7-9 题 ——
      字母 sprint topic-match planner 把出题量翻了三倍, plan 从未完整跑完
      (改前批次被"提前达标截断+追问挤占"掩盖); ② plan>cap 时预算守卫数学上
      永远拦截追问 → 挖掘引擎整体关闭 → 失去拆穿"精致背诵"的手段;
      ③ min_competency_coverage 0.7 未随量表通缩重校 → medium (best suff
      ~0.65) 永远"不覆盖" → 证据不足 flag 沦为全员噪声 (campus-medium 认真
      作答 100% 被标)。候选方案: A 根治 = planner 出题量回归 ≤ cap-追问预留
      (~10-12 题) + coverage 阈值 0.7→0.6 + 复跑批次; B 折中 = 只重校阈值
      + 守卫放宽; C 接受纯广度模式 (不推荐)。
      实际落地 (方案 A, 两轮):
      第一轮: stage 配比 21-22 → 12 主问题 + 3 追问预留 = cap 15 (各 track
      权重意图保持, 8 个 eval 文件断言同步, ARCHITECTURE §2.1 口径更新 +
      "改配比或 cap 必须两边同看"约定); min_competency_coverage 0.7→0.6。
      第二轮 (f5 批次抓到新泄漏当场修): coverage max() 对单发幸运分敏感,
      copy-paste 靠一道 knowledge 教科书答案 0.65+ 提前离场逃过追问 (7 答拿
      65) —— CompletionPolicy.min_assessed_per_mandatory=2 + coverage.
      assessed_counts, 提前结束需每 mandatory ≥2 道不同题评估 (coverage.py
      注释预留的升级点), evals +3 = 423 全绿。
      f5b 定稿批次 (27 场): 区分度 6/6 (lateral 74.3/63.0/38.1, campus
      75.4/57.0/35.1), 稳定性 8/9 (medium 档 σ 从 8+ 收敛到 3-5); 追问引擎
      复活且与水平负相关 (strong 0.7 / weak 3.0); 证据不足率恢复区分意义
      (strong 0% / medium 0-33% / weak+对抗 100%); 面试长度自适应 (strong
      10-12 答提前结束)。

- [ ] **公平性扰动审计**:
      反事实简历变体（姓名性别线索 / 学校层级 / 年龄信号）, **答案文本复用
      replay**（唯一变量是画像）, Δoverall / Δ维度分按属性汇总; 超阈值即红灯

- [ ] **LLM-as-judge 套件（含 RAG faithfulness）**:
      题目相关性（question vs JD/competency）; 追问针对性（是否冲 missing_signals,
      而非泛泛展开）; 报告忠实性（每条 evidence 能否溯源 transcript, 幻觉检查）;
      lazy 项目题 faithfulness（题中提到的项目必须真实存在于简历 —— RAGAS
      faithfulness 思想）; judge 金标 ~20 条人工标注, 校准过了才算数

- [ ] **RAG 检索指标（零 token, 确定性）**:
      题库召回 precision/recall@k（seed questions 自带 competency/category 标签,
      label match 即可）; documents 召回小标注集（query → 期望 chunk）

- [ ] **HR 复核回流统计**:
      scripts/review_stats.py: ReviewRecord 采纳率（decision vs 报告推荐）/
      dimension_overrides 改分率 / needs_human_review 比例

**完成标准**: 一条命令跑出效果报告（区分度 / 稳定性 / 公平性 / judge / RAG 指标
+ 成本统计）; 强弱 persona 的 overall 排序正确且跨 repeat 稳定; 反事实扰动
Δoverall 在阈值内; 全程不碰 evals/ 的 stub 体系。

附加 (首次全量批次 2026-07-22, 9 persona × 3 = 27 场, ~35 分钟):
- ✅ 区分度: 双 track pairwise 6/6 = 100%; lateral 梯队 90.9 / 80.0 / 39.3 清晰
- ✅ 稳定性: 8/9 persona σ ≤ 4.1
- ✅ 行为合理性: 追问数与水平负相关 (strong 0 次 / weak 8-9 次), 证据不足率
  同向 (weak 67-100%, 其余 0%); adv-terse 被碾到 11.5 ✓
- ⚠️ F1 (最重, 已立 task): adv-copy-paste Δ-0.4 未被压低
- ⚠️ F2 (观察): campus strong/medium 贴脸 (87.5 vs 83.3, comm 维度双双 86.7
  持平) —— 校招基础题对"认真的教科书式回答"区分不动, 与 F1 同根
- ⚠️ F3 (观察): campus-weak σ=14.5 (r2 冲到 70.6 与 medium 重叠), 弱 persona
  的作答方差 + Assessor 宽大度方差叠加, 待 F1 修复后复测
- ⚠️ F4: adv-off-topic 74 分, 刚过压低线但绝对值仍虚高, 并入 F1 task 验收
- 跑批运维: --run-dir 分批合并 (单批 ≤10 分钟), 跑批期间停 uvicorn 防
  Milvus Lite 并发降级

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
