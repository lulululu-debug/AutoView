---
title: 大模型 Agent（工具调用篇）
description: Agent 工具调用面试知识点：Function Call 机制与学习方式、MCP 协议（组件/通信模式/Streamable HTTP）、A2A 协议、Tool Calling / Function Calling / MCP / A2A 对比
category: 大模型
tag:
  - Agent
  - Function Call
  - MCP
  - A2A
---

# 大模型 Agent（工具调用篇）

## Function Call（函数调用）

### 什么是 Function Call

在 AI Agent 中，Function Call（函数调用）指的是大模型在推理过程中，通过生成一个结构化的调用请求（如 JSON 格式），去触发外部函数/接口/服务，以获取额外信息或执行操作。

例子：用户问"帮我查一下明天北京的天气"，大模型会生成一个函数调用：

```json
{
  "name": "get_weather",
  "arguments": {
    "location": "北京",
    "date": "2025-09-19"
  }
}
```

Agent 执行这个函数，拿到结果，再返回给用户。本质上：Function Call 就是把自然语言请求转化为函数调用参数，让大模型可以和外部世界交互。

### 调用的前提：工具 Schema 与结构化映射

大模型调用的关键点在于"接口规范化 + 自然语言到结构化数据的映射"：

1. 工具描述（Schema/Prompt）：给大模型定义工具的名字、功能描述、输入参数类型。示例（OpenAI 的 Function Schema）：

```json
{
  "name": "get_weather",
  "description": "获取指定地点和日期的天气",
  "parameters": {
    "type": "object",
    "properties": {
      "location": {"type": "string"},
      "date": {"type": "string"}
    },
    "required": ["location", "date"]
  }
}
```

2. 模型理解与映射：大模型看到用户输入 → 结合工具 Schema → 自动生成合适的参数。
3. 执行与反馈：Agent 执行工具 → 返回结果 → 模型继续对话。

总结：Function Call 就是通过 Schema 定义，把"外部工具"翻译成"大模型能读懂的任务接口"，让模型用自然语言来决定如何调用。

### 大模型学习 Function Calling 能力的方式

Function Calling 的能力，本质是将自然语言映射为结构化的函数调用格式。模型需要在理解用户意图的同时，结合工具 Schema，正确输出函数名和参数。学习方式主要包括四类：

1. 微调 / 指令微调（Instruction Tuning）：在训练集中加入"自然语言请求 → 函数调用 JSON"的映射样例，让模型学会把请求转化为结构化参数。输入是自然语言指令（如"查一下纽约今天的天气"），输出是对应的函数调用 JSON。优点：学习效果稳定，模型能较好泛化到相似任务，适合需要高一致性的 API 调用场景。缺点：需要人工构建或合成大量高质量训练样本；灵活性有限，新增工具需要重新微调。

2. 少样本提示（Few-shot Prompting）：在 Prompt 中直接提供一些"自然语言 → Function Call"的示例，模型通过类比生成调用。优点：无需额外训练、立即可用，灵活性高，可以随时添加新工具示例。缺点：模型容易出现格式错误（JSON 拼写错误、缺少参数）；上下文过长时示例消耗 Token。

3. 强化学习（RLHF / RLAIF）：让模型在工具调用场景下交互，并基于反馈信号进行优化——正确调用给奖励，错误调用/参数错误/无关调用给惩罚。RLHF 用人工标注调用是否正确，RLAIF 使用另一个模型或规则系统自动评估调用效果。优点：可以显著提升调用准确率和鲁棒性，模型能在复杂场景中学会"何时调用、调用什么工具、参数怎么填"。缺点：训练开销大；需要设计合理的奖励函数，否则可能学到投机行为。

4. 工程侧优化——结构化输出约束（Structured Output / JSON Schema 约束）：在推理时强制模型生成符合 Schema 的 JSON（如 OpenAI 的 function_call 参数、Anthropic 的 tool_use 机制）。本质上不是训练方式，而是推理阶段的输出约束，能减少格式错误。

