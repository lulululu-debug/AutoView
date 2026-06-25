# AI Interview Platform

多 agent 的 AI 视频面试基础设施。HR 上传 JD / 岗位要求 / 公司资料，候选人上传 Resume，
系统自动生成面试计划、执行多轮面试与追问、产出结构化评估报告，并支持 HR 人工复核。

终态是「招聘端 + 候选人端」双边 AI 面试平台。当前主链路文本面试已贯通，
视频/实时与多模态评价在后续 sprint。

> 详细架构与合规约束见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)，开发进度与里程碑见
> [`sprint.md`](./sprint.md)，仓库内协作守则见 [`CLAUDE.md`](./CLAUDE.md)。

---

## 核心特性

- **多 agent 编排**：Planner / Interviewer / Assessor / Evaluator / Analyzer 各司其职，
  agent 之间不互相调用，统一由 Orchestrator 路由
- **双 track 面试流程**：`campus`（校招，重知识）/ `lateral`（社招，重项目 + 场景），
  track 影响 stage 配额，不污染 agent 内核
- **阶段化推进**：`self_intro → knowledge → project → scenario → done`，项目题用
  **lazy generation**——拿到 self_intro 文本后再结合 Resume RAG 现场生成
- **在线 Assessor**：每个回答产出结构化 `AnswerAssessment`（sufficiency / confidence /
  missing_signals / followup_goal），驱动追问与终止策略；LLM 失败一律降级到启发式
- **FollowUpPolicy + CompletionPolicy**：追问配额与结束条件全配置驱动，
  不做动态补题（保证可复现 + 公平性）
- **RAG 题库与资料检索**：Milvus Lite 存 questions + documents 两个 collection，
  Planner / Evaluator 共用
- **合规分区写进 schema**：`EvaluationReport` 把 `content_scores`（进总分）与
  `performance_observations`（软信号，不进总分）严格分开
- **Stub 回退**：无 `OPENAI_API_KEY` / Postgres / Redis / Milvus 时各自走 stub 或惰性连接，
  骨架可纯本地跑通

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 语言 | Python 3.11+ |
| 数据契约 | pydantic v2 |
| LLM / Embedding | OpenAI（`gpt-4o-mini` + `text-embedding-3-small`，单 provider） |
| 关系存储 | PostgreSQL + SQLAlchemy 2.0 + psycopg3 |
| 热存储 / 缓存 | Redis（会话状态 + LLM/embedding 缓存） |
| 向量检索 | Milvus Lite（pymilvus） |
| HTTP API | FastAPI + uvicorn |
| 鉴权 | JWT + httpOnly cookie + bcrypt |
| 候选人 / HR 前端 | Next.js 16 + React 19 + Tailwind 4（`web/`） |
| 测试 | stdlib `unittest`（`evals/`） |

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
```

### 3. 配置 `.env`

复制 `.env.example` 为 `.env`，按需填：

- `OPENAI_API_KEY` —— 留空走 stub，骨架仍可跑（不会调真实 LLM）
- `POSTGRES_URL` / `TEST_POSTGRES_URL` / `REDIS_URL` / `MILVUS_LITE_URI`
- `JWT_SECRET` —— prod 用 `openssl rand -hex 32`，dev 随便 hex
- `ASSESSOR_ENABLED` —— 默认 `true`（Sprint 5.9 起 calibration 通过后翻默认）

各变量的语义与失败回退行为见 `.env.example` 注释。

### 4. 跑通骨架 demo

```bash
python -m src.main
```

写死的 JD + Resume + 候选人回答跑完整链路，打印一份 `EvaluationReport`。
需要 Postgres + Redis（Sprint 1 起 orchestrator 强依赖）。

### 5. 起 HTTP API

```bash
uvicorn api.main:app --reload
# OpenAPI: http://127.0.0.1:8000/docs
```

### 6. 起前端（可选）

```bash
cd web
npm install
npm run dev
# http://localhost:3000
```

### 7. 种子数据

```bash
python -m scripts.seed_questions   # 灌题库到 Milvus
python -m scripts.seed_users       # 灌默认 HR 账号
```

---

## 项目结构

```
src/
  schemas/        全部 pydantic 数据契约 (agent 输入输出类型)
  llm/            OpenAI Chat Completions 的唯一调用点; 无 key 走 stub
  embeddings/     OpenAI Embeddings 的唯一调用点; 无 key 返回零向量 stub
  agents/
    planner/      JD + Resume + RAG → InterviewPlan (track-aware, lazy project gen)
    interviewer/  下一题 / 追问 / 结束信号; 消费 Assessor 输出
    assessor/     单题在线评估 (AnswerAssessment); LLM 失败降级启发式
    evaluator/    面试结束时合成 EvaluationReport (content vs performance 分区)
    analyzer/     多模态分析占位 (Sprint 7)
  orchestrator/   串联 agent + Redis 状态机 + Postgres 归档
  db/             Postgres 惰性连接 + ORM + repository
  cache/          Redis 惰性连接 + 会话热存储 + LLM/embedding 缓存
  vector_store/   Milvus Lite: questions + documents 两个 collection
  ingestion/      文档切片 + 向量化 pipeline
  resume_parser/  PDF / DOCX / TXT Resume 解析
  auth/           JWT + bcrypt + cookie 鉴权
  coverage.py     CompletionPolicy 用的 competency_coverage 计算
  main.py         Sprint 0 风格的写死输入跑通 demo

