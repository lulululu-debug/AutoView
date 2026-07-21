---
title: 大模型 Agent Skills
description: Agent Skills 面试知识点：概念与工作机制、SKILL.md 规范、创建与编写原则、工程实践、描述优化与触发评估、输出质量评估、客户端实现、企业级治理
category: 大模型
tag:
  - Agent
  - Skills
  - LLM
  - 上下文工程
---

# 大模型 Agent Skills

## Agent Skills 概念与工作机制

### Agent Skills 的定位与为什么需要

Agent Skills 的定位非常直接：Agent Skills 是一种给 AI agents 增加新能力和专业知识的标准化方式。它不是某个模型的内置知识，也不是一次性 prompt，而是一套开放文件格式，让不同 agent 能通过同一种目录结构加载任务能力。

为什么需要 Agent Skills：Agents 本身能力越来越强，但在真实工作里通常缺少足够上下文。比如 agent 可能会写文档、分析数据、处理代码，但它不知道某个公司内部的审查流程、团队格式规范、数据分析管线、演示文稿模板或用户偏好。Skills 的作用就是把这些过程性知识（procedural knowledge）和公司、团队、用户特定上下文打包成可移植、可版本控制、按需加载的文件夹。

它带来的能力可以分成 3 类：

- 领域专业知识（Domain expertise）：Skill 可以捕获专门知识，例如法律审查流程、数据分析管线、演示文稿格式要求。这类知识往往不是通用模型天然知道的，也不是每次对话都适合临时解释。
- 可重复的工作流程（Repeatable workflows）：Skill 可以把多步骤任务转成一致、可审计的流程。比如一个数据分析 Skill 可以规定先检查输入文件、再清洗数据、再生成图表、最后写结论，这样每次执行都更稳定。
- 跨产品复用（Cross-product reuse）：Skill 可以构建一次，然后在任何兼容 Skills 的 agent 中使用。这是开放标准的核心价值：工作流资产不绑定到单一产品。

补充：Agent Skills 格式最初由 Anthropic 开发，后来作为开放标准发布，并被越来越多 agent 产品、团队和企业采用。它的定位类似一种 AI agent 工作流包格式，希望成为可被不同产品、不同客户端、不同开发者共同实现和扩展的标准，而不是某一家公司的封闭功能。

### Agent Skills 是什么

Agent Skills 是一种轻量、开放的格式，用于用专门知识和工作流扩展 AI agent 能力。一个 Skill 的核心是一个文件夹，里面必须有 SKILL.md。SKILL.md 至少包含两类信息：

- 元数据（metadata）：至少包括 name 和 description。
- 指令（instructions）：告诉 agent 如何执行某个具体任务。

Skill 还可以包含其他资源：

```text
my-skill/
├── SKILL.md
├── scripts/
├── references/
├── assets/
└── ...
```

最重要的理解是：Skill 是一个能力包，不是一段孤立提示词。SKILL.md 提供主说明；scripts/ 可以提供可执行代码；references/ 可以放补充文档；assets/ 可以放模板和资源。这样 agent 不只是"听到一段指令"，而是获得一个可导航、可执行、可复用的任务包。

### Agent Skills 如何工作（渐进式披露三阶段）

Agent Skills 通过渐进式披露（progressive disclosure）工作。agent 不会一开始读取所有 Skills 的完整内容，而是分 3 个阶段加载：

- 发现（Discovery）：agent 启动时只加载每个 Skill 的 name 和 description。这些信息足够 agent 判断某个 Skill 可能什么时候相关，但不会把完整指令塞进上下文。
- 激活（Activation）：当用户任务匹配某个 Skill 的 description 时，agent 才读取完整 SKILL.md，把其中的指令加载进上下文。
- 执行（Execution）：agent 按照指令执行任务，并在需要时运行 bundled code，或加载引用的文件。

这个机制解决的是上下文效率问题。完整指令只有在任务需要时才加载，所以 agent 可以"手边放很多 Skills"，但只消耗很少的上下文。真正执行时，agent 再逐步拉取细节。

一句话概括：metadata 负责发现，SKILL.md 负责激活后的主流程，脚本和资源负责执行时的深层支持。

### Skills 与 Prompt、MCP、Subagent、Hooks 的区别

它们都能影响 agent 行为，但解决的问题不同：

- Prompt 是一次性指令，适合当前对话里的即时要求，比如"用中文回答""把这段话改得更正式"。Prompt 的问题是生命周期短、复用性弱、每次都占上下文。如果某套流程会反复使用，或者需要脚本、模板、参考资料支持，就更适合沉淀成 Skill。
- Skill 是按需加载的任务能力包。它不是每次都塞进上下文，而是先通过 name 和 description 被发现，相关时再加载 SKILL.md 和必要资源。Skill 解决的是"agent 应该如何完成某类可重复任务"的问题。
- MCP 解决的是"agent 能连接什么外部工具和数据源"的问题。MCP server 可以把数据库、GitHub、Figma、Notion、内部系统等能力暴露给 agent，更像工具和数据连接层。Skill 则告诉 agent 如何使用这些工具完成工作流。只有 MCP，agent 可能能访问工具，但不知道组织希望它怎么用；只有 Skill，agent 可能知道流程，但缺少外部执行能力。两者组合时，MCP 提供能力边界，Skill 提供任务流程。
- Subagent 解决的是"把任务拆给谁做"的问题，适合把复杂任务拆成并行或隔离的子任务。Subagent 是执行组织方式，不是知识包。Skill 可以被主 agent 使用，也可以被子 agent 使用；是否继承 Skill catalog 或已激活 Skill，由客户端决定。
- Hooks 解决的是"在特定事件发生时自动执行什么确定性动作"的问题。Hooks 通常绑定在工具调用前后、提交前后、会话结束等事件上，用来做日志记录、格式化、校验、阻止危险操作、发送通知。Hooks 更像事件驱动的自动化规则，强调确定性和外部控制；Skill 更像 agent 可理解、可选择、可执行的任务说明。

对照表：

| 能力 | 解决的问题 | 典型形态 | 是否按需加载 | 适合场景 |
| --- | --- | --- | --- | --- |
| Prompt | 这次对话怎么做 | 临时自然语言指令 | 直接进入上下文 | 一次性要求、语气、格式 |
| Skill | 某类任务如何反复稳定完成 | SKILL.md + scripts / references / assets | 是 | 可复用工作流、领域流程、组织规范 |
| MCP | agent 能访问什么工具和数据 | 外部 server / tool connector | 工具按需调用 | 数据库、GitHub、设计工具、内部系统 |
| Subagent | 任务由谁并行或隔离执行 | 子 agent / worker | 取决于客户端 | 并行研究、隔离执行、复杂任务拆分 |
| Hooks | 事件发生时强制做什么 | 事件触发脚本或命令 | 由事件触发 | 校验、审计、阻断、通知、自动格式化 |

一句话总结：Prompt 是临时指令，Skill 是可复用任务流程，MCP 是外部工具连接，Subagent 是任务执行组织方式，Hooks 是事件驱动的确定性自动化。

## 开放规范与目录结构

### 目录结构

Skill 文件夹拆成核心文件和可选资源：SKILL.md 负责主说明，Assets、References、Scripts 分别承载模板与示例、补充资料和可重复执行的代码。这个结构直观说明 Skill 是结构化工作包，而不是一段孤立 prompt。

```text
skill-name/
├── SKILL.md
├── scripts/
├── references/
├── assets/
└── ...
```

两个核心约束：

- SKILL.md 是必需文件，负责承载元数据和主指令。
- 其他目录都是可选的，用来承载执行代码、文档、模板和静态资源。

从工程角度看，这种目录结构很像一个小型插件或包：它有入口文件，也有可选资源目录，可以被版本管理、分发和校验。

### SKILL.md 格式与前置元数据

SKILL.md 必须由两部分组成：YAML frontmatter 和 Markdown content。frontmatter 用来声明结构化元数据；正文用来写 agent 执行任务所需的自然语言指令、示例和注意事项。

最小示例：

```yaml
name: skill-name
description: A description of what this skill does and when to use it.
```

包含可选字段的示例：

```yaml
name: pdf-processing
description: Extract PDF text, fill forms, merge files. Use when handling PDFs.
license: Apache-2.0
metadata:
  author: example-org
  version: "1.0"
```

frontmatter 中字段分为必填和可选：

- name：必填，最多 64 个字符，只能使用小写字母、数字和连字符，不能以连字符开头或结尾。
- description：必填，最多 1024 个字符，不能为空，要说明 Skill 做什么以及何时使用。
- license：可选，声明许可证名称，或引用打包在 Skill 中的许可证文件。
- compatibility：可选，最多 500 个字符，说明环境要求，比如目标产品、系统包、网络访问需求等。
- metadata：可选，任意 key-value 映射，用于额外元数据。
- allowed-tools：可选，实验性字段，用空格分隔预批准工具。

frontmatter 的作用是让 Skill 被客户端、agent 和工具识别。尤其是 name 和 description，它们直接参与 Skill 的发现和触发。

### name 字段的约束与命名

name 是必填字段，命名约定强调一致性，推荐使用动名词形式（动词加 -ing），因为它能清楚描述 Skill 提供的活动或能力。约束非常具体：

