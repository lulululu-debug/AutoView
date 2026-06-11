# AI Interview Platform

## 概述
一个多 agent 的 AI 视频面试平台。HR 上传 JD / 岗位要求 / 公司资料，
候选人面试前上传 Resume；系统结合 JD 与 Resume 自动生成面试计划、
执行多轮面试与追问、产出结构化评估报告。
最终目标是「招聘端 + 候选人端」双边 AI 面试基础设施。

面试题目分两类：
- **基础知识考察**：由 JD / 岗位要求驱动，考察岗位所需通用能力
- **项目/实习内容考察**：由 Resume 驱动，针对候选人具体项目/实习深挖

详细架构见 ARCHITECTURE.md。

## 技术栈（计划）
- Agent 核心：Python 3.11+
- LLM SDK：anthropic
- Agent 编排：自研轻量编排 / LangGraph（待定，先用自研保持可控）
- API 层：FastAPI
- 前端：Next.js + TypeScript（HR 端 + 候选人端）
- 数据库：Postgres
- 依赖管理：uv
- 实时媒体（后期）：WebRTC + 流式 STT/TTS（骨架阶段不实现）
- 多模态分析（扩展）：视觉/音频模型（骨架阶段不实现）

## 目录结构（计划）
- `src/agents/planner/`      — 规划 agent：JD + Resume → 面试计划（基础知识题 + 项目/实习深挖题）
- `src/agents/interviewer/`  — 面试官 agent：执行轮次、提问、追问
- `src/agents/evaluator/`    — 评估 agent：打分、结构化报告
- `src/agents/analyzer/`     — 多模态分析 agent（扩展，先留空目录）
- `src/orchestrator/`        — 编排器：会话状态、agent 间上下文传递
- `src/llm/`                 — LLM 调用统一封装
- `src/schemas/`             — 数据结构（InterviewPlan / Session / Report 等）
- `evals/`                   — 评估用例
- `api/`                     — FastAPI 接口（后期）
- `web/`                     — Next.js 前端（后期）

## 约定
- 所有函数带类型注解；数据结构统一用 pydantic 定义在 src/schemas/
- LLM 调用一律走 src/llm/ 封装，不要在 agent 里直接调 SDK
- 每个 agent 暴露一个清晰的入口函数，输入输出都是 schemas 里的类型
- 一次只实现/修改一个 agent，做完立刻验证并 commit
- agent 之间通过编排器传递上下文，不要 agent 直接互相调用

## 常用命令
（待骨架搭好后补充：安装、运行、测试、eval）

## 不要做的事
- 骨架阶段不要实现视频、实时语音、多模态分析，全部用文本/占位
- 不要把 API key 写进代码，用环境变量
- 不要在没有对应 eval 的情况下改 prompt 模板
- 多模态「眼神/语气」信号只能作为参考证据，绝不可作为自动淘汰的唯一依据
  （原因见 ARCHITECTURE.md 的合规与公平性章节）
