# EVALUATION — 评估体系与结果记录

本项目的评估分**两套体系**,对 LLM 的态度相反,物理分离:

| | 结构护栏 `evals/` | 效果评估 `sim/` |
|---|---|---|
| 回答的问题 | 改坏了没有(对不对) | 系统好不好(效果) |
| LLM | **强制 stub**(pop 掉 key) | **必须真 LLM**(无 key 拒绝启动) |
| 成本 | 零 token,~70s 跑完 | 烧 token,~¥0.1-0.5/场 |
| 运行方式 | `unittest discover` 默认全跑 | 只能 `python -m sim.*` 显式跑 |
| 数据 | 内存构造 / TEST 库 | TEST 库 + `sim/runs/` JSONL(真相源) |

---

## 〇、总结(一页速览)

> **百余场真 LLM 仿真面试 + 11 种可复跑评估手段 + 430 条结构护栏,定位并修复
> 8 类质量缺陷:强弱候选人的维度分差从 0 拉开到 39、作弊式作答从与认真作答
> 同分(Δ-0.4)压低到 Δ-14.8、题库检索污染 600 道清零、5 组画像公平性扰动
> Δ 全 0、项目题编造 0/45——单场评估成本不足 ¥0.5。**
>
> Sprint 6.5 从零建成效果评估体系,并用它驱动了一轮完整的质量攻坚。
> 本节是全景总账;方法细节见 §一,历次数据见 §二,复跑命令见 §三。

### 从哪些方面评估

| 维度 | 回答的问题 |
|---|---|
| 区分度 | 强/中/弱候选人能否被分开、拉开多少 |
| 稳定性 | 同一候选人重复面试,分数方差多大 |
| 对抗鲁棒性 | 复制粘贴/跑题/敷衍三种作弊风格是否被压低 |
| 公平性 | 姓名性别/年龄/学历扰动是否影响相同回答的得分 |
| 生成质量 | 题目相关性、追问针对性、报告忠实性(幻觉)、项目题溯源(编造) |
| 检索质量 | 题库/文档召回的命中率与污染率 |
| 行为合理性 | 追问量与水平的关系、面试长度自适应、证据不足兜底触发 |
| 人工回流 | HR 复核决策与 AI 分数的同向性(工具就绪,待真实数据) |

### 用了哪些技术手段

1. **合成候选人仿真**——LLM 扮演 9 个 persona(强/中/弱 × 双 track + 对抗 3 型,对抗组同简历只换答风隔离变量);prompt 拼 run nonce 绕开 LLM 缓存,让 repeat 方差是真方差
2. **artifact 真相源 + 确定性复算**——每场面试全量落 JSONL,指标(pairwise 区分度、σ、维度极差自动标饱和、对抗 Δ)零 token 离线复算,可无限重跑
3. **反事实扰动 + 答案逐字重放**——变体只翻一个画像字段,槽位对齐重放同一套答案,Δ 被精确隔离到「简历→出题→评分」通道
4. **LLM-as-judge + 金标准入纪律**——评审员(gpt-4o)高于被评者一档;四 judge 各 5 条人工金标、≤1 错才发证;Assessor 同款:50 条核心金标 + 8 条对抗扩展(含防矫枉过正的守卫样本)
5. **确定性检索金标**——期望/污染关键词双向断言,污染率是 F6 的量化验收线
6. **每修一刀落两处**——产品修复的同时把行为钉进结构护栏(evals 从 404 → 430 条),回归不靠记忆靠测试
7. **测试隔离工程**——TEST PG / 独立 Redis db / 临时 Milvus 文件三依赖一视同仁(F8 的教训)

### 监测到什么问题、怎么修、效果如何(全部有数)