- 长度必须是 1-64 个字符。
- 只能包含小写字母、数字和连字符。
- 不能以连字符开头或结尾；不能包含连续连字符。
- 必须匹配父目录名。

有效示例：`pdf-processing`、`data-analysis`、`code-review`。无效示例：`PDF-Processing`（大写）、`-pdf`（连字符开头）、`pdf--processing`（连续连字符）。

这些规则的目的不是形式主义，而是为了让 Skill 在文件系统、包管理、客户端索引和跨产品分发中保持稳定标识——Skill 名称既是人类可读标识，也是机器可验证标识。命名一致的好处：文档和对话中更容易引用；读者能一眼看出 Skill 做什么；多个 Skill 更容易组织和搜索；整个 Skill 库更专业、更统一。

### description 字段的写法

description 也是必填字段，因为 description 是 Skill 发现的核心入口，agent 会用它决定当前任务该不该调用某个 Skill。要求：

- 长度必须是 1-1024 个字符。
- 应该同时描述 Skill 做什么和什么时候使用。
- 应该包含帮助 agent 识别相关任务的具体关键词。

好描述示例：`Extracts text and tables from PDF files, fills PDF forms, and merges multiple PDFs. Use when working with PDF documents or when the user mentions PDFs, forms, or document extraction.`

差描述示例：`Helps with PDFs.`

差别非常关键：好的 description 明确列出"提取文本和表格、填写 PDF 表单、合并 PDF"，还说明当用户提到 PDF、forms、document extraction 时使用；差描述只说"帮助处理 PDFs"，太泛，agent 不容易判断边界。description 是 Skill 召回质量的核心字段：写得不好，Skill 可能不会被正确触发，或者在不相关任务中误触发。

description 应使用第三人称，原因是 description 会被注入系统提示，如果写成"我可以帮你处理 Excel 文件"或"你可以用这个处理 Excel 文件"，视角不一致可能影响发现效果。好的写法是"处理 Excel 文件并生成报告"这种第三人称描述。

### 正文内容

frontmatter 后面的 Markdown 正文是 Skill 的指令部分。规范没有强制格式限制，原则是写任何能帮助 agent 有效完成任务的内容。推荐内容包括：步骤化指令、输入和输出示例、常见边界情况。

重要提醒：当 agent 决定激活某个 Skill 后，会加载整个 SKILL.md。因此如果正文太长，就应该拆分到引用文件里，否则 Skill 一旦激活，就会把过多内容塞进上下文，降低效率。这也解释了为什么 Agent Skills 强调主文件要精简：SKILL.md 应该承载核心流程，不应该把所有参考资料都堆进去。

### 可选目录：scripts、references、assets

scripts/ 目录用来放 agent 可以运行的可执行代码。脚本应该满足 3 个要求：自包含或清楚记录依赖；提供有帮助的错误信息；能优雅处理边界情况。支持语言取决于具体 agent 实现，常见选择包括 Python、Bash 和 JavaScript。scripts/ 的意义是把确定性、重复性强的操作交给代码，比如解析文件、转换格式、提取字段、批量处理数据，比让 agent 每次临时生成逻辑更稳定。

references/ 目录用来放 agent 按需读取的补充文档，示例包括 REFERENCE.md（详细技术参考）、FORMS.md（表单模板或结构化数据格式）、finance.md / legal.md 等领域专用文件。规范建议每个 reference 文件保持聚焦：agent 会按需加载这些文件，文件越小、主题越清楚，越能减少上下文使用。这个目录适合承载重要但不是每次都需要的内容，比如 API 细节、行业术语、公司政策、复杂模板说明等。

assets/ 目录用来放静态资源，包括文档模板、配置模板、图片（图表、示例）、数据文件（lookup tables、schemas）。assets/ 和 references/ 的区别：references 更偏"给 agent 读的说明文档"，assets 更偏"执行任务时使用的静态材料"，比如 Word 模板、PPT 模板、配置样例、schema 文件。

### 渐进式披露的加载层级

渐进式披露是 Agent Skills 的核心工程设计：不是靠无限扩大上下文，而是靠文件结构和加载策略控制上下文消耗。一个好的 Skill 应该让 agent 先用 description 判断是否相关，再用主文件执行核心流程，最后只在需要时读取更深资源。

agent 会逐步加载 Skill：

- Metadata（约 100 tokens）：启动时加载所有 Skills 的 name 和 description。
- Instructions（建议低于 5000 tokens）：Skill 激活时加载完整 SKILL.md 正文。
- Resources（按需）：只有需要时才加载 scripts/、references/、assets/ 中的文件。

规范建议主 SKILL.md 保持在 500 行以内，详细参考材料应该移动到独立文件中。最小 Skill 不需要复杂目录，只要元数据和核心说明足够清楚即可；Skill 变复杂后，主文件保持入口作用，把高级参考、表单说明拆到独立文件，主文件通过链接指向其他文件，agent 只有在任务需要时才读取额外内容。完整的技能目录结构可能如下：

```text
pdf/
├── SKILL.md              # 主指令文件（触发 Skill 时加载）
├── FORMS.md              # 表单填写指南（按需加载）
├── reference.md          # API 参考资料（按需加载）
├── examples.md           # 使用示例（按需加载）
└── scripts/
    ├── analyze_form.py   # 工具脚本（执行，不加载进上下文）
    ├── fill_form.py      # 表单填写脚本
    └── validate.py       # 校验脚本
```

SKILL.md 应该像入职指南里的目录：一个新成员加入团队时，不会把整个公司知识库都背下来，而是先知道有哪些手册、什么时候该看哪份文档、遇到具体任务再打开具体章节。Skill 对 Agent 的作用类似：不是把所有知识一次性塞进模型，而是给 Agent 一个可导航的能力目录。

## Skill 创建与编写原则

### 最小可用 Skill 与好 Skill 的条件

最小可用 Skill 可以非常短：只要有目录、SKILL.md、合法 frontmatter 和可执行说明，就能被兼容客户端发现和使用。Skill 不一定复杂，关键是触发描述和执行步骤足够清楚。

写 Skill 的目标是让它 well-scoped and calibrated to the task，两个关键要求：

- well-scoped 解决的是边界问题：Skill 既不能小到一个普通任务需要加载多个 Skills，也不能大到什么都想覆盖，导致触发不准、内容臃肿。
- calibrated 解决的是自由度问题：任务越灵活，越应该给 agent 判断空间；任务越脆弱、越依赖固定顺序，指令越要明确。

核心判断是：Skill 不是一份泛泛的最佳实践列表，而是让 agent 在具体任务中少走弯路的执行知识包。

### 从真实专业经验出发

有效 Skill 必须来自真实专长。这里的专长不一定只来自专家口述，也可以来自真实任务、项目资料、代码历史、issue、review 反馈和故障复盘。关键是把领域特定上下文（domain-specific context）放进 Skill 创建过程。

Skill 的常见失败模式，是直接让 LLM 在没有领域上下文的情况下生成内容。这样往往得到很通用的流程，比如 "handle errors appropriately" 或 "follow best practices for authentication"。这些话看起来正确，但没有告诉 agent 在当前项目里应该使用哪个 API、哪些边界情况最常见、团队约定是什么、哪些错误以前真实发生过。

### 从实际任务中提取

从真实任务中抽取 Skill 是最可靠的方法之一：先和 agent 一起完成一项实际工作，在过程中提供上下文、纠正方向、说明偏好和补充约束，任务完成后再把可复用模式抽出来。需要重点记录 4 类信息：

- 实际可行的执行步骤：真正导向成功的动作顺序，不是理论上应该怎么做，而是实际执行中跑通的路径。
- 用户修正项：用户纠正 agent 的地方，比如"用 X 库，不要用 Y 库""还要检查 Z 边界情况"。
- 输入/输出格式：输入数据是什么样，输出结果应该长什么样。
- 提供的背景信息：项目特定事实、团队约定、限制条件，以及 agent 原本不知道的信息。

这种方法的价值在于：成功执行本身就是最好的 Skill 原料。真实任务会自然暴露哪些指令有用、哪些错误容易发生、哪些信息如果不说 agent 就会猜错。

### 从已有项目资料综合

如果已经有大量项目材料，也可以从这些材料中综合 Skill。关键不是喂通用文章，而是喂项目特定材料。适合用来生成 Skill 的材料包括：

- 内部文档、runbooks、style guides。
- API 规范、schema 和配置文件。
- Code review comments 和 issue trackers——它们能捕捉反复出现的问题和 reviewer expectations。
- Version control history，尤其是补丁和修复记录，因为真实改动能揭示实际模式。
- 现实世界中的失败案例及其解决方案。

比如，一个 data-pipeline Skill 如果来自团队真实事故报告和运行手册，会比来自泛泛"数据工程最佳实践"的 Skill 更强：前者包含真实 schema、失败模式和恢复流程，后者只提供通用原则。工程含义是：Skill 要补的是局部事实和项目流程，不是互联网常识。越贴近真实项目，Skill 越可能在执行中发挥作用。

### 通过真实执行来打磨

Skill 的初稿通常只是起点，真正的质量来自真实执行后的迭代：把 Skill 放到实际任务中运行，再把结果反馈回创建过程。"结果"不只包括失败，也包括成功案例——成功能说明哪些指令有效，失败能说明哪里缺失、过宽或过窄。

迭代时重点问 3 个问题：