api/
  main.py         FastAPI 工厂, CORS / 异常映射 / 路由聚合
  routes/         auth · jobs · candidates · interviews · hr
  schemas.py      API 出入参 (DTO)

web/              Next.js 16 候选人端 + HR Dashboard

scripts/          一次性运维脚本 (seed_questions / seed_users)
evals/            stdlib unittest, 结构性 + 合规护栏 + API smoke + calibration
```

---

## API 简表

所有路由的真实出入参以 `/docs` 为准。

| 模块 | 路由 | 说明 |
| --- | --- | --- |
| Auth | `POST /auth/login` · `GET /auth/me` · `POST /auth/logout` | httpOnly cookie 鉴权 |
| Jobs | `GET/POST /jobs` · `GET /jobs/{id}` | HR 创建 / 列出职位 |
| Candidates | `POST /jobs/{id}/candidates` · `POST /candidates/parse-resume` · `GET /candidates/{id}` · `GET /candidates/{id}/plan` | Resume 解析 + 触发 Planner |
| Interviews | `POST /interviews` · `POST /interviews/{id}/answers` · `GET /interviews/{id}` · `POST /interviews/{id}/finalize` · `GET /interviews/{id}/report` | 三段式会话 + 中断恢复 + 报告 |
| HR | `GET /hr/jobs` · `GET /hr/sessions/{id}` · `GET /hr/reports/{id}` · `PATCH /hr/reports/{id}/review` | Dashboard + 人工复核 |

---

## 测试

```bash
# 全量
python -m unittest discover -s evals

# 单类
python -m unittest evals.test_skeleton.ComplianceInvariantTests

# 单文件
python -m unittest evals.test_assessor_calibration
```

约定：

- `evals/` 内所有 `TestCase` 强制走 LLM stub（清掉 `OPENAI_API_KEY`），结构性护栏快、稳、不烧 token
- 需要 PG + Redis 的端到端 case 在缺 env 时自动 skip
- eval 用 `TEST_POSTGRES_URL`，**绝不**共用 `POSTGRES_URL`（部分 case 会 `TRUNCATE`）
- 改 prompt 模板前确认有对应 eval；改 **Assessor / FollowUpPolicy / CompletionPolicy** 必须先跑 calibration eval

---

## 关键设计约束（写代码前必读）

- Agent 间通信只走 Orchestrator，agent 之间**不**互相 import
- LLM 调用一律走 `src/llm/`，embedding 一律走 `src/embeddings/`，agent 里不 import `openai`
- 新增 LLM 调用必带 timeout + 启发式 fallback，不引入"LLM 挂了整条链路就挂"的依赖
- `AnswerAssessment` 的数字（sufficiency / confidence）**不暴露给 HR UI / 候选人**，
  仅作内部追问决策 + 审计
- 候选人端**不返回** `EvaluationReport` / `DimensionScore`
- **不做动态补题**——题库由 plan + lazy project gen 一次确定；coverage 不够走人工复核兜底
- 多模态「眼神 / 语气」信号只能作为参考证据，**绝不可**作为自动淘汰的唯一依据
- 嵌套结构走 JSONB，顶层可查询字段提列；按需拆子表，不一次拆完
- 骨架阶段不实现视频 / 实时语音 / 多模态分析，全部用文本或占位

---

## 进度

- ✅ Sprint 0 — Walking Skeleton（纯文本跑通主链路）
- ✅ Sprint 1 — Postgres + Redis 持久化与会话态
- ✅ Sprint 2 — FastAPI HTTP API + 真实 JD/Resume 解析
- ✅ Sprint 3 — Milvus + RAG 题库 / 资料检索
- ✅ Sprint 4 — 候选人端 Web（文本面试）
- ✅ Sprint 5 — HR Dashboard
- ✅ Sprint 5.5 — 校招 / 社招双 track + 阶段化面试
- ✅ Sprint 5.6 — Assessor + FollowUpPolicy
- ✅ Sprint 5.7 — Assessment 持久化 + CompletionPolicy
- ✅ Sprint 5.8 — PDF Resume + Cookie 鉴权 + 追问 UI
- ✅ Sprint 5.9 — Assessor calibration 升级 + 默认开启
- ⏳ Sprint 6 — 实时媒体（视频面试）
- ⏳ Sprint 7 — 多模态评价（含合规护栏）

完整任务清单见 [`sprint.md`](./sprint.md)。