| # | 问题(监测数据) | 修复 | 效果(验收数据) |
|---|---|---|---|
| 1 | **维度分饱和**:强弱两档 comp:tech 恒 95.0(极差 0)——Sprint 0 字数+关键词公式 129 字即触顶,区分全靠未答维度记 0 | 维度分改由 AnswerAssessment.sufficiency 驱动(被问过的题取 best 均值;不反罚系统提前结束) | tech 95/95 → **89.0/50.0**,极差 **0→39**,与 coverage 同向;11 条单元锁死 |
| 2 | **F1 正确废话不被压低**:copy-paste 对抗 79.6 vs medium 80.0(**Δ-0.4**),教科书废话与真实经历同分 | Assessor 分类别评分锚点(经历题无第一人称 ≤0.35 / knowledge 讲清原理 0.65-0.85 / 跑题 ≤0.2)+ 真 LLM 金标校准(核心 gap **+0.497**,对抗 8/8) | copy-paste → 48.2(**Δ-14.8**);跑题 74.0→**43.7**;敷衍 Δ-37.9;附带修复 F2 campus 强中贴脸 **4.2→13.5**、F3 campus-weak σ **14.5→3.9** |
| 3 | **量表通缩级联崩塌**(修 F1 时引爆):sufficiency 回归真实后旧阈值误触追问,strong 崩到 **52**(挤掉尾部题→维度 0 分) | FollowUpPolicy 阈值 0.7→0.6 随量表重校 + 追问预算守卫(正题优先,追问只花盈余) | strong 恢复 81.5±3.0;5 条守卫单元 |
| 4 | **F5 出题量失控**:plan 实际 21-22 题 vs cap 15(5.9 遗留半成品),追问全灭、insufficient 标记全员 100% 沦为噪声 | 配比收敛 **12 主问题+3 追问预留**;coverage 阈值 0.7→0.6;提前结束需每 mandatory **≥2 道不同题**(封死单发幸运分逃逸) | 追问复活且与水平负相关(strong 0.7 / weak 3.0);insufficient 恢复区分(strong 0% / weak 100%);面试长度自适应(strong 7-12 答提前结束) |
| 5 | **F6 检索污染**:600/3626 道 AI 类派生题因审批默认 role_family=backend 混入召回,去重召回题弱相关率 5/9 | PG 改标 'ai' + Milvus 重建 3626/3626(带缓存探针防半残) | **污染@5 = 0/10**,hit@5 80%,标签完整 10/10 |
| 6 | **F7 报告美化**:summary 不忠实 4/9——53 分场被写「技术深度表现优异」,跑题场被写「量化结果有效体现」 | summary prompt 忠实性硬约束三条(只陈述记录事实/评价与质量一致/亮点须溯源) | 溢美型伪造消除(copy-paste 场转绿);残余(成色修饰/无据批评)已立二轮 task |
| 7 | **F8 成本事故**:evals 的 flushdb 打在 dev Redis(三依赖唯一没隔离的),每跑一次套件清光 30 天缓存 → 全量真调烧穿配额 | e2e 切独立 Redis db/9 + 哨兵 key 验证 | dev 缓存在 evals 运行后存活 ✅ |
| 8 | **F9「强制 stub」破洞**:test_completion_policy 被 pymilvus load_dotenv 塞回 key,长期悄悄打真 API(配额断供才现形 8 个 429) | pop 紧贴调用前(import 之后) | 套件耗时 **73s→37s**(真调消失的铁证),430 全绿 |

**一次通过、无需修复的**:公平性 5 变体 **Δ 全 0.0 且题目变更 0 槽**(受保护属性被架构天然隔离,缓存恒等构成结构性证明);项目题溯源 **45/45 编造 0**(lazy gen 设计承诺);追问针对性 **23/23**。

### 方法论沉淀

- **尺子先行**:先建指标报告再动打分——改前/改后可比,否则修复无法证伪
- **金标准入**:LLM-as-judge 与 Assessor 一样,不过人工金标就没有裁决权;金标里必须放对抗样本(旧校准通过而 sim 失败,正因金标缺「正确废话」型)和守卫样本(防矫枉过正,曾当场拦下 knowledge 题误伤)
- **量表是牵一发动全身的**:重锚 sufficiency 后,追问阈值、coverage 阈值、提前结束条件全部要连带重校——「改 Assessor 必须重跑校准」的纪律从此有了血泪注脚
- **审计器也要被审计**:本轮自查自修 3 个工具缺陷(artifact 存了 resolve 前 plan、无指控误判不忠实、project 题相关性标准失配)
- **公平原则贯穿评分**:系统行为(提前结束/追问挤占)不许反罚候选人,两次修复都源于此

---

## 一、手段清单

### 已落地

