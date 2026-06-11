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

- [ ] Next.js + TypeScript 初始化，候选人面试界面
- [ ] 候选人端: 面试前 Resume 上传页（支持文本粘贴 / 文件上传, 至少存原文）
- [ ] 对接面试会话 API，文本一问一答 + 追问
- [ ] 会话进度、剩余轮次展示
- [ ] 基础鉴权（候选人凭邀请链接进入）

**完成标准**：候选人能在浏览器里完成一次完整文本面试。

---

## Sprint 5 — 招聘端 Dashboard

- [ ] HR 端：创建职位、上传资料、查看生成的面试计划
- [ ] 候选人列表与面试状态
- [ ] 评估报告查看页：内容维度与表现维度分区展示
- [ ] **人工复核**：HR 可标注/覆盖、留存复核记录

**完成标准**：HR 能从建岗到查看报告全程在界面完成。

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
