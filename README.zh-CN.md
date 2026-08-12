# AutoView — AI 面试平台

> English version: [`README.md`](./README.md)

多 agent 的 AI 视频面试基础设施。HR 注册账号、上传 JD / 岗位要求 / 公司资料(支持
飞书文档导入),候选人上传 Resume,系统自动生成面试计划、执行多轮面试与智能追问、
产出结构化评估报告,并支持 HR 人工复核。数据按 HR 隔离,多租户各管各的。

终态是「招聘端 + 候选人端」双边 AI 面试平台。当前已贯通**视频面试**(Tier B):
AI 虚拟面试官有脸、开口说中文提问,候选人可语音作答(转写可校对再提交)、
摄像头画面录制归档;真口型数字人(Tier A)与多模态评价在后续 sprint。
文字问答是永远的保底路径——任何媒体环节失败都自动降级,面试不中断。

> 详细架构与合规约束见 [`ARCHITECTURE.md`](./ARCHITECTURE.md),开发进度与里程碑见
> [`sprint.md`](./sprint.md),仓库内协作守则见 [`CLAUDE.md`](./CLAUDE.md),
> 评估体系(结构护栏 + 真 LLM 效果评估)与结果记录见 [`EVALUATION.md`](./EVALUATION.md),
> Agent 能力升级(8.x 系列)的提案与评审见 [`AGENT_UPGRADES.md`](./AGENT_UPGRADES.md)。

---

## 核心特性

### 面试内核
- **多 agent 编排**:Planner / Interviewer / Assessor / Evaluator / Analyzer 各司其职,
  agent 之间不互相调用,统一由 Orchestrator 路由
- **双 track 面试流程**:`campus`(校招,重知识)/ `lateral`(社招,重项目 + 场景),
  track 影响 stage 配额,不污染 agent 内核
- **阶段化推进**:`self_intro → knowledge → project → scenario`,项目题用
  **lazy generation**——拿到 self_intro 文本后再结合 Resume RAG 现场生成,
  并优先深挖 CandidateModel 里累积的存疑点
- **在线 Assessor**:每个回答产出结构化 `AnswerAssessment`;LLM 失败一律降级启发式,
  双路径永存。真 LLM 路径附 **Platt 校准分**(P(信号充分),50 条人工金标拟合)
- **校准 + 信念驱动追问**(Sprint 8.3):追问决策在校准概率空间判定,叠加
  全局追问预算与 competency 信念方差门(证据足且已确立为佳的维度不再耗预算);
  启发式路径行为与配置驱动的 FollowUpPolicy / CompletionPolicy 保持不变
- **跨 stage 候选人记忆**(Sprint 8.4):strengths/concerns 沉淀为带原文证据的
  SkillClaim(verified/doubted/contradicted),矛盾用确定性规则标记、追问澄清、
  summary 如实提及但不替 HR 裁决
- **rubric 化打分**(Sprint 8.5):每题 plan 期生成 3-6 条二元 checklist,
  Assessor 逐项判定,题级质量 = 0.7×sufficiency + 0.3×命中率;
  多裁判 panel 代码就绪但**默认关**(MAE 校准门禁未过不开)
- **不做动态补题**——题库由 plan + lazy project gen 一次确定(可复现 + 公平性)

### 安全与合规
- **注册与多租户隔离**(Sprint 6.8):HR 自助注册(可选邀请码),岗位/候选人/
  报告按 owner 隔离,越权一律 404;admin 全量
- **输入注入防御**(Sprint 8.1):候选人文本经 `wrap_untrusted`(hash 派生
  nonce 边界)拼入 prompt + 指令无效声明;简历/回答剥不可见字符;异常只标记
  `integrity_flags` 供人工复核,**不拦截**(防 over-defense 误伤)
- **决策 Trace + 确定性回放**(Sprint 8.2):每场面试的追问/结束/评估决策带
  实际阈值数值全量落 `decision_traces`(EU AI Act Art.12「决策可重构」);
  `REPLAY_MODE` 按 request_hash 回放,golden trace diff 做零 token 回归
- **合规分区写进 schema**:`overall` 只由 `content_scores` 得出;软信号、
  assessment 数字、信念、记忆、trace 都是内部数据,不进 HR UI 明细

### 媒体与数据
- **视频面试媒体层(纯适配器)**:consent 门 → TTS 播报(火山/Azure 按 region
  路由)→ 三态视频 avatar → 流式 STT 语音作答(WS 代理,厂商 key 不出后端)→
  摄像头录制归档(**只录不判**)