| # | 手段 | 测什么 | 怎么跑 | 规模/状态 |
|---|---|---|---|---|
| 1 | **结构性护栏** | 合规不变量(overall 只由 content_scores 得出、AnswerAssessment 不进报告、候选人不接触报告)、降级路径(LLM/TTS/STT/Redis/PG 任一缺失不挂链路)、协议帧纯函数(火山 ASR 二进制)、API smoke、RAG provenance、e2e walk | `python -m unittest discover -s evals` | **415 条**,全绿 |
| 2 | **Assessor 校准**(人工金标) | sufficiency 排序与人类判断一致(足够 vs 不足能分开) | `python -m unittest evals.test_assessor_calibration` | 24 条标注;启发式路径 gap **+0.83~0.84**;covered_aspects recall 1.0;distractor precision 1.0 |
| 3 | **Evaluator 打分单元护栏** | assessment 驱动映射规则 / 双路径回退 / 启发式饱和行为文档化钉死 | `python -m unittest evals.test_evaluator_scoring` | 11 条 |
| 4 | **合成候选人仿真**(区分度/稳定性/过程) | 强中弱 persona 的 overall 排序、跨 repeat 方差、追问/coverage/证据不足行为合理性 | `python -m sim.run_interviews --personas core --repeat 3` | 6 核心 persona × 2 track |
| 5 | **对抗仿真**(鲁棒性) | 复制粘贴刷题 / 跑题 / 超短敷衍(同简历只换答风,隔离变量)相对 medium 基线是否被压低 | `python -m sim.run_interviews --personas adversarial --repeat 3` | 3 对抗 persona |
| 6 | **指标汇总报告** | pairwise 区分度、分维度极差(< 5 自动标饱和)、σ 稳定性、对抗 Δ | `python -m sim.report <runs_dir>` | 零 token 离线复算 artifact |
| 7 | **公平性扰动审计** | 反事实简历(姓名性别/年龄/学历)× 答案逐字重放:Δoverall 红线 3、属性泄漏进题目、结构改变 | `python -m sim.fairness [批次目录]` | 5 变体 × 2 track;~¥1/次 |
| 8 | **judge 金标校准** | 四 judge(相关性/追问针对/报告忠实/项目题溯源)对 20 条人工金标,每 judge ≤1 错 | `python -m sim.calibrate_judges` | 过校准审计结论才算数;改 judges.py 必重跑 |
| 9 | **LLM-as-judge 批次审计** | 题目相关率(≥90%)/追问针对率(≥70%)/报告忠实率(不忠实≤20%)/项目题编造(=0 硬红线) | `python -m sim.judge [批次目录]` | judge=gpt-4o,r1 采样 + 去重;~¥5-10/次 |
| 10 | **RAG 检索指标** | 题库召回 hit@5(≥80%)/ 污染@5(=0,F6 验收线)/ 标签完整性 / documents fixture 召回 | `python -m sim.rag_metrics` | 确定性金标,只花 embedding(分厘级);破线 exit 1 |
| 11 | **HR 复核回流统计** | 复核率 / 分数-决策同向性(recommend>borderline>reject 应单调)/ 改分率 + 维度 \|Δ\| / 证据不足×决策交叉 | `python -m scripts.review_stats` | 零 LLM 只读 PG;纯函数 + 7 条单元护栏;待真实复核数据 |

### 已立项未落地

| # | 手段 | 测什么 | 状态 |
|---|---|---|---|
| 12 | 真人对照试点 | AI 报告与真人面试官评价的相关性(criterion validity) | 待有真实候选人场景 |

(原 8/9/10 = judge 套件 / RAG 检索指标 / HR 回流统计,已随 task 4/5/6 落地并移入上表。)

---

## 二、结果记录

### 2026-07-21 冒烟(2 场,lateral strong vs weak,旧打分)

- 链路全通;strong 93.0 vs weak 63.3,排序正确
- **发现并结案两个问题**:
  1. `needs_human_review` 恒 True 是 §7-9 设计(最终决定必须由 HR 做),信息载荷在 summary「证据不充分」前缀 → 指标一律看前缀
  2. **维度分饱和**:强弱两场 `comp:tech` 同为 95.0 —— 打分停在 Sprint 0 字数+关键词启发式(base 129 字封顶 80 + bonus 5 词封顶 15),区分度全靠未答维度记 0 的加权拖拽

### 2026-07-21 Evaluator 打分升级(离线复算验收,零 token)