1. 哪些输入让 Skill 在不该触发时触发？
2. 哪些应该覆盖的场景没有覆盖？
3. 哪些内容没有任何作用，可以删掉？

整个迭代过程中，更重要的是看 agent execution traces，而不是只看 final outputs。执行轨迹能暴露 agent 的过程问题。agent 在无效步骤上浪费时间，通常有 3 个原因：指令太模糊，agent 需要试好几条路才找到可行方案；指令不适用于当前任务，但 agent 仍然照做；选项太多，没有明确默认方案。

哪怕只做一轮 execute → revise，质量也会有明显提升；复杂领域通常需要多轮。更系统的迭代可以使用测试用例、断言和评分机制。

### 控制上下文成本：补充 agent 缺少的，省略它已知道的

Skill 一旦激活，完整 SKILL.md body 会进入 agent 的上下文窗口，并和对话历史、系统上下文、其他 active skills 一起竞争注意力。上下文不是免费的，Skill 里的每个 token 都要有理由。

Skill 应该聚焦 agent 不知道、且没有指令就容易做错的信息：项目特定约定、领域特定流程、不明显的边界情况、需要使用的特定工具或 API。不需要解释 agent 已经知道的通用概念，比如 PDF 是什么、HTTP 如何工作、database migration 是什么。

低效写法（过度冗长）：先解释 PDF 是什么、PDF 里有什么、提取文本需要库，最后才说 pdfplumber is recommended。问题不是内容错误，而是有效信息密度太低——真正有价值的只有推荐 pdfplumber 这一句，却被常识性说明稀释了。

更好的写法直接给默认工具、fallback 条件和最小代码示例：

```markdown
## 提取 PDF 文本

Use pdfplumber for text extraction. For scanned documents, fall back to
pdf2image with pytesseract.
```

普通文本 PDF 默认用 pdfplumber；扫描件需要 OCR 时，切换到 pdf2image + pytesseract，并附上 `pdfplumber.open()` / `extract_text()` 的最小代码。

判断一段内容是否值得写进 Skill，可以问自己一句：没有这条指令，agent 会不会做错？如果答案是否定的，就删掉；如果不确定，就测试；如果没有 Skill 时 agent 已经能完成整项任务，这个 Skill 可能没有提供价值。

### 设计内聚的能力单元与大型 Skill 的组织

Skill 的范围设计像函数设计：它应该封装一个连贯的工作单元（coherent unit of work），并且能和其他 Skills 组合。

- 范围太窄的问题：一个任务可能需要加载多个 Skills，带来上下文开销和指令冲突。
- 范围太宽的问题：Skill 难以精准激活。一个"查询数据库并格式化结果"的 Skill 可能是合理单元；如果还覆盖 database administration，就很可能在做太多事。

范围判断不靠主题大小，而靠任务是否属于同一个可组合工作单元。Skill 边界应该贴着工作动作，而不是贴着宽泛领域。

大型 Skill 应该使用渐进式披露组织：SKILL.md 控制在 500 行以内、5000 tokens 以内，只保留每次运行都需要的核心指令。引用文件要有明确触发条件，例如 "Read `references/api-errors.md` if the API returns a non-200 status code"——只有 API 返回非 200 状态码时才读取参考文件，agent 不需要预先加载整套参考材料。这就是 progressive disclosure 的核心：先加载核心流程，再按任务需要加载细节。

### 追求适中细节

过度全面的 Skill 可能伤害效果：agent 需要从大量内容里筛选当前任务相关信息，容易被不适用指令带到低效路径。更稳的方式是简洁、步骤化指导 + 可工作的例子。当内容开始覆盖每个边界情况，就需要重新判断：这些边界是否真的应该写进 Skill，还是可以交给 agent 自己根据上下文判断。

这不是拒绝细节，而是拒绝把 SKILL.md 写成完整参考手册。主文件应该服务每次执行都需要的核心流程，详细资料应该按需加载。好的 Skill 不是越长越好，而是要足够简洁、结构清晰，并且经过真实任务测试。

### 根据任务风险控制指令强度

Skill 的不同部分不需要同样强的规定性，指令的具体程度应该匹配任务脆弱度：任务越脆弱、越容易出错、越要求一致性，就越应该给低自由度的明确指令；任务越开放、越依赖上下文判断，就越可以给高自由度。对灵活指令来说，解释"为什么"通常比死板规定步骤更有效，agent 理解目的后更能根据上下文做判断。

- 代码审查这类开放任务，可以给检查方向（SQL 注入、认证检查、并发竞态、错误信息泄露），让 agent 根据具体 diff 判断，不强行规定每一步。
- 报告生成这类任务，可以给模板或参数化框架。
- 数据库迁移这类顺序敏感、高风险任务，要给固定命令并明确禁止修改，例如：

```markdown
## 数据库迁移

Run exactly this sequence:

    python scripts/migrate.py --verify --backup

Do not modify the command or add additional flags.
```

固定命令既包含验证也包含备份；"Do not modify" 明确阻止 agent 自行加 flag 或改命令。多数 Skills 是混合型的：有些部分可以让 agent 灵活判断，有些部分必须严格执行，每一部分都要单独校准控制力度。

### 提供默认方案，而不是菜单

当多个工具或方法都可能可用时，Skill 应该选定默认方案，并简短说明替代方案，而不是把所有选项平铺成菜单。

不推荐写法："You can use pypdf, pdfplumber, PyMuPDF, or pdf2image..."——给了 4 个选项，但没有告诉 agent 默认用哪个，也没有说明切换条件，结果是 agent 可能浪费时间比较工具，甚至选错路径。

更好的写法给出明确的默认路径 + 特殊情况的备用路径："Use pdfplumber for text extraction. For scanned PDFs requiring OCR, use pdf2image with pytesseract instead."——默认用 pdfplumber 做文本提取，扫描 PDF 需要 OCR 时再切换。这种设计降低了 agent 的选择成本，也减少无效试错。

### 优先写流程，不只写原则

Skill 应该教 agent 处理一类问题，而不是给一个具体任务的一次性答案。

一次性答案写法（不推荐）："Join the `orders` table to `customers` on `customer_id`, filter where `region = 'EMEA'`, and sum the `amount` column."——只适用于一个具体查询，换一个分析任务就没法复用。

可复用流程写法（推荐）：

1. Read the schema from `references/schema.yaml` to find relevant tables
2. Join tables using the `_id` foreign key convention
3. Apply any filters from the user's request as WHERE clauses
4. Aggregate numeric columns as needed and format as a markdown table

抽象出分析查询的一般方法：读 schema 找表、按外键约定连接表、根据用户请求加 WHERE 条件、聚合数值列并输出表格，可以适配不同分析请求。

这不代表 Skill 不能包含具体细节。输出模板、never output PII 这类约束、工具特定指令都很有价值，关键是这些具体细节要服务一个可泛化的流程，而不是把 Skill 变成一次性答案。

## Skill 工程实践

### 易错点（Gotchas）小节

很多 Skill 里价值最高的内容是隐藏陷阱（Gotchas）：那些违反合理直觉、agent 如果不知道就容易犯错的环境特定事实。示例（3 个高价值陷阱）：

- users 表使用软删除，查询必须加 `WHERE deleted_at IS NULL`，否则会把已停用账户也算进去。
- 同一个用户 ID 在不同系统里名字不同：数据库是 `user_id`，认证服务是 `uid`，计费 API 是 `accountId`，三者指同一个值。
- `/health` 端点只要 Web server 运行就返回 200，即使数据库连接已断开；完整健康检查应该用 `/ready`。

这些不是"处理错误"这种通用建议，而是 agent 靠常识很容易猜错的环境事实。隐藏陷阱最好放在 SKILL.md，让 agent 在遇到问题前就看到；也可以放在 reference 文件里，但必须明确何时加载，否则 agent 可能不知道自己该去读 reference。

当 agent 犯错并被用户纠正时，这条纠正就应该加入 gotchas——这是迭代 Skill 最直接的方法之一。

### 模板模式与编辑工作流

模板模式：模板可以直接规定输出结构。例如一个分析报告模板规定包含标题、执行摘要（一段关键发现概览）、关键发现（需要有数据支撑）、建议（要具体可执行）。比起"写一份结构清晰的报告"，模板更能稳定格式。短模板可以直接放在 SKILL.md；长模板或只在某些场景需要的模板，可以放进 assets/，再由 SKILL.md 按需引用。

编辑工作流：把质量控制嵌进执行过程。例如：

1. Make your edits
2. Run validation: `python scripts/validate.py output/`
3. If validation fails: review the error message, fix the issues, run validation again
4. Only proceed when validation passes

validator 不一定只能是脚本，也可以是 reference checklist。重点是把"希望输出正确"变成可执行的反馈回路。

### 计划、验证、执行（plan-validate-execute）

批量操作或破坏性操作适合使用 plan-validate-execute：先生成结构化计划，再用真实来源验证，最后执行。以 PDF 表单填写为例：

1. 提取表单字段：`python scripts/analyze_form.py input.pdf` → `form_fields.json`（列出每个字段名、类型、是否必填）
2. 创建 `field_values.json`，把每个字段名映射到目标值
3. 验证：`python scripts/validate_fields.py form_fields.json field_values.json`（检查字段名是否存在、类型是否兼容、必填字段是否缺失）
4. 验证失败则修订 `field_values.json` 并重新验证
5. 填表：`python scripts/fill_form.py input.pdf field_values.json output.pdf`