工程落地的常用方案：Few-shot Prompting + Schema Constraint 往往是成本最小、最容易部署的方案——无需微调模型（避免数据收集、标注和训练的高昂成本）；格式可靠（Schema 约束在推理阶段强制输出合法 JSON，减少工具调用失败）；灵活性高（随时新增或修改工具，只需更新示例和 Schema，适合快速迭代）。

## MCP 协议

### 什么是 MCP、核心价值

MCP（Model Context Protocol）是由 Anthropic（Claude 系列模型）提出的一种开放协议，用于让大模型与外部工具、数据源和服务进行安全、标准化的交互。定位：它是 AI Agent 与外部世界的"通信桥梁"，可以把它理解成"大模型的插件系统"或"大模型的 USB 标准"。

MCP 通过解耦大模型与外部工具、提供统一的调用接口和安全控制，解决了传统工具调用的不一致性和安全风险：

- 统一工具调用接口与标准化：避免 ad-hoc JSON 格式不一致，工具提供方只需按照 MCP 协议实现接口，就能被任何支持 MCP 的客户端（如 Claude Desktop、集成 MCP 的 IDE）直接使用。
- 增强上下文能力：让大模型能够动态、实时获取运行时所需信息（如读取文件、查询数据库），不再仅依赖训练知识或有限的聊天上下文。
- 提升安全性与可控性：用户或管理员可以精确控制大模型可访问的工具和数据源，杜绝未经授权访问，同时所有通信清晰可审计。
- 提高可扩展性：开发者可以快速将已有应用包装成 MCP 服务，实现跨平台调用和复用。

在 AI 大模型系统中的作用：标准化 Tool Calling（为 Function Calling、API 调用等提供统一格式）、跨平台互通（LangChain、Spring AI、Claude 等不同 Agent 框架都可以使用 MCP 接入同一套工具）、增强安全性（内置权限控制和审计机制）、提高开发效率（应用只要实现 MCP 服务接口，就能被多个大模型直接调用）。

### MCP 的核心组件

MCP 架构遵循客户端-服务器模型，标准的核心组件有两个：Client（客户端）和 Server（服务器），加上二者之间的传输层。

1. Client（客户端）：大模型（或 AI 应用）所在的一方，是工具的"消费者"和调用方。功能：管理与用户的对话；根据对话内容向 Server 发起工具调用请求；接收 Server 返回的结果，并将其提供给大模型以生成最终回复。例子：Claude Desktop、Cursor IDE、任何集成了 MCP Client SDK 的 AI 应用。

2. Server（服务器）：工具和数据的提供方，是工具的"生产者"。功能：向 Client 注册（Register）自己提供的工具（Tools）和资源（Resources）；接收并处理 Client 发来的请求；执行具体的操作（如执行代码、查询数据库、读取文件）；将结构化的结果返回给 Client。例子：一个提供"天气预报"工具的 Server、一个提供"数据库查询"工具的 Server。

3. 传输层（Transport）：Client 与 Server 之间的数据交换通道（这是一个通信通道，而非独立组件）。它定义了 Client 和 Server 之间如何交换数据，负责将协议消息可靠地从 Client 发送到 Server 并返回执行结果。主要通信模式：stdio（标准输入输出，本地进程间通信）、SSE（基于 HTTP 的单向事件流，适合远程通信）、Streamable HTTP（基于 HTTP 的可流式传输，支持长连接与分块传输）。

| 组件 | 角色 | 功能 | 例子 |
| --- | --- | --- | --- |
| Client | 大脑 | 决策何时调用何种工具，并处理结果 | Claude Desktop、你的 AI 应用 |
| Server | 手脚 | 提供具体工具的实现和执行 | 文件操作 Server、数据库查询 Server |
| Transport | 神经 | 在 Client 和 Server 之间传递消息 | stdio、SSE、Streamable HTTP |

### MCP 的三种通信模式