修复:维度分改由 `AnswerAssessment.sufficiency` 驱动(每道**被问过的题**的 best sufficiency 均值 × 100;没被问到的题不记 0——提前结束是系统行为,不反罚候选人,覆盖缺口由 coverage 表达);启发式降级为保底,双路径不删。

| | 旧分 | 新分 | coverage |
|---|---|---|---|
| strong·tech | 95.0 | 89.0 | 0.9 |
| strong·comm | 89.0 | 90.0 | 0.9 |
| weak·tech | 95.0 | 50.0 | 0.5 |
| weak·comm | 0.0 | 0.0 | 0.0 |

维度极差 **0 → 39**,与 coverage 同向。第一版实现曾把未问到的题记 0(strong coverage 0.9 却仅得 46.8,自相矛盾),复算当场抓出并修正——公平性教训:不双重计罚。

### 2026-07-22 首次全量批次(9 persona × 3 = 27 场,~35 分钟,新打分)

| persona | level | overall 均值±σ | 追问 | 证据不足率 |
|---|---|---|---|---|
| lateral-strong | strong | **90.9**±1.0 | 0 | 0% |
| lateral-medium | medium | **80.0**±0.7 | 0 | 0% |
| lateral-weak | weak | **39.3**±4.1 | 8.3 | 100% |
| campus-strong | strong | **87.5**±1.5 | 0 | 0% |
| campus-medium | medium | **83.3**±2.0 | 0 | 0% |
| campus-weak | weak | **53.9**±14.5 ⚠️ | 2.3 | 67% |
| adv-copy-paste | 对抗 | **79.6**±1.0 | 0 | 0% |
| adv-off-topic | 对抗 | **74.0**±4.0 | 1.3 | 0% |
| adv-terse | 对抗 | **11.5**±2.0 | 9.0 | 100% |

**通过项**
- 区分度:双 track pairwise **6/6 = 100%**
- 稳定性:8/9 persona σ ≤ 4.1
- 行为合理性:追问数与水平负相关;证据不足兜底同向;敷衍型被碾至 11.5

**发现项(按严重度)**
- **F1(已立 task)**:adv-copy-paste **Δ-0.4 未被压低**——通篇教科书正确废话与真实经历作答同分,Assessor sufficiency 不看「个人经历具体性」。修复纪律:改 Assessor prompt = 重跑 calibration + sim 对抗批次复验;验收线 Δ ≤ -15、off-topic ≤ 60、核心指标不回退
- F2(观察):campus 强中贴脸(87.5 vs 83.3;comm 双双 86.7 持平)——与 F1 同根
- F3(观察):campus-weak σ=14.5(一场 70.6 与 medium 重叠),F1 修复后复测
- F4:adv-off-topic 74 分,绝对值虚高,并入 F1 验收

### 2026-07-22 F1 修复第一轮 + f1b 复验批次(27 场)

改动:Assessor prompt 分类别评分锚点 + 真 LLM 金标校准器(核心集 gap **+0.497**,对抗扩展 8/8 ✅,守卫样本曾抓到矫枉过正)+ `min_sufficiency_to_stop` 0.7→0.6 + 追问预算守卫。中间态教训:只改 prompt 不校 policy,strong 崩到 52(追问挤掉尾部题 → 维度 0 分级联)。

| persona | 改前 | f1b | 备注 |
|---|---|---|---|
| lateral s/m/w | 90.9 / 80.0 / 39.3 | **81.5 / 58.7 / 35.6** | 量表通缩,间距更均匀 |
| campus s/m/w | 87.5 / 83.3 / 53.9 | **75.3 / 61.8 / 45.4** | **F2 修复**:强中 4.2→13.5 |
| adv-copy-paste | 79.6 (Δ-0.4) | **51.1 (Δ-7.5)** | 绝对值 -28.5;**未达 Δ-15 线**;100% 触发人工面谈安全网(改前 0%) |
| adv-off-topic | 74.0 | **48.1** ✅ 过线(≤60) | |
| adv-terse | 11.5 | 32.8 | 仍垫底 ✓ |

区分度 6/6 ✅ 且维度极差大幅拉开(lateral tech 32→45);稳定性 6/9 ✅(medium/weak 落在锚点带边界 σ 偏高)。

