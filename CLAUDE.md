# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AI Interview Platform

## 概述
多 agent 的 AI 视频面试平台。HR 上传 JD / 岗位要求 / 公司资料，候选人面试前上传 Resume；
系统结合 JD 与 Resume 自动生成面试计划、执行多轮面试与追问、产出结构化评估报告。
最终目标是「招聘端 + 候选人端」双边 AI 面试基础设施。

面试题目分两类，在 `Question.category` 中显式区分，与 `Question.type`（题目风格）正交：
- **KNOWLEDGE**：基础知识考察，由 JD / 岗位要求驱动
- **PROJECT_EXPERIENCE**：项目/实习深挖，由 Resume 驱动

进度：Sprint 0 骨架（纯文本主链路）已跑通；Sprint 1 持久化与会话态进行中（详见 sprint.md）。
完整架构与合规约束见 ARCHITECTURE.md，特别是第 7 节多模态评价的硬约束。

## 技术栈
- Python 3.11+，pydantic v2
- **openai**（LLM chat + embedding，单一 provider；Sprint 3 起从 anthropic 切过来 consolidate key/计费）
- SQLAlchemy 2.0 + psycopg3（Postgres，Sprint 1）
- redis-py（Redis 热存储，Sprint 1）
- pymilvus + milvus-lite（向量存储，Sprint 3）
- FastAPI（HTTP API，Sprint 2）
- Next.js / WebRTC 是后续 sprint 的事，目前不引入

## 常用命令
```bash
source .venv/bin/activate
pip install -e .            # 装依赖（pyproject.toml 已声明）

python -m src.main          # Sprint 1 起：需要 Redis + Postgres 跑（无 API key 仍会进 stub 分支）

uvicorn api.main:app --reload    # Sprint 2 起：HTTP API，热重载；/docs 看 OpenAPI

brew services start postgresql   # 本机外部服务（macOS）
brew services start redis
brew services stop redis         # 停 Redis
```
环境变量见 `.env.example`：`OPENAI_API_KEY` / `OPENAI_CHAT_MODEL` /
`OPENAI_EMBEDDING_MODEL` / `OPENAI_BASE_URL` / `POSTGRES_URL` / `REDIS_URL` /
`MILVUS_LITE_URI` / `SESSION_TTL_SECONDS` / `LLM_CACHE_TTL_SECONDS` /
`EMBEDDING_CACHE_TTL_SECONDS`。运行时按需读取，缺哪个就退到对应的回退分支。

跑 eval（stdlib unittest，无第三方依赖）：
```bash
python -m unittest evals.test_skeleton                            # 全部
python -m unittest evals.test_skeleton.ComplianceInvariantTests   # 单类
python -m unittest discover -s evals                              # discover
```
`evals/` 里所有 TestCase 都被强制走 LLM stub（清掉 `OPENAI_API_KEY`），保证结构性护栏快、稳、不烧 token。
需要 PG+Redis 的端到端 TestCase 在缺 env 时自动 skip。

**坑提醒**：`pymilvus.settings` 在 import 时调 `load_dotenv()` 自动把 `.env` 塞回 `os.environ`。
所以 import 了 vector_store 的 eval（如 `test_seed_questions`、未来的 Planner RAG eval），
模块顶 pop 太早，必须在 setUp 里 pop 才稳。test_skeleton 不 import pymilvus，模块顶 pop 仍然有效。

## 代码结构（已实现部分）
- `src/schemas/` — 全部 pydantic 数据契约。Agent 输入输出都是这里的类型。
- `src/llm/` — OpenAI Chat Completions 的唯一调用点。`complete(system, user)` 同步入口。
- `src/embeddings/` — OpenAI Embeddings 的唯一调用点。`embed(text)` 同步入口。
- `src/agents/{planner,interviewer,evaluator,analyzer}/` — 每个 agent 一个 `__init__.py`，
  暴露一个动词函数：`plan` / `next_turn` / `evaluate` / `analyze`。
- `src/orchestrator/` — 串联 agent 的唯一入口。Agent 之间**绝不**互相调用。
- `src/db/` — Postgres 归档：`base.py` engine、`models.py` ORM、`repository.py` save/load。
- `src/cache/` — Redis：会话热存储 + LLM/embedding 响应缓存。
- `src/vector_store/` — Milvus Lite：questions 题库 + documents RAG 资料切片。
- `src/main.py` — 写死 JD + Resume + 候选人回答跑通全链路的 demo 入口。
- `api/` — FastAPI 层（Sprint 2 起）：只做 HTTP 入口 + 校验 + 异常映射，业务下沉到 orchestrator。
- `scripts/` — 一次性运维脚本（如 `seed_questions.py` 填题库）。
- `evals/` — stdlib unittest，结构性 + 合规护栏 + API smoke。