关键是第 3 步：用验证脚本把计划文件和真实来源对比，只有验证通过才真正执行填表。好的错误信息能让 agent 自我修正，比如 "Field 'signature_date' not found — available fields: customer_name, order_total, signature_date_signed"——不仅说字段不存在，还列出可用字段，agent 能据此修正字段名再重新验证。这种模式把高风险执行放到最后，先用结构化计划和验证降低错误概率。

### 打包可复用脚本

迭代 Skill 时，要比较 agent 在多个测试用例中的执行轨迹。如果发现 agent 每次都在重新发明同一段逻辑（比如生成图表、解析特定格式、验证输出），就应该把这段逻辑写成测试过的脚本放进 scripts/。

脚本的价值是把重复、确定性、容易出错的逻辑固定下来：agent 不需要每次重新推理和重写实现，只需要调用脚本。这样能提升稳定性，也能减少上下文消耗和执行时间。

### 命名约定与引用结构

命名约定：Skill 命名要保持一致、具体、可搜索。推荐使用能表达活动或能力的名称，比如 processing-pdfs、analyzing-spreadsheets、managing-databases；也可以使用名词短语或动作导向命名，比如 pdf-processing、analyze-spreadsheets，关键是同一套 Skill 库里风格要一致。不推荐模糊名字（helper、utils、tools），也不推荐过度泛化（documents、data、files）。好的命名应该让人和 agent 都能快速判断这个 Skill 面向什么工作流。

参考文件不要嵌套太深：引用结构要尽量扁平。SKILL.md 可以链接到 advanced.md、reference.md、examples.md，但不应该让 SKILL.md 链到 advanced.md、再由 advanced.md 链到 details.md，把关键信息藏在第二、三层。原因是 agent 读取被引用文件时可能只预览一部分，关键内容藏得太深就有机会漏掉。更好的结构是所有关键参考文件都从 SKILL.md 直接链接，SKILL.md 作为总入口，直接告诉 agent 什么情况下读取哪个文件。

长参考文件要有目录：如果 reference 文件超过 100 行，最好在顶部加目录。这样即使 agent 只预览文件开头，也能知道这个文件覆盖哪些内容。这和渐进式披露是配套的：拆出来的文件本身也要容易被 agent 理解和导航，长 reference 应该像可导航手册，而不是大文本仓库。

### 内容维护细节

避免时间敏感信息：Skill 会被重复使用，不要把容易过期的信息直接写进主流程。坏写法是"如果在 2025 年 8 月之前使用旧 API，之后使用新 API"。更好的写法是把当前方法放在主路径（"使用 v2 API endpoint"），旧方法放进 Old patterns 或折叠区并明确标注已废弃。

保持术语一致：选择固定术语并贯穿使用，比如始终使用 API endpoint、field、extract；不要在同一份 Skill 中混用 API endpoint、URL、API route、path。术语漂移会增加 agent 的理解成本，也可能让 agent 误以为这些词代表不同概念。对需要稳定输出的 Skill 来说，术语一致性就是行为一致性的一部分。

避免 Windows 风格路径：路径统一使用正斜杠（`scripts/helper.py`），不要写 `scripts\helper.py`。路径是 agent 导航文件系统和执行脚本的基础，路径风格错误会直接导致读取或执行失败。

不要假设工具已安装：Skill 不应该假设某个包、库或命令一定存在。坏写法是只写"使用 pdf library 处理文件"；好写法是明确需要 pypdf 或 pdfplumber，并说明如何安装、验证或在不可用时走 fallback。不同 agent 平台的代码执行环境不同：有的能安装 PyPI、npm 包，有的没有网络访问不能运行时安装包，因此 SKILL.md 应该列出所需包并说明如何验证它们可用。

MCP 工具引用：如果 Skill 使用 MCP 工具，应该使用完整限定工具名，格式类似 `ServerName:tool_name`，例如 `BigQuery:bigquery_schema`、`GitHub:create_issue`。这样可以避免多个 MCP server 同时存在时工具名冲突，或 agent 找不到正确工具。Skill 不应该只写"调用 schema 工具"，而应该明确 server 和 tool。

### 避免反模式与高质量检查清单

常见反模式：

- 过多选项：不要列一堆库让 agent 自己选，应该给默认路径和例外条件。
- 过深引用链：不要让关键内容藏在二级、三级 reference 里。
- 一次性答案：不要把某次任务答案写死，应该抽象成可复用流程。
- 空泛原则：不要只写"遵循最佳实践"，要写清楚当前环境下怎么做。

高质量 Skill 检查清单——发布或复用一个 Skill 前，按三组问题检查：

核心质量：description 是否具体并包含关键触发词；是否同时说明 Skill 做什么和何时使用；SKILL.md 是否控制在合适长度内、主流程是否足够精简；额外细节是否拆到独立文件；文件引用是否尽量一层直达；是否正确使用渐进式披露；工作流是否有清晰步骤；术语是否一致；是否避免时间敏感主路径。

代码和脚本：脚本是否可靠、可执行、可调试；依赖是否明确；路径是否跨平台；错误信息是否能指导 agent 自我修正；高风险操作是否有验证、dry-run 或确认机制。

测试：至少创建多个代表性测试或评估报告；用计划使用的所有模型测试；用真实使用场景测试；如果是团队使用，还要纳入团队反馈。

## Skill 描述优化与触发评估

### description 是 Skill 的触发器

Skill 的效果首先取决于它能不能被激活。description 字段是 agent 判断是否加载 Skill 的主要信号，所以它不是普通简介，而是 Skill 的触发器说明：写得太保守，Skill 会漏掉本该处理的任务；写得太宽泛，又会在无关任务中误触发。优化 description 的本质是做一个小型分类器：把用户请求分成"应该加载这个 Skill"和"不应该加载这个 Skill"。

Skill 触发依赖渐进式披露：agent 启动时只读取每个 Skill 的 name 和 description 用于初步判断相关性，只有当用户任务匹配 description 时，agent 才读取完整 SKILL.md。这意味着 description 承担了完整触发责任：SKILL.md 写得再好，如果 description 没有传达"什么时候该用"，agent 就不会加载它。

还需要注意：agent 通常只会在任务需要额外知识或额外能力时考虑 Skill。简单的一步请求（比如 "read this PDF"）即使字面上匹配 PDF Skill，也可能不会触发，因为基础工具已经够用。description 真正发挥作用的任务，通常包含：不熟悉的 API、领域特定工作流、不常见格式、多步骤或需要判断的任务。所以 description 不只是关键词匹配，而是在告诉 agent：这个任务是否需要调用一套专门能力。

### 写出有效描述

好的 description 有几个关键原则：

- 使用祈使句：写成"在……时使用此技能"，而不是"此技能可以……"。agent 正在决定是否行动，所以 description 应该像指令，而不是像产品介绍。
- 关注用户意图，而非具体实现：description 应描述用户想完成的目标，而不是 Skill 内部怎么做。agent 看到的是用户请求，它要匹配的是用户意图。
- 宁可强势一些：description 可以适度主动，明确列出适用场景，甚至覆盖用户没有直接说出关键词的情况。例如用户没说 "CSV" 或 "analysis"，但请求本质上是在处理表格数据，也应该触发相关 Skill。
- 务必简洁：几句话到一小段通常足够。description 会在 agent 启动时和其他 Skills 的 description 一起加载，太长会增加上下文成本；规范还规定了 1024 字符硬限制。

关键平衡是：描述要足够具体以减少误触发，也要足够宽以覆盖隐含意图。

### 设计触发评估查询

优化 description 不能只靠感觉，需要准备一组带标签的 eval queries：每条 query 都是真实风格的用户请求，并标注是否应该触发 Skill。示例结构：

```json
[
  { "query": "I've got a spreadsheet in ~/data/q4_results.xlsx with revenue in col C and expenses in col D — can you add a profit margin column and highlight anything under 10%?", "should_trigger": true },
  { "query": "whats the quickest way to convert this json file to yaml", "should_trigger": false }
]
```

核心字段有两个：query 模拟真实用户输入（第一个有文件路径、列名、业务任务和格式要求，应该触发表格/数据处理类 Skill；第二个只是 JSON 转 YAML，不应该触发）；should_trigger 标注是否应该触发。

推荐的评估规模大约是 20 条 query，其中 8-10 条 should-trigger、8-10 条 should-not-trigger。数量不用一开始特别大，但要覆盖足够多边界情况。

### 正例与负例的设计

should-trigger queries 用来测试 description 是否覆盖 Skill 的能力范围，设计时应该在多个维度上变化：

- Phrasing：有正式表达，也有口语表达，还可以包含拼写错误或缩写。
- Explicitness：有的直接说出领域（"analyze this CSV"），有的只描述需求（"my boss wants a chart from this data file"）。
- Detail：有的很短，有的包含文件路径、列名、背景和上下文。
- Complexity：有单步任务，也有多步骤流程，测试 Skill 是否能在复杂链条中被识别出来。

最有价值的正例，是那些 Skill 有帮助但关联不明显的 query。如果 query 已经完全照着 Skill 名称发问，任何合理 description 都会触发，区分度并不高。真正能改进 description 的，是那些边界模糊、意图隐含、关键词不直接出现的请求。