- **RAG 题库与资料检索**:questions + documents 两个 collection;dev/eval 用
  Milvus Lite 单文件,多进程/部署用 **Milvus standalone**(docker,检索
  Strong 一致性);PG 是真理之源,Milvus 可随时重建
- **知识管线**:md 语料上传 / **飞书 wiki·docx 导入**(Sprint 6.7,凭证可在
  前端配置、Fernet 加密存储)→ 切片入库 → LLM 反向出题 → HR 审核进题库
- **简历深解析**:语义分段 + 图片 OCR + PDF/DOCX 上传;Planner 出题走主题匹配 +
  技能抽取

### 并发与异步(Sprint 9)
- **RQ 任务队列**:候选人上传后的切片 + 出题走独立 worker 进程,失败退避重试;
  队列不可用自动退回 BackgroundTasks
- **并发承载**:per-session Redis 锁(并发同会话 409)、PG 连接池 / threadpool
  env 可调;实测 30 路并发面试 30/30 零失败零串扰,应用层 turn 延迟
  p50 18ms、吞吐 ~367 turns/s(stub 压测口径,不含 LLM 时延;
  见 EVALUATION.md 2026-07-30 勘误)

### 质量体系
- **两套评估物理分离**:`evals/` 590 条结构护栏(强制 stub,零 token)+
  `sim/` 真 LLM 效果评估(persona 仿真 / 对抗 / 公平性扰动 / LLM-as-judge
  金标校准 / RAG 指标 / 冻结回归基线),18 种可复跑手段,见 EVALUATION.md

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 语言 | Python 3.11+ |
| 数据契约 | pydantic v2 |
| LLM / Embedding | OpenAI(`gpt-4o-mini` + `text-embedding-3-small`,单 provider) |
| 关系存储 | PostgreSQL + SQLAlchemy 2.0 + psycopg3(池容量 env 可调) |
| 热存储 / 缓存 / 锁 | Redis(会话状态 + LLM/embedding/TTS 缓存 + 会话锁 + 队列 broker) |
| 任务队列 | RQ(独立 worker,默认关,`JOBS_QUEUE_ENABLED` 开) |
| 向量检索 | Milvus Lite(dev/eval 单进程)/ Milvus standalone(docker,多进程) |
| HTTP API | FastAPI + uvicorn |
| 鉴权 | JWT(HS256)+ httpOnly cookie + bcrypt;注册可选邀请码 |
| 外部文档源 | 飞书 OpenAPI(tenant/user token 双模,凭证 Fernet 加密) |
| 实时媒体 | TTS / 流式 STT 按 region 路由(火山 / Azure)· WS 转写代理 · MediaRecorder 录制 |
| 数字人 | Tier B 三态视频循环(`web/public/avatar/`)→ Tier A 真口型(规划中) |
| 候选人 / HR 前端 | Next.js 16 + React 19 + Tailwind 4(`web/`) |
| 测试 / 评估 | stdlib `unittest`(`evals/`,强制 stub)+ `sim/`(真 LLM 效果评估) |

---

## 快速开始

### 1. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. 起本机外部服务

```bash
brew services start postgresql
brew services start redis
createdb interview          # 业务库
createdb interview_test     # eval 专用, 防误删 dev 数据

# 可选 (多进程/高并发时需要): 独立 Milvus
docker compose up -d milvus
```

### 3. 配置 `.env`

复制 `.env.example` 为 `.env`,按需填:

- `OPENAI_API_KEY` —— 留空走 stub,骨架仍可跑(不会调真实 LLM)
- `POSTGRES_URL` / `TEST_POSTGRES_URL` / `REDIS_URL` / `MILVUS_LITE_URI`
- `JWT_SECRET` —— prod 用 `openssl rand -hex 32`
- `MILVUS_SERVER_URI` —— 配了就优先于 lite(需 docker milvus;两份数据独立,
  切换后记得重灌题库)
- `JOBS_QUEUE_ENABLED` —— 开队列须另起 worker:`python -m src.jobs.worker`
- `REGISTER_INVITE_CODE` —— 配了注册必填邀请码;留空开放注册
- **媒体(可选)**:`TTS_PROVIDER` / `STT_PROVIDER` + 火山/Azure key、
  `MEDIA_STORAGE_DIR`——全不配 = 纯文字面试;**飞书(可选)**:
  `FEISHU_APP_ID/SECRET` 或直接在 HR 前端配置

各变量的语义与失败回退行为见 `.env.example` 注释。改 `.env` 后要重启 uvicorn/worker。

### 4. 起服务

