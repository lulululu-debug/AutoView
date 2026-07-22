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

### 已立项未落地(Sprint 6.5 余量)

| # | 手段 | 测什么 | 状态 |
|---|---|---|---|
| 7 | 公平性扰动审计 | 反事实简历(姓名性别/学校/年龄),答案复用 replay,Δoverall 超阈值红灯 | task 3;§7 偏见审计前置 |
| 8 | LLM-as-judge 套件 | 题目相关性 / 追问针对性 / 报告忠实性(evidence 溯源)/ lazy 题 faithfulness(RAGAS 思想自研,不引库) | task 4;judge 需先过 ~20 条金标校准 |
| 9 | RAG 检索指标 | 题库召回 precision/recall@k(seed 标签匹配,零 token)+ documents 召回标注集 | task 5 |
| 10 | HR 复核回流统计 | ReviewRecord 采纳率 / 改分率 / needs_human_review 比例 | task 6 |
| 11 | 真人对照试点 | AI 报告与真人面试官评价的相关性(criterion validity) | 待有真实候选人场景 |

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