后续 `web/`（Next.js）见 sprint.md。

## 关键设计模式（要看多文件才能拼出来）

### LLM / Embedding stub 回退
`src/llm/complete()` 在 `OPENAI_API_KEY` 未配置或 SDK 不可用时，返回前缀 `[stub] ...`
的占位文本。**每个调用 LLM 的 agent 都必须 `if llm.is_stub(text): return fallback`**，
让骨架在无 key 环境也产出真实可用的输出（而非占位字符串泄漏到结果）。
同款模式：`src/embeddings/embed()` 缺 key 时返回全零 stub 向量，`is_stub_vector()` 让
调用方在入 Milvus 前判断是否跳过（全零进库污染向量空间）。
新增 agent / 新增 LLM / embedding 调用点时保持这个模式。

### Postgres / Redis 惰性连接
`src/db` 和 `src/cache` 顶层 import **不**读环境变量、**不**建连接。
调用 `init_db()` / `save_session()` / `get_redis()` 等才真正连接；未配置 URL 时抛
`DatabaseNotConfigured` / `RedisNotConfigured`。新增持久化能力时保持这个模式。
注：Sprint 0 时 `src.main` 可以无 PG/Redis 跑通；Sprint 1 之后 orchestrator 强依赖
两者（状态机基于 Redis 读写），`src.main` 需要 PG+Redis 才能跑。

### 会话状态机基于 Redis
Orchestrator 三段式 API：`start_session` → `submit_answer*N` → `finalize`，
中途可用 `resume_session(session_id)` 重发当前待答提示（中断恢复）。Session 与 Plan
都在 Redis 里同 TTL 同生共死（`SESSION_TTL_SECONDS`）；`finalize` 时把 session+report
归档到 Postgres，并立刻从 Redis 删 session 和 plan。`run_interview()` 是这三段的便利
封装，保留 Sprint 0 风格的一把跑完。

### Agent 间通信只走 orchestrator
Agent 模块只 import `src.llm` 和 `src.schemas`，**不**互相 import，**不**接触 DB/Cache。
所有路由（planner → interviewer 循环 → analyzer → evaluator）以及与 Redis/PG 的交互
都在 orchestrator 内完成。新增 agent 时遵循同款：动词函数 + 输入输出都是 schemas 类型。

### 合规约束写进 schema
`EvaluationReport` 把 `content_scores`（内容维度，进总分）与 `performance_observations`
（表现维度，软信号）分开；`overall` 只由 `content_scores` 加权得出，不能依赖软信号。
任何新增表现/软信号永远走 `PerformanceObservation`，永远不进 `DimensionScore`。
背景见 ARCHITECTURE.md 第 7 节。

### DB schema 取舍
嵌套结构（`history` / `answers` / `content_scores` / `performance_observations`）走 JSONB，
顶层可查询字段（`status` / `job_id` / `overall` / `needs_human_review`）提列。
有按嵌套字段查询/聚合的需求时再拆子表，不要一次拆完。
Sprint 1 阶段不引 Alembic，用 `Base.metadata.create_all`；schema 真的开始演进时再切。

## 约定
- 所有函数带类型注解；数据契约一律放 `src/schemas/`，不在 agent 里另起 dataclass
- LLM 调用一律走 `src/llm/`，embedding 调用一律走 `src/embeddings/`，不要在 agent 里直接 import `openai`
- 每个 agent 暴露一个清晰的入口函数，输入输出都是 schemas 里的类型
- 一次只做 sprint.md 里的一个 task，做完立刻验证并 commit，不批量推进
- 改 prompt 模板前确认对应 eval 存在（eval 尚未引入时先记账，Sprint 1 末补上）

## 不要做的事
- 骨架阶段不要实现视频、实时语音、多模态分析，全部用文本/占位
- 不要把 API key 写进代码（包括 `.env.example`），用环境变量
- 不要在没有对应 eval 的情况下改 prompt 模板
- 多模态「眼神/语气」信号只能作为参考证据，绝不可作为自动淘汰的唯一依据
  （原因见 ARCHITECTURE.md 第 7 节）