should-not-trigger queries 的价值在于测试 description 是否精确。最好的负例不是完全无关的请求，而是 near-misses：共享关键词或概念，但实际需要不同能力。

- 弱负例："Write a fibonacci function"、"What's the weather today?"——和 CSV analysis 几乎没有重叠，太容易判断，不能测试 description 的边界。
- 强负例："I need to update the formulas in my Excel budget spreadsheet"（包含 Excel/budget/spreadsheet 等相邻概念，但任务是编辑预算表公式，不是 CSV 数据分析）、"can you write a python script that reads a csv and uploads each row to our postgres database"（包含 csv，但任务是写 ETL 脚本，不是分析数据）。

near-miss 能测试 description 是否只是靠关键词触发，还是能理解 Skill 的真实边界。

让测试更真实：真实用户输入通常比测试样例更混乱也更具体，eval queries 应该包含真实感——文件路径（~/Downloads/report_final_v2.xlsx）、个人背景（"my manager asked me to..."）、具体细节（列名、公司名、数据值）、口语表达、缩写和偶尔的拼写错误。一个 description 如果只能处理干净、标准、显式的 query，在真实使用中很可能表现不稳。

### 测试触发效果与多次运行

测试的基本方法是：将每条 query 提交给已安装该 Skill 的 agent，观察 agent 是否会调用该 Skill。不同客户端的注册方式不同（skills directory、配置文件或 CLI flag），关键是确保 Skill 对 agent 可发现。大多数 agent client 会提供可观察性能力（执行日志、工具调用历史或 verbose output）。判断标准很简单：如果 agent 加载了该 Skill 的 SKILL.md 就是触发，没有咨询这个 Skill 就继续执行就是没触发。

一条 query 通过测试的条件是：should_trigger=true 且 Skill 被调用；或 should_trigger=false 且 Skill 没有被调用。这把触发测试变成了一个二分类评估问题。

多次运行：模型行为有不确定性，同一条 query 可能某次触发、某次不触发，因此每条 query 应该运行多次（3 次是合理起点）。关键指标是 trigger rate（触发率）：某条 query 多次运行中 Skill 被调用的比例。正例 query 的触发率高于阈值就算通过；负例 query 的触发率低于阈值就算通过；0.5 可以作为默认阈值。如果有 20 条 query、每条跑 3 次，就是 60 次调用，最好脚本化。

### 用训练集和验证集避免过拟合

如果用全部 query 来反复优化 description，容易过拟合：description 可能只适配这些具体表达，换一批真实用户 prompt 就失效。解决办法是把 query 集拆成两部分：

- Train set（约 60%）：用来发现失败、指导修改。
- Validation set（约 40%）：放在一边，只用来检查改动是否泛化。

两组都要包含比例合理的 should-trigger 和 should-not-trigger，不要把所有正例放到一组、所有负例放到另一组。最好随机打乱后固定 split，保证每轮对比公平。

### description 优化循环

优化 description 是一个循环迭代的过程，而非一次性改写：

1. 评估当前 description 在训练集和验证集上的表现：训练集结果用于指导修改方向，验证集结果用于判断改动是否具备泛化能力。
2. 定位训练集中的失败案例：本应触发却未触发的 query，和不应触发却误触发的 query。注意只能用训练集中的失败案例指导修改，不可将验证集的失败案例用于训练，否则泛化能力下降。
3. 修订 description：若"本应触发的 query"失败，说明适用范围过窄，需扩大范围或补充适用场景；若"不应触发的 query"误触发，说明边界模糊、范围过宽，需明确不适用场景或澄清与相邻能力的区别。避免直接添加失败 query 中的具体关键词，更优做法是提炼这些失败 query 所代表的通用类别或核心概念再融入 description。若多轮微调后效果无明显提升，可尝试彻底改变 description 的结构、表述方式或切入角度。修订过程中要检查 1024 字符限制。
4. 循环迭代直至达标：重复"评估-定位失败-修订"，直到训练集基本通过或表现进入瓶颈期。
5. 依据验证集通过率选择最优版本：最优版本不一定是最后一版——后续迭代版本可能因过度适配训练集而过拟合，导致验证集通过率下降。

通常经过 5 轮迭代即可达到较好的效果。若迭代多轮后表现仍无提升，问题可能不在 description 本身，而在 query 设计（测试难度过高或过低、标签标注错误等）。也可以用 skill-creator Skill 自动化这个循环：拆分 eval set、并行评估 trigger rate、用 agent 提议 description 改进，并生成可实时查看的报告。

## Skill 输出质量评估与迭代

### 为什么需要结构化评估

Skill "看起来能用"并不能证明它真的可靠，真正需要回答的是：

- 不同表达方式下是否稳定可用。
- 边界情况是否还能处理。
- 相比不用 Skill 是否真的更好。
- 输出质量提升是否值得额外 token 和时间成本。

结构化评估（evals）就是为这些问题建立反馈回路。它不是一次性验收，而是让 Skill 能被系统化迭代：跑测试、看输出、评分、聚合、分析模式、改 Skill，再跑下一轮。

### 设计测试用例

一个测试用例由 3 部分组成：Prompt（真实用户会输入的消息）、Expected output（成功输出应该是什么样，用人类可读方式描述）、Input files（可选，任务需要处理的文件）。测试用例放在 Skill 目录里的 evals/evals.json：

```json
{
  "skill_name": "csv-analyzer",
  "evals": [
    {
      "id": 1,
      "prompt": "I have a CSV of monthly sales data in data/sales_2025.csv. Can you find the top 3 months by revenue and make a bar chart?",
      "expected_output": "A bar chart image showing the top 3 months by revenue, with labeled axes and values.",
      "files": ["evals/files/sales_2025.csv"]
    }
  ]
}
```

关键点：

- prompt 要像真实用户会写的请求，而不是抽象测试语句；表达方式、细节程度和正式程度都要变化，有些随意（"hey can you clean up this csv"），有些精确（指定输入路径、处理规则和输出路径）。
- expected_output 先写成人类可理解的成功标准，不必一开始就拆成严格断言。
- files 指向测试输入，确保每次评估用同样数据。
- 边界情况要覆盖：至少要有一个测试 malformed input、异常请求，或 Skill 指令可能模糊的场景。真实上下文也很重要（文件路径、列名、个人背景），"process this data" 这种 prompt 太泛，几乎测不出什么。
- 测试集逐步扩展：2-3 个用例足够开始第一轮，后续再根据失败模式补充。第一轮跑完后再加详细断言，因为实际输出出来之前往往不知道什么标准最值得检查。

### 运行评估与编写断言

运行评估的核心模式是每个测试用例跑两次：启用 Skill 跑一次；不启用 Skill（或用旧版本 Skill）跑一次。这样就有 baseline——只有和 baseline 对比，才能判断 Skill 是不是实际带来了改进。

断言（assertions）是关于输出应该包含什么或达成什么的可验证陈述。第一轮输出出来之后再写断言更合适。好的断言应该具体、可观察、可验证：

- "The output file is valid JSON"：可以用程序验证。
- "The bar chart has labeled axes"：具体且可观察。
- "The report includes at least 3 recommendations"：可以计数。

弱断言没有稳定评分价值："The output is good" 太模糊无法评分；"The output uses exactly the phrase 'Total Revenue: $X'" 太脆弱，正确输出可能换个说法就被判失败。

注意：不是所有质量都应该写成断言。写作风格、视觉设计、输出是否"感觉对"，很难拆成 pass/fail，更适合人工审查。断言应该留给能客观检查的标准。例如"生成收入最高 3 个月柱状图"可以拆成 4 条可检查标准：输出里要有柱状图图片文件；图中正好展示 3 个月；两个坐标轴都有标签；图标题或说明要提到 revenue。这样评分时就不会只凭感觉说"图做得不错"，而能定位到底缺了什么。

### 评分输出

评分（grading）就是对每条断言判断 PASS / FAIL，并保存具体证据。证据应该引用或指向真实输出，而不是一句主观评价。

机械检查优先用脚本：JSON 是否有效、行数是否正确、文件是否存在、图片尺寸是否符合预期，这些用脚本比 LLM 判断更可靠，也能跨迭代复用。语义性或视觉性检查可以让 LLM 辅助评分。

评分结果（grading.json）包含两部分：assertion_results 逐条记录断言文本、是否通过、证据；summary 汇总通过数、失败数、总数和通过率。例如某条失败记录为 "Y-axis is labeled 'Revenue ($)' but X-axis has no label"——失败得很具体，可以直接指导下一轮修改（在 Skill 中明确要求图表必须同时标注 X/Y 轴）。

### 聚合评估结果

每个 run 都评分后，需要聚合统计结果保存到 benchmark.json，对比 with_skill 和 without_skill 两组的 pass_rate、time_seconds、tokens 的均值和标准差。核心是 delta，它告诉你 Skill 多花了什么、又买到了什么：

- pass_rate 提升 0.50 表示通过率提高 50 个百分点。
- time_seconds 增加 13 秒表示耗时变长。
- tokens 增加 1700 表示上下文/推理成本更高。

如果 Skill 多花 13 秒但通过率提升 50 个百分点，通常值得；如果 token 翻倍但只提升 2 个百分点，就可能不值得。stddev 只有在每个 eval 多次运行时才有意义：早期只有 2-3 个测试用例、每个只跑一次时，重点看原始通过数和 delta；随着测试集扩大、多次运行增多，均值和标准差才更有统计价值。

