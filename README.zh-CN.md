# AutoView — AI 面试平台

> English version: [`README.md`](./README.md)

多 agent 的 AI 视频面试基础设施。HR 上传 JD 与公司资料,候选人上传简历;AutoView
随即自动生成面试计划、多轮执行并智能追问,产出结构化、可复核的评估报告——端到端贯通。

视频面试已可用:AI 面试官有脸、开口提问,候选人可语音作答(转写可校对再提交),
摄像头画面录制归档。**文字问答是永远的保底路径**——任一媒体环节失败,面试自动降级
而非中断。

<p align="center">
  <img src="./docs/screenshots/interview.png" alt="视频面试 —— AI 虚拟面试官开口提问、候选人摄像头画面、实时语音转写" width="720">
  <br>
  <em>视频面试:AI 虚拟面试官开口提问,候选人语音作答并实时转写。</em>
</p>

**深入阅读:** [架构](./ARCHITECTURE.md) · [评估体系](./EVALUATION.md) ·
[路线图](./sprint.md) · [Agent 升级](./AGENT_UPGRADES.md)

---

## 工作原理

```
JD + 简历 ──▶ Planner ──▶ Interviewer ⇄ Assessor ──▶ Evaluator ──▶ 报告 ──▶ HR 复核
                            (每题在线)                 (内容分 vs 软信号)
```

五个 agent 各司一职,由唯一的 **Orchestrator** 编排——agent 之间绝不互相调用。面试按
stage 推进(`self_intro → knowledge → project → scenario`);项目题在候选人自我介绍后,
结合简历 RAG **现场生成**,而非上来就凭简历瞎猜。

## 特性

- **自适应追问。** 每个回答产出结构化评估,带 Platt 校准的充分度分数。追问决策在校准
  概率空间中、受全局预算与 competency 信念门共同约束——证据不足处深挖,足够处即止。
  任何 LLM 调用失败一律降级启发式。
- **可复现 + 公平。** 题集在 plan 期一次固定,绝不中途动态补题。`overall` 只由内容分
  得出;软信号、信念、候选人记忆均为内部数据,绝不泄漏进报告或总分。
- **决策可审计。** 每个追问 / 结束 / 评估决策连同实际用到的阈值全量落库,并可确定性
  回放——golden trace diff 提供零 token 的回归覆盖(对齐 EU AI Act Art. 12)。
- **视频媒体层是纯适配器。** consent 门 → TTS 播报 → 三态 avatar → 流式 STT → 录制。
  厂商 key 不出后端;录像只存档、绝不打分。
- **检索支撑内容。** Milvus 题库 + 文档库,由知识管线喂养(md 语料 + 飞书文档导入 →
  切片 → 反向出题 → HR 审核)。Postgres 是真理之源,向量库随时可重建。
- **多租户 + 安全加固。** HR 自助注册、按 owner 隔离(越权访问返回 404),候选人输入
  文本全量做注入防御。
- **异步 + 高并发。** 上传后的重活跑在 RQ worker;per-session Redis 锁防串扰。实测 30 路
  并发面试 30/30 零失败,turn 延迟 p50 18ms。

## 质量体系

两套物理分离的评估:`evals/`——590 条结构护栏,跑在 stub 上(零 token、CI 安全);
`sim/`——真 LLM 效果评估(persona 仿真、对抗与公平性扰动、LLM-as-judge 金标校准、
RAG 指标、冻结回归基线)。改任何 prompt 或 policy 阈值都需 golden trace diff + 对应
校准复跑。详见 [`EVALUATION.md`](./EVALUATION.md)。

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11+ · FastAPI · pydantic v2 |
| 前端 | Next.js 16 · React 19 · Tailwind 4 |
| LLM / Embedding | OpenAI(`gpt-4o-mini` + `text-embedding-3-small`) |
| 存储 | PostgreSQL(真理之源)· Redis(状态/缓存/锁)· Milvus(向量) |
| 异步 | RQ worker(可选) |
| 鉴权 | JWT + httpOnly cookie + bcrypt |
| 媒体 | TTS / 流式 STT 按 region 路由(火山 / Azure)· MediaRecorder |

---

## 快速开始

```bash
# 1. 安装
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. 本机服务
brew services start postgresql redis
createdb interview && createdb interview_test
docker compose up -d milvus          # 可选:高并发时用独立 Milvus

# 3. 配置——复制 .env.example 为 .env 按需填写。
#    OPENAI_API_KEY 可留空;骨架走 stub,不调真实 LLM 仍可跑通。

# 4. 运行
uvicorn api.main:app --reload        # API + OpenAPI 文档在 /docs
cd web && npm install && npm run dev # 前端 http://localhost:3000
```

**可选:** `python -m src.jobs.worker`(`JOBS_QUEUE_ENABLED=1` 时的 RQ worker)· 媒体
环境变量(`TTS_PROVIDER` / `STT_PROVIDER` + 厂商 key)开启语音;全不配 = 纯文字面试。
各变量语义与回退行为见 `.env.example`。

种子与运维脚本(`seed_questions`、`ingest_md_corpus`、`derive_questions`、
`record_golden_traces` …)在 `scripts/`,各脚本头注释即用法。

---

## 项目结构

```
src/
  schemas/        pydantic 数据契约(全部 agent 输入输出类型)
  llm/ embeddings/ OpenAI 唯一调用点;stub 回退;注入净化
  agents/         planner · interviewer · assessor · evaluator · analyzer
  orchestrator/   agent 编排 + Redis 状态机 + 会话锁 + PG 归档
  trace/          决策 trace 收集 + 确定性回放
  vector_store/   Milvus:questions + documents(lite / standalone 双模)
  knowledge_pipeline/ + derivation/   语料解析 + 反向出题
  db/ cache/ auth/ tts/ stt/ media_store/ connectors/   基础设施与适配器
api/              FastAPI:HTTP 入口、校验、异常映射
web/              Next.js 候选人端 + HR Dashboard
evals/            结构护栏(stub,零 token)
sim/              真 LLM 效果评估
scripts/          种子与运维脚本
```

## 设计约束

- Agent 只经 Orchestrator 通信;LLM/embedding 一律走 `src/llm` / `src/embeddings`,
  每个 LLM 调用都带 timeout + 确定性 fallback。
- 内部信号(评估数字、信念、候选人记忆、trace)绝不进 HR 明细或候选人视图;候选人
  不接触自己的报告。
- 媒体层是纯适配器——任一环节失败降级回文字;可校对的转写是唯一真相源。

## 路线图

- ✅ 核心闭环 → 持久化 → API → RAG → 候选人端 → HR Dashboard
- ✅ 双 track 与 stage 化 · 在线 Assessor · CompletionPolicy · 校准
- ✅ Agent 升级:注入防御 · 决策 trace + 回放 · 校准信念驱动追问 · 跨 stage 候选人记忆 ·
  rubric 化打分
- ✅ 异步队列 + 独立 Milvus + 会话锁 + 并发压测
- 🔨 视频面试(真口型数字人待定)
- ⏳ 多模态评价(含合规护栏)· 部署

完整任务清单见 [`sprint.md`](./sprint.md)。
</content>
