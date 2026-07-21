# AI Interview Platform

多 agent 的 AI 视频面试基础设施。HR 上传 JD / 岗位要求 / 公司资料，候选人上传 Resume，
系统自动生成面试计划、执行多轮面试与追问、产出结构化评估报告，并支持 HR 人工复核。

终态是「招聘端 + 候选人端」双边 AI 面试平台。当前已贯通**视频面试**（Tier B）：
AI 虚拟面试官有脸、开口说中文提问，候选人可语音作答（转写可校对再提交）、
摄像头画面录制归档；真口型数字人（Tier A）与多模态评价在后续 sprint。
文字问答是永远的保底路径——任何媒体环节失败都自动降级，面试不中断。

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
- **视频面试媒体层（纯适配器，Sprint 6）**：consent 门（PIPL 告知 + AI 合成标识）→
  TTS 面试官播报（火山 / Azure 按 region 路由）→ 三态视频 avatar（说话 / 聆听 / 思考,
  思考空档播预合成过渡语音遮蔽）→ 流式 STT 语音作答（WS 代理, 厂商 key 不出后端）→
  摄像头录制归档（**只录不判**）。agent 内核与三段式 API 零改动
- **RAG 题库与资料检索**：Milvus Lite 存 questions + documents 两个 collection，
  Planner / Evaluator 共用
- **知识管线**：md 语料（`corpus/`）→ 切片入库 → LLM 反向出题 → HR 审核队列 →
  批准进题库；LLM 失败宁缺勿滥，不塞模板题
- **简历深解析**：分段结构化（教育 / 项目 / 实习）+ 图片简历 OCR + 文件上传；
  Planner 出题走主题匹配 + 技能抽取，`used_source_ids + prior_texts` 防同维度重复
- **合规分区写进 schema**：`EvaluationReport` 把 `content_scores`（进总分）与
  `performance_observations`（软信号，不进总分）严格分开
- **Stub 回退**：无 `OPENAI_API_KEY` / Postgres / Redis / Milvus / TTS / STT 时各自走
  stub、惰性连接或纯文字降级，骨架可纯本地跑通

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
| 实时媒体 | TTS / 流式 STT 按 region 路由（火山 / Azure）· WS 转写代理 · MediaRecorder 录制 |
| 数字人 | Tier B 三态视频循环（`web/public/avatar/`）→ Tier A 真口型（规划中） |
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
- **媒体（可选，Sprint 6）**：`TTS_PROVIDER` + 火山 / Azure key（面试官出声）、
  `STT_PROVIDER` + key（语音作答）、`MEDIA_STORAGE_DIR`（录制归档）——
  全部不配 = 纯文字面试，功能自动隐藏；改 `.env` 后要重启 uvicorn

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

### 7. 种子数据与运维脚本

```bash
python -m scripts.seed_questions       # 灌题库到 Milvus
python -m scripts.seed_users           # 灌默认 HR 账号
python -m scripts.ingest_md_corpus     # corpus/ md 语料切片入库 (知识管线)
python -m scripts.derive_questions     # 反向出题, 进 HR 审核队列
python -m scripts.cleanup_recordings   # 面试录像留存清理 (建议挂 cron, 默认 90 天)
```

### 8. 数字人素材（可选）

`web/public/avatar/{idle,talking,thinking}.mp4` 三段同人视频按状态切换，
生成与 ffmpeg 处理规范见 [`web/public/avatar/README.md`](./web/public/avatar/README.md)。
素材缺失自动退回占位面板，不影响面试。

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
  orchestrator/   串联 agent + Redis 状态机 + Postgres 归档 + turn/filler 音频
  db/             Postgres 惰性连接 + ORM + repository
  cache/          Redis 惰性连接 + 会话热存储 + LLM/embedding/TTS 缓存
  vector_store/   Milvus Lite: questions + documents 两个 collection
  tts/            统一 TTS 调用点 (火山/Azure 路由; 未配置返 None, 前端退文字)
  stt/            流式 STT 抽象 + 火山 ASR 二进制 WS 协议客户端
  media_store/    面试录像归档 (本地盘起步, S3/MinIO 换实现即可; 只录不判)
  knowledge_pipeline/  md 语料解析 (知识管线)
  derivation/     反向出题 (KnowledgeChunk → DerivedQuestion)
  ingestion/      文档切片 + 向量化 + resume_sections 简历分段
  resume_parser/  PDF / DOCX / TXT / 图片(OCR) Resume 解析
  auth/           JWT + bcrypt + cookie 鉴权
  coverage.py     CompletionPolicy 用的 competency_coverage 计算
  main.py         Sprint 0 风格的写死输入跑通 demo

api/
  main.py         FastAPI 工厂, CORS / 异常映射 / 路由聚合
  routes/         auth · jobs · candidates · interviews · media · hr
                  · admin_upload · admin_drafts
  schemas.py      API 出入参 (DTO)

web/              Next.js 16 候选人端 + HR Dashboard
  .../session/    面试主界面: page + media(consent/avatar/PiP) + stt(录音转写)
                  + recorder(录制上传)
  public/avatar/  Tier B 数字人三态视频素材 (idle/talking/thinking)

scripts/          运维脚本 (seed_* / ingest_md_corpus / derive_questions
                  / cleanup_recordings)
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
| Media | `GET /media/config` · `GET /interviews/{id}/turns/{ref}/audio` · `GET /interviews/{id}/fillers/{i}/audio` · `WS /interviews/{id}/transcribe` · `POST /interviews/{id}/recordings` | 能力探测 / TTS 播报 / 过渡语音 / 转写代理 / 录制归档 |
| HR | `GET /hr/jobs` · `GET /hr/sessions/{id}` · `GET /hr/reports/{id}` · `PATCH /hr/reports/{id}/review` | Dashboard + 人工复核 |
| Admin | `POST /admin/upload-knowledge` · `GET /admin/datasets` · `PATCH /admin/drafts/{id}` · `POST /admin/drafts/{id}/approve` 等 | 语料上传 + 反向出题审核入库 |

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
- **媒体层是纯适配器**——面试官的嘴 = `TurnResult.prompt` 文本，候选人的答 =
  `submit_answer(text)`；TTS / STT / avatar / 录制任一环节失败都降级回文字问答，
  agent 内核不感知媒体
- **录像只录不判**——仅作 HR 复核素材（`media_ref` 溯源），打分路径绝不消费；
  转写文本候选人可校对后再提交，textarea 是唯一真相源

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
- ✅ 字母 sprint 系列 — 知识管线（语料 + 反向出题 + 审核）/ 简历分段 + 图片 OCR /
  Planner 主题匹配
- 🔨 Sprint 6 — 视频面试（5/6：consent 门 / TTS 播报 / 三态 avatar / 语音作答 /
  录制归档已落；Tier A 真口型待定）
- ⏳ Sprint 7 — 多模态评价（含合规护栏）

完整任务清单见 [`sprint.md`](./sprint.md)。