1. stdio（标准输入输出）：使用操作系统的标准输入/输出流（stdin/stdout）进行通信，常用于本地进程间的消息传递。工作原理：Client 将请求序列化为 MCP 消息，通过标准输出发送到 Server；Server 解析消息、执行工具操作，并通过标准输出返回结果。特点：低延迟、通信直接、简单可靠，无需额外网络协议，但仅适合同机环境。适用场景：本地 Agent 与本地工具服务交互、轻量级原型开发或测试。

2. SSE（Server-Sent Events，服务器推送事件）：基于 HTTP 协议的单向事件流通信，Server 可以主动向 Client 推送消息。工作原理：Client 向 Server 建立 SSE 连接，Server 将事件流推送给 Client（如函数调用结果、工具执行状态），Client 实时接收事件并处理。特点：单向推送，适合远程通信，支持实时更新、无需 Client 轮询。适用场景：远程 Agent 调用 Server 上的工具、需要实时反馈或长任务通知的场景。

3. Streamable HTTP（可流式 HTTP）：基于 HTTP 协议的流式数据传输，支持大模型生成输出实时返回，或长时间任务的逐步返回结果。工作原理：Client 发起 HTTP 请求到 Server；Server 按块（chunk）发送数据流，Client 可边接收边处理；支持长连接和流式更新，实现连续推理或多步骤调用。特点：可扩展性高，适合大规模或远程调用；支持长任务和实时输出，降低延迟感知；兼容现有 HTTP 基础设施（负载均衡、监控等）。

| 通信模式 | 特点 | 优点 | 缺点 | 适用场景 |
| --- | --- | --- | --- | --- |
| stdio | 本地标准输入输出 | 低延迟、简单 | 仅本地可用 | 本地 Agent 与工具交互、原型开发 |
| SSE | HTTP 单向事件流 | 实时推送、远程可用 | 单向、不支持双向交互 | 远程调用、实时反馈 |
| Streamable HTTP | HTTP 双向流式传输 | 可流式输出、长任务支持 | 实现稍复杂 | 多轮推理、长文本/大模型输出 |

### Streamable HTTP 与 SSE 的关系与区别

在 MCP 中，Streamable HTTP 是一种新的通信机制，旨在替代之前的 HTTP+SSE 模式。HTTP+SSE 传输协议是 2024-11-05 版本起使用的，最新的 2025-06-18 协议修订版已明确用 Streamable HTTP 替代它。

二者的关系：在之前的 MCP 版本中，客户端与服务端之间的通信需要通过两个独立的 HTTP 端点进行——`/sse` 用于建立持久的 SSE 连接（服务端向客户端推送消息），`/sse/messages` 用于客户端发送 JSON-RPC 请求。这种架构有效但增加了系统的复杂性和维护成本。Streamable HTTP 模式将这两个端点合并为一个统一的 MCP 端点（例如 `https://example.com/mcp`），同时支持 HTTP POST 和 GET 请求；服务端可以选择性地使用 SSE 特性，通过 `Content-Type: text/event-stream` 响应头向客户端推送多条消息。

Streamable HTTP 的工作机制：

1. 客户端请求：客户端向 MCP 端点发送 HTTP POST 请求，携带 JSON-RPC 消息。
2. 服务端响应：如果立即有结果，使用 `Content-Type: application/json` 返回 JSON 结果；如果需要异步处理，使用 `Content-Type: text/event-stream` 开始 SSE 流式传输。
3. 消息传递：在 SSE 流中，服务端可以发送多条 JSON-RPC 响应、通知或请求，客户端通过监听该流接收数据。

这种机制允许服务端在同一个连接中，既能响应客户端的请求，也能主动向客户端推送消息，实现了双向通信。Streamable HTTP = 一种能同时承载 JSON 短响应和 SSE 长流的统一 HTTP 通道。