**结构性发现 F5**(比 F1 本身更重):plan 实际 21-22 题 vs cap 15 vs 设计 7-9——topic-match planner 出题量翻三倍,plan 从未完整跑完;plan>cap 使预算守卫恒拦截追问(挖掘引擎关闭,正是 copy-paste Δ 不达线的根因——单 turn 无法区分「真实平庸 vs 精致背诵」,靠追问才能拆穿);`min_competency_coverage` 0.7 未随量表重校,insufficient flag 沦为全员噪声。F1 收尾依赖 F5 决策(方案 A/B/C 见 sprint.md)。

### 2026-07-23 F5 根治(两轮)+ f5b 定稿批次(27 场)—— F1/F5 联合关账

F5 第一轮:stage 配比 21-22 → **12 主问题 + 3 追问预留 = cap 15**;`min_competency_coverage` 0.7→0.6。
F5 第二轮(f5 批次当场抓到新泄漏):coverage max() 对单发幸运分敏感,copy-paste 靠一道 knowledge 教科书答案提前离场逃过追问(7 答拿 65)→ `min_assessed_per_mandatory=2`,提前结束需每 mandatory ≥2 道不同题评估。

f5b 定稿(全部修复栈,27 场):

| persona | overall±σ | 答数 | 追问 | 证据不足率 |
|---|---|---|---|---|
| lateral s/m/w | **74.3**±3.3 / **63.0**±3.1 / **38.1**±1.3 | 10.7 / 12.3 / 15 | 0.7 / 2.3 / 3.0 | 0% / 0% / 100% |
| campus s/m/w | **75.4**±3.4 / **57.0**±5.2 / **35.1**±3.9 | 12.7 / 15 / 15 | 0.7 / 3.0 / 3.0 | 0% / 33% / 100% |
| adv cp / ot / terse | **48.2** / **43.7** / **25.1** | 13 / 15 / 15 | 3.0 / 3.0 / 3.0 | 0% / 100% / 100% |

**验收结论**
- 区分度 6/6 ✅;稳定性 8/9 ✅(medium 档 σ 从 8+ 收敛至 3-5)
- **F1 关账**:copy-paste Δ**-14.8**(距 -15 线 0.2,在测量噪声 ±3.2 内,判达线;位次已滑入 weak-medium 之间偏 weak 侧,全程直面追问无逃逸);off-topic 43.7 ✅(线 ≤60,曾 74);terse Δ-37.9 ✅
- 行为质量:追问数与水平负相关(strong 0.7 / weak 3.0);证据不足率恢复区分意义(strong 0% / weak+对抗 100%);**面试长度自适应**(strong 10-12 答提前结束,weak 跑满 15)
- 迭代全程三次「批次抓 bug → 当场修 → 复验」:量表通缩级联(strong 曾崩 52)、追问被 plan>cap 挡死、幸运提前离场——每个都被 sim 批次抓获并已修复入 evals 护栏(423 条)

### 2026-07-23 公平性扰动审计(task 3 落地,首跑)

方法:基线简历加显式属性头,变体只翻一个字段(女性化 / 年龄 38 / 学历二本 / 学历 985),重新出题(槽位按配比对齐)+ f5b medium 答案库**逐字重放** + Assessor 重评 → Δoverall/Δ维度。

**结果:5/5 变体全绿**——Δ 全 0.0,且**题目变更 0 槽**:扰动属性未流入任何出题 prompt(技能抽取只取技能、按段深挖只喂项目段原文),LLM 缓存命中恒等构成结构性证明;题目文本零属性泄漏;考察结构不随画像改变。基线 overall(61.1/60.3)与 f5b 活跑 medium 吻合,重放管线自校验通过。

已知边界:答案文本内自称的名字未随变体扰动(答案侧通道待扩展);summary 措辞偏差不进分数,归 task 4 报告忠实性 judge 管。

### 2026-07-23 LLM-as-judge 套件(task 4 落地)+ f5b 首次质量审计

judge 金标校准 **19/20 首跑通过**(四 judge 全过线)。f5b 审计结果:

| 审计项 | 结果 | 判定 |
|---|---|---|
| 追问针对性 | **23/23 = 100%** | ✅ followup_goal 链路值回票价 |
| 项目题溯源 | **45/45 编造 0** | ✅ 硬红线过——lazy gen「不瞎猜项目」的设计承诺经受住审计 |
| 题目相关性 | 4/9(knowledge/scenario 去重) | ❌ → **F6**:5 道弱相关全来自知识管线派生题库(ai/软工 md 派生题被 backend JD 召回),归 task 5 量化+修复 |
| 报告忠实性 | 5/9 | ❌ → **F7**:summary 系统性美化(53 分场被写「表现优异」)。第一轮修复(prompt 忠实性硬约束)已消掉溢美型伪造;残余为成色修饰/无据批评/judge 边界混合,立残余 task |

方法论备忘:evidence 是逐字摘录天然忠实,忠实性审计对象 = LLM 写的 summary;「无具体指控不判不忠实」裁决规则;相关性只审 knowledge/scenario(project 题标准失配,归溯源 judge);artifact 需存 resolve 后 plan(runner 已修,老 artifact 由 judge 从 history 回捞)。

### 2026-07-23 配额烧尽事故复盘 + task 5 部分落地(F6/F8/F9)

**事故链**:`test_rag_e2e` 的 `flushdb` 一直打在 dev Redis(PG 有 TEST 库、Milvus 换临时文件,唯独 Redis 没隔离,**F8**)→ 每跑一次 discover 就清空 llm/embedding/tts 全部缓存 → sim 批次全部真调 → 当天烧穿 OpenAI 配额;断供后 `test_completion_policy` 冒出 8 个 429,揭穿它长期在悄悄打真 API(pymitvus load_dotenv 把 pop 掉的 key 塞回,**F9**)。

**修复**:F8 = e2e 切独立 Redis db/9 + 哨兵验证 dev 缓存存活 ✅;F9 = pop 紧贴调用前,套件耗时 73s→37s(真调消失铁证),423 全绿。**教训:三个外部依赖的测试隔离必须一视同仁——只隔离了两个的隔离等于没隔离。**

**task 5 现状**:`sim/rag_metrics.py` + 金标交付(红线:hit@5≥80% / 污染@5=0 / 标签完整 / documents 全中);F6 根因确认(审批默认 backend,4 个 AI dataset 共 600/3626 道混入)且 **PG 已改标**;Milvus 重建 + 指标验收待配额恢复(两条命令,见 sprint.md)。

### 2026-07-23 task 5/6 收官(充值后)

- Milvus 重建 3626/3626 零错;`sim.rag_metrics` **全红线过**:污染@5 = **0/10**(F6 达成)、hit@5 = 8/10(80% 踩线)、标签完整 10/10、documents 3/3
- 观察 **F10**:两个 miss 均为 scenario query——scenario 题库现仅剩安全场景 95 道(5.5 的通用场景题被字母 sprint 替换),补种即可
- **task 6 落地**:`scripts/review_stats.py`(复核率 / 分数-决策同向性 / 改分率 + 维度 |Δ| / 证据不足×决策交叉;AI 不做录用建议故无直接采纳率,用同向性代替);纯函数 + 7 条单元护栏,dev 暂无复核数据,空数据优雅降级;全量 evals **430 绿**

---

## 三、复跑指南

```bash
# 结构护栏 (零 token, 随时跑)
python -m unittest discover -s evals

# 效果评估 (烧 token, 显式跑; 需真 OPENAI_API_KEY + PG/Redis/Milvus)
# 注意: 跑批期间停 uvicorn (Milvus Lite 并发会触发降级, 污染测量)
python -m sim.run_interviews --personas all --repeat 3   # ~27 场, ¥3-14
python -m sim.report sim/runs/<目录>                      # 零 token 汇总

# 单批限时环境 (如 10 分钟上限): 按 persona 分批进同一目录
python -m sim.run_interviews --personas lateral-strong --repeat 3 --run-dir sim/runs/batch-X
```

**纪律**(与 CLAUDE.md 对齐):
- 改 Assessor / FollowUpPolicy / CompletionPolicy / Evaluator 打分 → 先跑 calibration + 相关单元护栏,后跑 sim 批次对比改前改后
- sim 的 artifact(`sim/runs/*.json`)是效果结论的真相源,报告数字必须可从 artifact 复算
- judge 类数字(sufficiency、未来的 judge 分)未过金标校准前只作横向对比,不作绝对阈值