```bash
uvicorn api.main:app --reload          # API, /docs 看 OpenAPI
python -m src.jobs.worker              # 可选: RQ worker (开队列时)
cd web && npm install && npm run dev   # 前端 http://localhost:3000
```

`python -m src.main` 仍可跑写死输入的骨架 demo(需 PG + Redis)。

### 5. 种子数据与运维脚本

```bash
python -m scripts.seed_users           # 默认 HR 账号 (或前端 /hr/register 注册)
python -m scripts.seed_questions       # 灌题库到 Milvus
python -m scripts.ingest_md_corpus     # corpus/ md 语料切片入库 (知识管线)
python -m scripts.derive_questions     # 反向出题, 进 HR 审核队列
python -m scripts.backfill_job_owner   # 存量岗位归属迁移 (6.8 隔离)
python -m scripts.cleanup_recordings   # 面试录像留存清理 (建议挂 cron, 默认 90 天)
python -m scripts.load_test            # 并发压测 (stub, 零 token)
python -m scripts.record_golden_traces # golden trace 录制 (改 prompt/policy 后)
python -m scripts.freeze_persona_answers  # sim 冻结基线重录 (出题变更后)
python -m scripts.review_stats         # HR 复核回流统计
```

### 6. 数字人素材(可选)

`web/public/avatar/{idle,talking,thinking}.mp4` 三段同人视频按状态切换,
生成与 ffmpeg 处理规范见 [`web/public/avatar/README.md`](./web/public/avatar/README.md)。
素材缺失自动退回占位面板,不影响面试。

---

## 项目结构

```
src/
  schemas/        全部 pydantic 数据契约 (agent 输入输出类型)
  llm/            OpenAI Chat 唯一调用点; stub 回退; sanitize (注入防御, 8.1)
  embeddings/     OpenAI Embeddings 唯一调用点; 零向量 stub
  agents/
    planner/      JD + Resume + RAG → InterviewPlan (track/lazy/rubric/主题匹配)
    interviewer/  下一题 / 追问 / 结束; 校准+信念门控决策 (8.3)
    assessor/     单题在线评估 + Platt 校准 (calibration.py); 失败降启发式
    evaluator/    EvaluationReport (rubric 命中率进分; panel 默认关, 8.5)
    analyzer/     多模态分析占位 (Sprint 7)
  orchestrator/   串联 agent + Redis 状态机 + 会话锁 + PG 归档 + trace 编排
  trace/          决策 trace 收集 (contextvars) + 确定性回放 + diff (8.2)
  beliefs.py      competency 信念的高斯共轭更新 (8.3)
  candidate_model.py  跨 stage 候选人记忆: SkillClaim 沉淀/矛盾/反思 (8.4)
  coverage.py     competency_coverage / richness 计算 (CompletionPolicy 消费)
  jobs/           RQ 任务队列: enqueue / tasks / worker 入口 (Sprint 9)
  db/             Postgres 惰性连接 + ORM + repository
  cache/          Redis: 会话热存 + LLM/embedding/TTS 缓存 + trace 热存 + 会话锁
  vector_store/   Milvus: questions + documents; lite/server 双模式 (Sprint 9)
  connectors/     飞书 OpenAPI (wiki/docx 拉取, 凭证加密, 6.7)
  auth/           JWT + bcrypt + httpOnly cookie 鉴权 (cookie 优先 Bearer 兜底)
  tts/ stt/       语音合成 / 流式转写唯一调用点 (未配置自动退文字)
  media_store/    面试录像归档 (只录不判)
  knowledge_pipeline/ + derivation/   md 语料解析 + 反向出题 (离线管线)
  ingestion/      文档切片 + 向量化 + 简历语义分段
  resume_parser.py  PDF / DOCX / 图片(OCR) Resume 解析 + 净化
  main.py         写死输入跑通 demo

api/
  main.py         FastAPI 工厂: CORS / 异常映射 / threadpool 容量
  routes/         auth · jobs · candidates · interviews · media · hr
                  · admin_upload · admin_drafts · admin_feishu
  schemas.py      API 出入参 (DTO)

web/              Next.js 16 候选人端 + HR Dashboard (注册/登录/岗位/复核/admin)
sim/              真 LLM 效果评估: persona 仿真 / 对抗 / 公平性 / judge /
                  校准 (assessor·platt·judges·evaluator) / RAG 指标 / 冻结基线
evals/            stdlib unittest 结构护栏 (590 条, 强制 stub, CI 零外部依赖)
scripts/          运维脚本 (见上文清单)
docker-compose.yml + deploy/   Milvus standalone (Sprint 9)
corpus/           知识管线的 md 语料
```

---

## API 简表