| 特性 | SSE（旧模式） | Streamable HTTP（新模式) |
| --- | --- | --- |
| 连接数量 | 需要 /sse 和 /sse/messages 两个独立端点 | 只需一个统一的 MCP 端点（如 /mcp），降低架构复杂度 |
| 通信方向 | 单向：服务端 → 客户端，客户端只能另开 HTTP 请求发送指令 | 伪双向：同一长连接中既能响应客户端请求，又能服务端主动推送，甚至服务端向客户端发起 JSON-RPC 请求 |
| 响应类型 | 固定为 text/event-stream，只能流式推送事件 | 支持 application/json（一次性 JSON 响应）和 text/event-stream（流式多条推送）两种 |
| 状态管理 | 需维护两个连接，状态同步复杂，易出现消息不同步或丢失 | 单一端点管理请求和流，服务端更容易跟踪会话上下文 |
| 扩展性 | 功能单一，只适合事件推送 | 支持同步返回、异步流式返回、事件驱动推送、反向请求 |
| 实现复杂度 | 两个端点、多连接管理，协议简单但运维成本高 | 统一端点，协议稍复杂，但减少多端点维护成本 |
| 性能 | 两条连接同时维护，资源消耗较多 | 一个长连接即可，连接复用率更高，节省带宽和资源 |
| 适用场景 | 简单的单向事件推送（日志流、行情、通知） | 需要双向通信和实时交互的复杂场景（Agent 工具调用、任务进度跟踪） |

总结：SSE 更像是"订阅一个消息广播频道"，只能接收服务端消息，适合轻量级事件通知；Streamable HTTP 更像是"一个支持双向交流的会话管道"，在同一个端点里既能响应请求、又能实时推送事件、还能发起反向请求，更适合 AI Agent 这种复杂交互场景。注意：Streamable HTTP 不是与 SSE 割裂的独立技术，它复用了 SSE 的流式传输特性，把"HTTP 连接长时间开放、流式传数据"作为统一端点的可选响应方式。

### MCP 工作流程（查天气示例）

以官方 Build an MCP server 的查询天气为例：

1. 启动（Startup）：客户端（Claude Desktop / IDE 插件）启动时读取配置，发现需要连接远程的 Weather MCP Server（如 `https://api.weather.com/mcp`），通过 Streamable HTTP 建立通信——所有请求都发往同一个 MCP 端点，既支持一次性 JSON 响应，也支持 SSE 流式响应。
2. 建立连接：
   - 初始化（initialize）：Client 发送 `POST /mcp` 请求 initialize，Server 返回 JSON 响应，告知相关参数（主要是一个 mcp-session-id）。
   - 通知初始化完成：客户端收到初始化响应后，触发一次 `notifications/initialized`，标记 MCP Server 已初始化。
   - 获取工具列表：客户端再进行一次 `tools/list`，获得 MCP Server 所有可用工具的列表。
3. 模型智能判断：用户输入"帮我查一下今天上海的天气"，模型解析语义，决定调用 getWeather 工具，参数为 `{ "location": "Shanghai", "date": "2025-09-22" }`。
4. 工具调用（Tool Call）：Client 通过 `POST /mcp` 发送 JSON-RPC 请求（method: getWeather + params）。
5. 执行与响应：
   - 情况 A（一次性 JSON 响应）：服务端能立即返回结果时，用 `Content-Type: application/json` 返回天气数据。
   - 情况 B（流式 SSE 响应）：天气数据需要异步获取时，Server 返回 `Content-Type: text/event-stream` 流式响应，依次推送"正在查询气象局数据..."、"已获取到温度数据：28°C"、最终结果，最后发送 `event: end`。客户端实时接收流式更新，最终拿到完整结果。
6. 最终回复：Client 将结果作为上下文传入模型，模型整理后返回给用户："今天上海是晴天，气温大约 28 度，适合出行。"
7. 关闭（Shutdown）：会话结束时客户端关闭连接；如果是 SSE 流式模式，服务端会主动发送 `event: end` 关闭流。