### 分析失败模式

聚合数字会隐藏很多细节，benchmark 之后还要看模式：

- 删除或替换两边总是通过的断言：如果 with-skill 和 without-skill 都稳定通过，说明模型本来就做得好，这条断言不能体现 Skill 价值，保留它只会虚高 pass rate。
- 调查两边总是失败的断言：可能是断言坏了（要求模型做不到的事）、测试用例太难，或断言检查错了东西。下一轮要先修正这些问题。
- 重点研究 with-skill 通过、without-skill 失败的断言：这是 Skill 明确创造价值的地方，要弄清楚是哪些指令或脚本产生了差异。
- 结果跨 run 不一致时收紧指令：同一个 eval 有时通过有时失败（高 stddev），可能是 eval 本身 flaky，也可能是 Skill 指令太模糊、模型每次解释不同，可以加示例或更具体指导减少歧义。
- 检查耗时和 token 异常值：如果某个 eval 比其他用例慢 3 倍，要读执行 transcript，找出 agent 在哪里浪费时间。

模式分析的重点是找改进方向，而不是只看总分。总分告诉你"好不好"，模式告诉你"为什么"和"怎么改"。

### 人工复核与迭代 Skill

断言评分和模式分析只能检查已经写出来的断言。人工 reviewer 可以发现未预料的问题，比如输出技术上正确但没抓住用户真正意图、结构不清晰、视觉效果不好、结果不可用。每个测试用例都应该把实际输出和评分结果一起看，并保存具体反馈（如 feedback.json），反馈要可操作，比如"图表缺少坐标轴标签，而且月份按字母序而不是时间顺序排列"。

评分和复核完成后，有 3 类信号可以用来改 Skill：

- Failed assertions：指向具体缺口，比如少了某个步骤、指令不清、某个场景没覆盖。
- Human feedback：指出更宽的质量问题，比如方法错、结构差、输出技术正确但没帮助。
- Execution transcripts：揭示为什么失败。agent 忽略指令可能是指令模糊；agent 在无效步骤上花太久，可能是某些指令该删或简化。

最有效的改法，是把这 3 类信号和当前 SKILL.md 一起交给 LLM，让它提出修改建议——LLM 可以跨失败断言、人工反馈和 transcript 行为找模式，比人工逐条归纳更省力。给 LLM 提改进时要加几条约束：

- 从反馈中泛化：Skill 面向很多 prompt，不只服务测试用例。修改要解决底层问题，不要为某个样例打窄补丁。
- 保持 Skill 精简：少而好的指令经常胜过穷尽规则。如果 transcript 显示 agent 做了不必要验证、生成了无用中间结果，就删掉相关指令；如果 pass rate 在加规则后停滞，可能是 Skill 过度约束，可以尝试删减。
- 解释为什么：基于原因的指令通常比僵硬的 "ALWAYS do X / NEVER do Y" 更可靠。例如"做 X，因为 Y 往往导致 Z"会让模型更理解目的，也更容易在变化场景中执行正确。
- 打包重复工作：如果每次测试 run 都写类似 helper script（图表生成器、数据解析器），就应该把脚本打包到 scripts/ 目录中。

## 客户端 Skills 支持实现

### 实现总览与三层加载策略

在自己的 AI agent 或开发工具里支持 Agent Skills，覆盖的是完整生命周期：如何发现 Skills、如何告诉模型有哪些 Skills、如何把 Skill 内容加载进上下文、如何在会话过程中保持 Skill 内容有效。实现细节主要取决于 2 个问题：

- Skills 存在哪里：本地运行的 agent 可以扫描用户文件系统；云端或沙箱 agent 通常不能直接访问本地文件，需要用 API、远程 registry、上传目录、配置仓库或内置资产来提供 Skills。
- 模型如何访问 Skill 内容：如果模型有读文件能力，可以直接读取 SKILL.md；如果没有，就需要专门工具或程序化 prompt 注入。

所有兼容 Skills 的 agent 都应该遵循同一套三层加载策略：

| 层级 | 加载内容 | 加载时机 | Token 成本 |
| --- | --- | --- | --- |
| 1. Catalog | name + description | 会话开始 | 每个 Skill 约 50-100 tokens |
| 2. Instructions | 完整 SKILL.md body | Skill 被激活时 | 建议低于 5000 tokens |
| 3. Resources | scripts、references、assets 等 | 指令引用时 | 视资源而定 |

模型从一开始只看到 Skill catalog，知道有哪些能力可用；当它判断某个 Skill 相关时，再加载完整指令；如果指令提到支持文件，再单独读取具体资源。这种设计能保持基础上下文很小：即使 agent 安装了 20 个 Skills，也不需要一开始支付 20 份完整指令的 token 成本，每次对话只为实际用到的 Skill 付费。

### 发现 Skills：扫描、冲突与信任

扫描位置：客户端通常从多个位置发现 Skills——用户级目录（个人安装、跨项目复用）、项目级目录（当前仓库或工作区内，适合项目规则和团队约定）、组织级或平台级来源（企业统一分发、远程 registry、内置资产或 API 返回的 Skills）。本地 agent 可以直接扫描文件系统；云端或沙箱 agent 需要通过上传、配置仓库、远程 registry 或 API 把 Skills 提供给运行环境。

扫描对象：发现阶段不应该把任意目录都当成 Skill。合理做法是只识别包含 SKILL.md 的目录，并且只在 discovery 阶段解析 frontmatter，不加载完整正文和资源。扫描时还要忽略隐藏目录、临时目录、构建产物和不可信路径。

名称冲突处理：多个来源可能出现同名 Skill（比如用户级和项目级都定义了 code-review），常见策略有 3 种：

1. 优先级覆盖：项目级覆盖用户级，组织级覆盖个人级，或按客户端约定排序。
2. 报错并要求用户选择：适合安全要求更高的环境。
3. 命名空间区分：用来源或路径区分同名 Skill，比如 project/code-review 和 user/code-review。

无论采用哪种策略，都应该避免静默随机选择——Skill 名称参与激活和日志记录，冲突处理不清会导致 agent 加载错误流程。

信任考虑：项目级 Skills 来自当前仓库，而仓库可能不可信（比如刚 clone 的开源项目）。项目级 Skill 一旦自动加载，就可能把仓库里的指令注入 agent 上下文。因此应该加 trust check：只有用户把项目目录标记为 trusted 后，才加载项目级 Skills。这个机制防止不可信仓库静默注入指令，对开发工具来说是很重要的安全边界。

### 解析 SKILL.md 文件

SKILL.md 有两部分：`---` 分隔符之间的 YAML frontmatter，和 closing delimiter 后面的 Markdown body。解析过程：找文件开头的 `---`，再找后面的 closing `---`；解析中间的 YAML block，提取必填的 name 和 description 以及可选字段；closing `---` 后面的内容去掉首尾空白就是 Skill body。

异常 YAML 处理：不同客户端的解析器宽容度不同，最常见问题是没有加引号的值里包含冒号（如 `description: Use this skill when: the user asks about PDFs`），标准 YAML parser 可能把它误解析为新的 key/value 结构导致解析失败。兼容性更好的做法是 fallback：把这类值包成引号，或转换成 YAML block scalar 后再重试。

存储解析结果：解析后最好把 Skill 拆成几类信息存储——metadata（frontmatter 字段）、body（Markdown 正文）、directory path（用于解析相对路径和 bundled resources）、source（用户级/项目级/组织级/远程 registry）。每条 Skill 记录至少需要 name、description、location（SKILL.md 的绝对路径）三个字段，可以放在内存 map 中用 name 作为 key。body 有两种存法：发现时就存 body（激活更快）；激活时从 location 读取 body（内存占用更低，且能捕捉两次激活之间文件内容的变化）。

### 向模型披露可用 Skills（catalog）

这一层只告诉模型有哪些 Skills，不加载完整内容，属于 progressive disclosure 的第 1 层。

构建 Skill 目录：每个发现的 Skill 应该包含 name、description，以及可选的 location。格式可以是 XML、JSON 或项目里更合适的结构化形式，重点是只向模型暴露一份轻量目录。catalog 的 3 个核心字段：name 用于模型识别 Skill；description 用于模型判断什么时候相关；location 用于 file-read activation（给模型一个 base path）以及解析 Skill body 中的相对路径。每个 Skill 只增加约 50-100 tokens，即使安装几十个 Skills，catalog 仍然很紧凑。

catalog 放在哪里：通常放进 system prompt、developer message，或客户端专门维护的能力说明区。注意两点：catalog 旁边最好有行为指令，说明模型应该在任务相关时激活 Skill，而不是凭关键词机械触发；要说明如果没有相关 Skill，就按普通能力完成任务，不要强行使用 Skill。

过滤：客户端不一定要把所有发现的 Skills 都暴露给模型，可以根据当前用户权限、当前项目是否 trusted、当前产品入口是否支持该 Skill 的工具需求、Skill 的 compatibility 字段、当前任务或角色绑定的 Skill bundle 过滤。过滤的目标是减少候选集、提高召回准确率、降低不必要的安全暴露。