所有路由的真实出入参以 `/docs` 为准。

| 模块 | 路由 | 说明 |
| --- | --- | --- |
| Auth | `POST /auth/register` · `POST /auth/login` · `GET /auth/me` · `POST /auth/logout` | 注册(可选邀请码)+ httpOnly cookie 鉴权 |
| Jobs | `GET/POST /jobs` · `GET /jobs/{id}` | 建岗需登录,归属创建者(6.8) |
| Candidates | `POST /jobs/{id}/candidates` · `.../parse-resume` · `GET .../plan` | Resume 解析 + 净化 + 触发 Planner(队列或 BG) |
| Interviews | `POST /interviews` · `POST /interviews/{id}/answers` · `GET /interviews/{id}` · `POST .../finalize` · `GET .../report` | 三段式会话 + 会话锁(并发 409)+ 中断恢复 |
| Media | `GET /media/config` · turn/filler 音频 · `WS .../transcribe` · `POST .../recordings` | TTS / 转写代理 / 录制归档 |
| HR | `GET /hr/jobs` · `GET /hr/sessions/{id}` · `GET /hr/sessions/{id}/trace` · `GET /hr/reports/{id}` · `PATCH .../review` | 按 owner 隔离;trace 为审计导出(不进 UI) |
| Admin | `POST /admin/upload-knowledge` · drafts 审核 · `/admin/feishu/*` | 语料上传 / 反向出题审核 / 飞书导入与凭证配置 |

---

## 测试与评估

```bash
python -m unittest discover -s evals               # 结构护栏全量 (零 token)
python -m unittest evals.test_trace_replay         # golden trace 决策回归
python -m sim.run_interviews --personas all --frozen --run-dir sim/runs/xxx
                                                   # 冻结基线批次 (真 LLM)
```

约定:

- `evals/` 强制走 LLM stub(清 `OPENAI_API_KEY`);需要 PG+Redis 的 case 缺 env
  自动 skip;一律用 `TEST_POSTGRES_URL`,**绝不**碰 dev 库
- **改任何 prompt / policy 阈值 = 双门禁**:golden trace diff(变红属预期信号,
  确认后重录)+ 对应 calibration;改 Assessor 必重跑 `sim/calibrate_assessor`
- 效果评估(真 LLM、烧 token)走 `sim/`,与结构护栏物理分离;
  手段、指标与历次结果见 [`EVALUATION.md`](./EVALUATION.md)

---

## 关键设计约束(写代码前必读)

- Agent 间通信只走 Orchestrator;LLM/embedding 调用一律走 `src/llm` / `src/embeddings`
- 新增 LLM 调用必带 timeout + 确定性 fallback;启发式保底永不删
- **内部数据不外泄**:`AnswerAssessment` 数字 / 信念 / CandidateModel / trace /
  integrity_flags 不进 HR UI 明细,候选人不接触报告
- **不做动态补题**;多模态软信号绝不作为自动淘汰依据(详见 ARCHITECTURE §7)
- **媒体层是纯适配器**:任一环节失败降级回文字问答;录像只录不判;
  转写候选人可校对,textarea 是唯一真相源
- 越权访问一律 404(防枚举);数据按 owner 隔离
- **panel(多裁判)未过 MAE 校准门禁不许开**(`EVAL_PANEL_ENABLED` 默认关)

---

## 进度

- ✅ Sprint 0–5.9 — 骨架 → 持久化 → API → RAG → 候选人端 → HR Dashboard →
  双 track/stage 化 → Assessor → CompletionPolicy → calibration
- ✅ 字母 sprint 系列 — 知识管线 / 简历分段 + 图片 OCR / Planner 主题匹配
- 🔨 Sprint 6 — 视频面试(5/6;Tier A 真口型待定)
- ✅ Sprint 6.5 — 效果评估体系(sim/ + 8 类质量缺陷修复)
- ✅ Sprint 6.7 — 飞书文档导入(前端可配凭证)
- ✅ Sprint 6.8 — HR 注册 + 按 owner 数据隔离
- ✅ Sprint 8.1–8.5 — Agent 能力升级:注入防御 / 决策 trace+回放 /
  校准+信念驱动追问 / CandidateModel 记忆 / rubric 化打分+裁判团(默认关)
- ✅ Sprint 8.3.1 — sim 冻结回归基线("真 LLM 版 golden")
- ✅ Sprint 9 — RQ 异步队列 + Milvus standalone + 会话锁 + 并发压测
- ⏳ Sprint 7 — 多模态评价(含合规护栏)· 部署

完整任务清单见 [`sprint.md`](./sprint.md)。