### 已有 API 接口转为 MCP 服务的流程

一般流程：

1. 抽象功能接口：找出应用的核心功能点（例如查询库存、提交订单）。
2. 定义 Schema：用 MCP 定义接口描述（输入参数、输出格式、用途）。
3. 实现 MCP Server：在现有应用之上封装一个 MCP Adapter，把原有 API 转成 MCP 兼容接口。
4. 部署与注册：部署 MCP 服务端，并在 Agent 框架中注册该服务。
5. 测试与验证：用 Agent 调用测试，确保功能可用、权限安全。

例如一个电商应用原本有 REST API `GET /inventory?product_id=123`，可以封装为如下 MCP 服务，就能被任何支持 MCP 的大模型直接调用：

```json
{
  "name": "get_inventory",
  "parameters": {"product_id": "string"},
  "returns": {"stock": "integer"}
}
```

## A2A 协议

### 什么是 A2A、五大设计原则

A2A 协议（Agent-to-Agent Protocol）是一种专门设计的多智能体通信协议，其本质是 AI Agent 之间的标准化协议，是驱动多智能体生态系统的核心通信框架。作用：

- 支持多 Agent 之间任务分工与协作，类似人类团队协作。
- 允许 Agent 调用其他 Agent 的能力，将其视为工具。
- 可以和 MCP 协议结合，让 Agent 既能调用工具，也能调用其他智能体。

应用场景示例：一个客服 Agent 接到用户问题后，把技术类问题转给技术 Agent、把账单类问题转给财务 Agent，再整合答案返回给用户；在科研协作中，不同 Agent 分别负责数据收集、模型训练和结果分析，然后汇总报告。

A2A 协议的五大设计原则：

1. 让 Agent 能以自然、非结构化的方式进行协作：即使它们不共享记忆、工具和上下文，也能进行真正的多 Agent 协作，目标是避免把 Agent 简单地限制为"工具"。
2. 协议基于现有的流行标准：构建在 HTTP、SSE、JSON-RPC 等现有标准之上，便于与企业现有 IT 基础设施集成。
3. 支持企业级认证和授权：默认安全，和 OpenAPI 的身份验证与授权方案具备同等效力。
4. 支持长期任务：灵活设计，既能处理快速任务，也能支持数小时乃至数天的深度研究任务，执行过程中能够持续提供实时反馈、通知和状态更新。
5. 支持多模态：不仅限于文本，还支持音频和视频流等多种模式。

### 核心架构与基本通信要素

核心架构：

- 用户（User）：发起请求或定义需要 AI 代理协助的目标的终端使用者（人或自动化服务）。
- A2A 客户端（Client Agent）：代表用户的应用、服务或其他 AI 代理，使用 A2A 协议发起通信。
- A2A 服务器（Remote Agent）：实现 A2A 协议 HTTP 接口的 AI 代理或代理系统，接收请求、处理任务并返回结果或状态更新。对客户端而言，它是一个不透明的黑箱，内部实现、记忆或工具不对外暴露。

基本通信要素：

| 要素 | 描述 | 关键作用 |
| --- | --- | --- |
| Agent Card | 描述代理身份、能力、端点、技能和认证需求的 JSON 元数据文档 | 让客户端发现代理并安全、有效地交互 |
| Task | 由代理发起的有唯一 ID 的有状态工作单元，拥有定义好的生命周期 | 便于跟踪长时任务，支持多轮交互与协作 |
| Message | 客户端与代理之间单轮通信，包含内容和角色（"user"或"agent"） | 传递指令、上下文、问题、答案或状态更新 |
| Part | 消息或制品内部的基本内容容器（如 TextPart、FilePart、DataPart） | 为代理在消息和制品中交换多种内容类型提供灵活性 |
| Artifact | 任务执行期间代理生成的有形输出（文档、图片、结构化数据等） | 提供具体的工作成果，确保结构化、可检索的输出 |

### 交互机制与工作原理