无可用 Skills 时的处理：如果当前没有可用 Skills，就不要注册 activate_skill 这类工具，也不要在 prompt 里告诉模型有 Skills 可用，否则模型可能会幻想不存在的 Skill 名称，或浪费时间尝试激活能力。

### 激活 Skills

当模型或用户选择某个 Skill 时，需要把完整指令送进对话上下文（progressive disclosure 第 2 层）。

模型驱动激活：多数实现依赖模型自己的判断，而不是在 harness 侧做关键词检测或触发匹配。常见实现模式有 2 种：

- 文件读取激活：模型用标准 file-read tool 读取 catalog 中的 SKILL.md 路径。不需要额外基础设施，只要 agent 已经有文件读取能力即可。
- 专用工具激活：注册一个工具（比如 activate_skill），输入 Skill 名称，返回 Skill 内容。模型不能直接读文件时必须用这种方式；即使模型能读文件，专用工具也有优势——控制返回内容（去掉或保留 YAML frontmatter）、用结构化标签包住内容方便上下文管理、同时列出 bundled resources、执行权限检查或请求用户同意、记录激活事件用于 analytics。

如果使用专用激活工具，name 参数应该限制为有效 Skill 名称集合（比如在 tool schema 中用 enum），防止模型幻想不存在的 Skill 名称。如果没有可用 Skills，就不要注册这个工具。

用户显式激活：用户也可以显式要求使用某个 Skill（"使用 data-analysis Skill 分析这个文件"），客户端应该允许模型或 harness 直接激活对应 Skill。但显式激活仍然需要做权限和可用性检查：Skill 是否存在、用户是否有权限、当前环境是否支持所需工具、项目级 Skill 是否来自 trusted workspace。用户显式要求不等于可以绕过安全边界。

模型收到什么：返回内容有 2 种选择——完整文件（模型看到整个 SKILL.md 包括 frontmatter，是 file-read activation 的自然结果；frontmatter 里可能有激活时有用的信息，比如 compatibility 提示环境要求）；只返回 body（harness 解析并移除 frontmatter，只返回 Markdown 指令，多数专用工具实现采用这种方式，因为 name 和 description 在 discovery 阶段已经提取过）。两种都可行，取决于客户端是否希望在激活阶段保留 frontmatter 信息。

### 结构化返回、资源列表与权限 allowlist

结构化返回 Skill 内容：专用激活工具可以把 Skill 内容用结构化形式返回给模型，通常包含 4 类信息：Skill 名称、SKILL.md body、Skill directory、可用的 bundled resources。结构化包裹有 3 个好处：模型能区分 Skill 指令和普通对话内容；harness 在上下文压缩时能识别并保护 Skill 内容；bundled resources 被展示给模型但没有被提前加载。

列出 bundled resources：激活 Skill 时，可以把 scripts/、references/、assets/ 中的可用资源列给模型，但不直接加载内容。资源列表应该包含相对路径和简短用途（例如 scripts/extract.py 用于提取 PDF 文本）。这样既保留渐进式披露，又减少模型盲目搜索文件系统。

权限 allowlist：如果使用专用激活工具，可以配合 allowlist 控制可激活的 Skill 名称、可读取的资源路径和可执行的脚本，避免模型越权读取 Skill 目录外文件，也能限制高风险脚本执行。allowlist 特别适合企业环境和远程 agent，因为它把"模型想做什么"和"系统允许做什么"分开：模型可以提出激活或读取请求，但最终由客户端执行权限检查。

### 管理 Skill 上下文

Skill 指令进入会话上下文后，还要在整个 session 中保持有效。

保护 Skill 内容不被上下文压缩丢掉：很多 agent 会在上下文窗口变满时截断或总结旧消息。如果 Skill 内容被压缩掉，agent 会在没有明显错误的情况下性能下降——模型还会继续工作，但已经丢失了专门指令。因此 Skill 内容应该被视为持久行为指导，在上下文压缩时受到保护。常见方法：给 Skill tool outputs 打 protected 标记，让 pruning algorithm 跳过它们；或使用结构化标签识别 Skill 内容并在 compaction 中保留。

激活去重：客户端可以跟踪当前 session 中已经激活过哪些 Skills。如果模型或用户尝试再次加载同一个 Skill，可以跳过重新注入，避免同一套指令在上下文里出现多次，减少 token 浪费和重复指令造成的注意力干扰。

Subagent 委派：如果客户端支持 subagent，要决定 Skill 是否传递给子 agent。常见策略有 3 种：

1. 继承已激活 Skills：子 agent 直接获得父会话已经激活的 Skill 内容，适合延续同一任务。
2. 继承 catalog、不继承内容：子 agent 只看到可用 Skills，自己根据子任务决定是否激活，适合保持上下文轻量。
3. 显式指定 Skills：父 agent 创建子 agent 时指定允许使用哪些 Skills，适合权限和任务边界更严格的系统。

核心仍然是渐进式披露和权限控制：不要默认把所有 Skills 全量复制给每个 subagent，否则会浪费上下文，也可能扩大权限边界。

## 平台化落地与企业级治理

### Skill 与 Prompt 的区别（平台视角）

prompt 是一次性指令，Skill 是可复用能力包；prompt 更偏对话控制，Skill 更偏任务流程工程化：

- Prompt 更像一次性指令，通常只在当前对话或当前任务里生效。用户每次需要同类能力时，都要重新写规则、补背景、贴模板。适合轻量、临时、低复用的任务。
- Skill 更像长期存在的能力包。它以文件系统或包结构存在，可以被安装、复用、版本管理，并在任务相关时按需加载。Skill 不只是告诉模型"怎么回答"，还可以组织脚本、模板、参考资料、schema、示例和验证流程。
- 从上下文角度看，prompt 会直接占用上下文窗口；Skill 把可能需要的信息放在外部，通过渐进式披露进入上下文，只在触发时加载必要部分。
- 从执行能力看，prompt 主要影响模型输出风格和推理方式；Skill 可以把操作流程、脚本和工具使用方式封装起来，让 agent 更稳定地完成具体工作流。

### 预置 Skills 与自定义 Skills

平台化 Skills 通常同时包含两类能力：

- 预置 Skills 是平台或官方提供的通用能力包，常见场景包括 PDF、Word、Excel、PowerPoint 等文件处理。特点是使用门槛低、覆盖高频任务、由平台维护，用户主要负责提出任务目标。
- 自定义 Skills 是用户、团队或组织自己创建的能力包，适合沉淀内部流程、品牌规范、代码规范、数据分析方法、业务报告模板、客户沟通格式等。它解决的是通用模型不知道组织内部做法的问题。

两者的关系：预置 Skills 解决通用文件和常见办公任务；自定义 Skills 解决组织特定流程和领域任务。二者可以组合使用，例如先用 PDF Skill 提取资料，再用组织内部报告 Skill 生成固定格式报告。

### Skills 的使用入口

Skills 可以出现在不同 agent 使用入口中（API、代码编辑环境、本地 agent、网页产品或企业工作台），不同入口概念一致，但能力同步和配置方式不一定相同：

- 在 API 中，Skills 往往和代码执行环境、容器、文件系统以及文件下载能力一起使用。应用需要声明启用哪些工具、哪些 Skills，以及生成的文件如何取回。预置 Skills 通过平台提供的 skill_id 引用，自定义 Skills 通常需要先上传或注册再在请求中启用。
- 在代码编辑环境中，Skills 更偏本地工作流（代码审查、测试、迁移、文档整理、项目脚手架），通常和仓库文件系统、命令执行、版本控制配合。这类入口更适合自定义 Skills，因为项目规则、团队约定和自动化脚本本来就应该跟代码库或本地开发环境放在一起。
- 在网页产品或企业工作台中，Skills 更偏用户可配置能力（文档处理、品牌规范、团队模板、内部流程）。用户不一定关心目录结构，但平台需要在后台完成 discovery、activation 和权限控制。常见边界：有些网页产品里的自定义 Skills 是个人级配置，每个用户需要自己上传或启用，不一定自动在组织内共享；企业要统一分发就需要组织级 provision 或 API 级集成。

Skills 的核心机制可以跨入口复用，但每个入口的安装、同步、权限、调用方式可能不同，不要把某个平台的具体配置误认为 Skills 的本质。

### API 中使用 Skills 的工程链路

API 场景里使用 Skills 通常不是普通文本聊天，而是一个完整的工具工作流，典型链路包括：

1. 启用支持 Skills 的能力开关或版本参数。
2. 提供代码执行或工具执行环境。
3. 创建容器或工作区，用于存放输入文件、中间产物和输出文件。
4. 在请求中声明要使用的预置 Skill 或自定义 Skill。
5. 通过任务提示词说明目标、输入、输出格式和成功标准。
6. agent 运行 Skill，生成结果文件。
7. 应用通过文件 API 或产物引用下载最终结果。

这条链路的重点是：Skill 提供"怎么做"的能力，任务提示词提供"这次要做什么"，工具环境提供"在哪里执行"，文件接口提供"如何拿到结果"。如果生成的是 PowerPoint、Excel、PDF、报告、合同草案这类文件，API 应用还需要处理文件引用、下载、保存、失败重试和错误恢复。