三种交互机制：

- 请求/响应（轮询）：客户端发送请求，服务器返回响应；对长时任务，客户端定期轮询获取更新。
- 服务器发送事件（SSE）流式：客户端建立流式连接，实时接收增量结果或状态更新。
- 推送通知：对极长任务或断连场景，服务器可主动向客户端提供的 webhook 发送异步通知。

A2A 协议的核心作用是让客户端代理与远程代理之间能够顺利沟通：客户端代理负责创建并下发任务，远程代理根据任务提供信息或执行操作。整个链路为"代理卡片发现能力 → 任务创建与管理 → 协作通信 → 用户体验协商"，支持多代理协作、长时任务执行、实时反馈和状态更新、多模态内容传输。关键环节：

1. 能力发现（Capability Discovery）：每个代理都有一张"代理卡片"（Agent Card），以 JSON 格式描述该代理能做什么。客户端代理读取这些卡片，选择最合适的远程代理执行任务，通过 A2A 协议建立通信。
2. 任务管理（Task Management）：任务是 A2A 的核心执行单元，每个任务都有一个协议定义的任务对象和完整的生命周期——有的任务可以立即完成，长时间运行的任务需要持续沟通和状态同步。任务完成后会产生一个工件（Artifact），即最终的执行结果。
3. 协作能力（Collaboration）：支持代理之间直接通信，消息中可包含上下文、回复、工作结果或用户指令，便于多个代理协同完成更复杂的任务。
4. 用户体验协商（User Experience Negotiation）：每条消息可包含多个部分（Parts），每个部分代表一个内容片段（例如生成的图片）并有明确的类型，客户端和远程代理可根据类型决定最佳展示方式（如以 iframe、视频、网页表单等形式呈现）来优化用户体验。

### 多 Agent 工作流程示例

1. Agent A 接收用户请求：如"生成一份市场分析报告"。
2. Agent A 拆解任务：拆成"收集数据""分析趋势""撰写报告"。
3. 发现并选择目标 Agent：Agent B 专注数据收集、Agent C 专注数据分析、Agent D 专注生成文本报告。
4. 发送任务请求：Agent A 向 B、C、D 发送子任务请求，采用 A2A 标准消息格式。
5. 执行与结果返回：B、C、D 执行任务，并将结果返回给 Agent A。
6. 汇总与输出：Agent A 整合结果，生成最终报告。
7. 审计记录：所有消息、任务执行结果和决策过程均被记录，便于追踪。

## 协议对比

### Tool Calling vs Function Calling

这是一个"概念范畴"与"技术实现"的区别，是广义与狭义的区别：Function Calling 是 Tool Calling 的一种具体实现。

- Function Calling = 技术机制（函数级别的接口调用），更强调技术实现层面：大模型生成结构化调用参数，触发函数/接口。常见于 OpenAI、Anthropic 提供的接口（官方 API 里叫 function calling）。
- Tool Calling = 应用概念（更广义的工具使用，包括函数调用、API 调用、RPA 等），更强调应用语境：大模型调用的"工具"不仅限于函数，还可以是搜索引擎、数据库、代码执行器、RPA、浏览器插件等。在 Agent 框架（LangChain、AutoGPT）中常用"Tool Calling"来表示。

| 维度 | Tool Calling（工具调用） | Function Calling（函数调用） |
| --- | --- | --- |
| 范畴 | 广义概念，指 AI Agent 使用任何外部能力的抽象行为 | 狭义实现，是 Tool Calling 的一种具体技术实现方式 |
| 表现形式 | 概念性的，不特指具体技术；工具可以是函数、API、插件、甚至物理设备 | 特指由 LLM 生成一个结构化的 JSON 请求来调用一个内部或外部函数 |
| 语境 | 常用于 AI Agent 讨论的高层架构中，描述其能力边界 | 常用于模型 API 和开发的具体上下文中（如 OpenAI API） |
| 关系 | Tool Calling 包含 Function Calling | Function Calling 是 Tool Calling 最主流的一种子集和实现方式 |
| 类比 | "出行"（一个抽象目标） | "驾驶汽车"（实现"出行"目标的一种具体方式），其他方式还有坐火车、骑自行车 |

结论：Function Calling 是一种具体的、流行的技术，用于实现 Tool Calling 这个概念。"Agent 支持 Tool Calling"是在描述它的能力；"通过 Function Calling 来实现 Tool Calling"是在说明用的技术方法。

### Function Call vs MCP

这是一个"功能"与"协议"的区别，是微观实现与宏观框架的区别。

| 维度 | Function Call（函数调用） | MCP（Model Context Protocol） |
| --- | --- | --- |
| 本质 | 一个具体的技术动作/机制 | 一个协议、标准、框架 |
| 范围 | 单体 Agent 内部的决策机制：LLM 如何决定调用哪个函数及其参数 | 跨进程、跨环境的通信规范：定义了 Agent（客户端）与工具（服务器）之间如何通信 |
| 关系 | Function Calling 是 MCP 协议中传输的"内容" | MCP 是 Function Calling 得以安全、标准化运行的"管道"和"规则" |
| 核心问题 | 解决"如何想"：LLM 如何将用户指令转化为结构化的动作意图 | 解决"如何做"：如何安全、可靠、标准化地执行这个动作意图 |
| 类比 | "说出一句命令"（例如"打开客厅的灯"） | "一整套通信系统"（对讲机设备、通信频率、呼叫协议、身份验证，确保命令能被正确的设备听到并安全地执行） |

结论：使用 Function Calling 的能力来生成一个调用工具的请求（JSON）；使用 MCP 来定义、管理、传输和执行这个请求，MCP 使 Function Calling 变得更加安全、可扩展和标准化。总结：Function Call 是 MCP 中的最小执行单元，MCP 提供了规范、权限和管理机制，Function Call 更偏"调用行为本身"。

### MCP 协议 vs A2A 协议

| 特性 | MCP 协议 | A2A 协议 |
| --- | --- | --- |
| 作用对象 | 大模型与外部工具/服务 | Agent 与 Agent 之间 |
| 核心功能 | 标准化工具调用、增强上下文、权限控制 | 多 Agent 协作、任务分发、通信协调 |
| 安全性 | 工具访问权限、沙箱、审计 | Agent 权限、消息加密、审计 |
| 交互形式 | Function Call / Tool Call | 消息传递、任务请求、结果返回 |
| 典型场景 | 单 Agent 调用数据库、API、搜索引擎 | 多 Agent 协作完成复杂任务 |

总结：MCP 是大模型与工具的交互协议，A2A 是 Agent 之间的协作协议，两者可以结合使用——Agent 可以通过 MCP 调用工具，也可以通过 A2A 调用其他 Agent，实现"Agent + 工具"的组合能力。用 MCP 来给一个 Agent 配备工具包（锤子、尺子、计算器）；用 A2A 来让这个 Agent 和其他专业的 Agent（木匠 Agent、会计师 Agent）一起团队作业。在一个复杂的 Agent 生态中，二者是共存的：一个 Agent 可以通过 A2A 协议接收任务，然后使用 MCP 协议调用各种工具来完成任务。

### 四个概念的层次关系总结

- Tool Calling：最上层的抽象概念（目标：使用工具）。
- Function Calling：实现上述概念的一种主流技术机制（方法：生成 JSON）。
- MCP：为 Function Calling（及其他工具调用）提供标准化、安全、可扩展的通信框架（基础设施：协议和管道）。
- A2A：另一个维度的协议，规范了多个具备 Tool Calling 能力的 Agent 之间如何协作。

最终，一个强大的 AI Agent 系统往往是这些技术的结合：多个 Agent（A2A）通过标准协议（MCP）安全地使用工具（Tool Calling），其核心实现机制是函数调用（Function Calling）。