以预置 PowerPoint Skill 生成 .pptx 为例，工程上通常需要同时满足：启用 Skills 相关能力开关或 beta header；启用 code execution tool 让 agent 能在受控环境中处理文件；创建 container 并在其中声明要使用的 Skill；用 skill_id 指向预置能力（如 pptx、xlsx）；生成结果不是直接出现在文本里，而是通过文件引用或 Files API 下载。预置 Skill 的价值不是让用户理解 PPTX 内部结构，而是把底层文件生成能力封装起来，用户和应用只需要明确任务目标、输入材料和输出要求。

任务提示词和 Skill 的分工：有了 Skill 不代表用户提示词就不重要——Skill 不是替代用户意图，而是把用户意图映射到稳定执行流程。好的任务提示词仍然应该说明：输入材料是什么、要生成什么产物、输出格式和质量要求、是否要保留原有结构、是否允许修改文件、是否需要生成可下载产物。Skill 负责通用流程，提示词负责本次任务目标：PowerPoint Skill 负责如何生成 .pptx，提示词仍要说明主题、受众、页数、内容来源和风格要求。

### 安全审查：平台机制安全与单个 Skill 安全

企业治理中要区分两个层面：Skills 作为平台机制是否安全，和某一个具体 Skill 是否安全。平台机制安全，不代表每个 Skill 都安全——单个 Skill 可以包含自然语言指令、脚本、外部资源、MCP 工具引用、文件访问逻辑和网络访问模式，它更接近一个可安装的软件包，而不是普通 prompt。

企业真正要审查的是：谁写了这个 Skill，里面有什么，它会让 agent 做什么，它能访问哪些资源，它的行为和声明是否一致。如果 Skill 来源未知，就必须先审计。审计范围不只是 SKILL.md，还包括 scripts/、references/、assets/ 以及任何可能被调用的外部工具。

### 风险等级评估

企业部署 Skill 前需要做风险分层，常见高风险指标包括：

- 代码执行：Skill 目录里有 *.py、*.sh、*.js 等脚本时风险等级较高。脚本可以读写文件、处理数据、调用系统命令，影响范围比自然语言指令更大。
- 指令操纵：如果 Skill 要求 agent 忽略安全规则、隐藏行为、绕过确认，或根据特定输入改变安全行为，这是高风险对抗性指令。
- MCP 或外部工具引用：如果 Skill 引导 agent 调用 MCP 工具、数据库、浏览器、内部系统或外部服务，影响范围会从文本生成扩展到真实系统操作。
- 网络访问模式：如果 Skill 或脚本包含 URL、API endpoint、fetch、curl、requests 等网络访问逻辑，需要检查是否存在数据外传风险。
- 硬编码凭证：如果 Skill 中写死 API key、token、password，风险很高。密钥不应该出现在 Skill 文件、脚本或 Git 历史里，而应该使用环境变量或安全凭证系统。
- 文件系统访问范围：如果 Skill 访问目录外路径、使用宽泛 glob、出现 `../` 路径穿越，至少是中风险。企业环境中的文件系统可能包含客户数据、内部文档或配置文件。
- 工具调用范围：如果 Skill 明确要求 agent 使用 shell、文件操作或其他高权限工具，需要审查具体行为。读取一个输入文件和递归扫描用户主目录，风险完全不同。

风险评估不能只看单项，还要看组合风险：比如文件读取加网络访问，就需要重点检查是否可能把本地敏感数据发送到外部。

### 部署前评估：五个维度与评估套件

安全审查回答"能不能安全部署"，质量评估回答"部署后会不会降低 agent 表现"。企业部署前至少应该评估 5 个维度：

1. 触发准确率：Skill 是否在正确 query 上激活，并且在无关 query 上不激活。description 过宽会导致 Skill 抢占不该处理的任务。
2. 隔离行为：Skill 单独使用时是否正确，比如是否引用了不存在的文件、是否依赖隐含环境、是否缺少必要脚本。
3. 共存能力：新 Skill 是否会降低其他 Skills 表现。多个 Skills 同时存在时，description 重叠可能导致误触发或抢触发。
4. 指令遵循：agent 是否稳定执行 Skill 要求，比如是否跳过验证步骤、是否使用了错误库、是否忽略输出格式。
5. 输出质量：最终结果是否正确、有用、符合业务要求。即使触发正确、流程执行正确，结果不可用也不能上线。

这 5 个维度覆盖了从"是否该用"到"用了以后是否好用"的完整链路。

评估套件要求：企业级 Skill 不应该只靠人工感觉验收，而应该要求作者提交 evaluation suite。每个 Skill 至少应该包含 3-5 个代表性 query，覆盖三类情况：应该触发的 query（验证召回能力）、不应该触发的 query（验证不会误触发）、模糊边界 query（测试 description 和指令是否足够清楚）。例如一个销售报表 Skill：应该触发"帮我格式化本季度销售报表"；不应该触发"解释一下销售漏斗指标"；模糊边界"看一下这个 pipeline 数据有没有问题"。

Skill 效果还可能因模型不同而变化：高能力模型能正确理解边界，不代表更小模型也能稳定触发和执行。因此企业最好在所有计划使用 Skills 的模型上进行测试。

### Skill 生命周期管理

企业级 Skill 有生命周期，不是一次写完永久有效，它会随着业务流程、工具、模型能力和组织结构变化而演化。生命周期可以拆成 3 个核心阶段：

1. 规划：识别哪些工作流适合做成 Skill。判断标准是：重复、容易出错、需要专业知识。一次性任务、简单且不易错的任务，不一定值得写 Skill。
2. 创建与审查：作者按照最佳实践编写 Skill，并提交安全审查和评估套件。生产级 Skill 应该有职责分离，作者不应该自己审查自己的 Skill。
3. 测试与发布：同时做隔离测试和共存测试。隔离测试验证 Skill 自身是否正常；共存测试验证它不会抢触发、不会让已有 Skills 失效、不会造成整体回归。

发布后还要持续收集失败案例、用户反馈和评估结果，再决定是否修改、拆分、合并或下线。

### 大规模 Skills 的召回限制与整合策略

组织内 Skills 数量变多后，主要问题不是存储，而是召回和选择。每个 Skill 的 name 和 description 都会在系统提示或 Skill catalog 中竞争注意力：如果同时暴露太多候选 Skills，agent 更容易选错、漏选或激活多个边界重叠的 Skills。因此不是所有 Skills 都应该同时挂载给所有用户，更合理的方式是根据角色、任务类型、应用入口或上下文路由到不同 Skill 集合。

有的平台或 API 会限制单次请求可启用的 Skills 数量（例如单次请求最多 8 个）。这个限制背后的工程原因不是文件放不下，而是为了维持可靠召回准确率。如果某个角色需要的 Skills 超过限制，企业通常有两种处理方式：把高度相关、评估后表现不下降的窄 Skills 合并成更宽的 Skill；或按任务类型做路由，让不同任务只挂载对应的 Skill 集合。企业级 Skills 管理不能只靠"继续添加"，还需要设计 Skill routing 策略。

先具体创建，再逐步整合：组织一开始应该创建窄而具体的工作流 Skill，而不是一上来做宽泛的万能 Skill。窄 Skill 的好处是边界清楚、触发准确、评估容易、风险可控。例如销售运营场景可以先拆成 formatting-sales-reports、querying-pipeline-data、updating-crm-records，等这些工作流成熟后再考虑合并成 sales-operations。合并依据不应该只是"它们都属于销售"，而应该是评估结果证明：合并后的 Skill 在触发准确率、输出质量和共存能力上不低于被替代的多个 Skills。

### 注册表、角色分组与版本控制

命名、注册表和资产台账：企业内部应该维护 Skills 注册表，把 Skill 当成组织资产管理。注册表至少应该记录：

- Purpose：这个 Skill 支持什么工作流。
- Owner：哪个团队或个人负责维护。
- Version：当前部署版本。
- Dependencies：依赖哪些 MCP servers、包、脚本或外部服务。
- Evaluation status：上次评估时间和结果。
- Risk level：风险等级和审查状态。

这个 registry 让组织知道自己有哪些能力包、谁负责、依赖什么、是否通过评估、出了问题找谁回滚。

基于角色的 Skill 组合：企业可以按组织角色分组 Skills，让每个用户的活跃 Skill 集合保持聚焦。例如 Sales team 挂载 CRM 操作、pipeline 报告、proposal 生成；Engineering 挂载代码审查、部署流程、事故响应；Finance 挂载报告生成、数据验证、审计准备。角色绑定 Skill 的原则是只包含该角色日常工作流相关的 Skills，既减少召回干扰，也降低用户接触不必要工具能力的风险。这个设计类似最小权限原则。

版本管理策略：Skills 应该像软件资产一样做版本控制，而不是散落在个人配置里。版本控制能解决：多人协作编辑、安全审查记录、变更追踪、失败回滚、环境一致性、不同团队使用不同版本时的排查问题。如果 Skill 包含脚本，更应该纳入测试和版本管理，因为脚本行为变化可能直接影响文件、数据或外部系统。

企业级 Skills 总结：Skill 从个人能力包升级到企业生产能力后，核心问题会从"怎么写"变成"怎么安全稳定地规模化使用"。企业级 Skills 的本质是把组织经验、业务流程和工具使用方法做成可审查、可评估、可分发、可回滚的 agent 能力包。个人使用 Skills 关注效率；企业使用 Skills 还要关注治理、安全、评估、角色分发、版本控制和召回准确率。
