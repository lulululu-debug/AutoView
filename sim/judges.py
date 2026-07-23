"""LLM-as-judge 四件套 —— Sprint 6.5 task 4。

judge 用强模型 (env JUDGE_MODEL, 默认 gpt-4o —— 比被评的 gpt-4o-mini 高一档,
评审员不能弱于被评者)。全部严格 JSON; stub / 解析失败**直接 raise** ——
judge 是审计探针, 不许静默降级出假结论。

纪律: 任何 judge prompt 改动 = 重跑 sim.calibrate_judges (金标 20 条),
过了才许跑 sim.judge 审计; 未校准的 judge 分只作横向对比。
"""
from __future__ import annotations

import json
import os

from src import llm

_TIMEOUT = 45.0
_MAX_TOKENS = 700


def _judge_model() -> str:
    return os.environ.get("JUDGE_MODEL", "gpt-4o")


def _call(system: str, user: str) -> dict:
    raw = llm.complete(
        system, user,
        model=_judge_model(), max_tokens=_MAX_TOKENS, timeout=_TIMEOUT,
    )
    if not raw or llm.is_stub(raw):
        raise RuntimeError("judge LLM 不可用 (stub), 审计中止")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"judge 输出无 JSON: {raw[:120]!r}")
    return json.loads(raw[start:end + 1])


# ---- 1. 题目相关性 ----

_REL_SYSTEM = (
    "你是面试出题质量评审员。判断一道面试题是否与给定岗位 JD 和考察维度相关且"
    "有考察价值。宽严尺度: 与岗位技术栈/职责沾边即算相关; 只有明显跑偏"
    "(考察完全无关的领域/岗位) 或毫无考察价值的题才判不相关。"
    '严格输出 JSON: {"relevant": true/false, "reason": "一句话"}'
)


def judge_question_relevance(
    question: str, jd: str, competency_name: str,
) -> dict:
    return _call(_REL_SYSTEM, (
        f"岗位 JD:\n{jd}\n\n考察维度: {competency_name}\n\n"
        f"面试题: {question}\n\n这道题相关且有考察价值吗?"
    ))


# ---- 2. 追问针对性 ----

_FU_SYSTEM = (
    "你是面试追问质量评审员。已知候选人回答被评估出的缺失信号, 判断面试官的"
    "追问是否**针对性地**指向这些缺口 (或回答中明显的薄弱处), 而不是"
    "泛泛的\"能再展开吗/能举个例子吗\"式通用追问。"
    '严格输出 JSON: {"targeted": true/false, "reason": "一句话"}'
)


def judge_followup_targeting(
    question: str, answer: str, missing_signals: list[str], followup: str,
) -> dict:
    ms = "; ".join(missing_signals) if missing_signals else "(未提供)"
    return _call(_FU_SYSTEM, (
        f"原题: {question}\n候选人回答: {answer[:500]}\n"
        f"评估出的缺失信号: {ms}\n\n面试官的追问: {followup}\n\n"
        f"这条追问有针对性吗?"
    ))


# ---- 3. 报告忠实性 (summary 幻觉检查) ----

_FAITH_SYSTEM = (
    "你是评估报告忠实性审计员。对照面试逐字记录, 找出报告总结里**没有记录依据**"
    "的具体事实性论断 (如: 记录里从未出现的数字/项目/成果被写进了总结)。"
    "评价性措辞 (\"表现出色\") 与合理概括不算; 只抓无中生有的事实。"
    '严格输出 JSON: {"faithful": true/false, '
    '"unsupported_claims": ["逐条列出无依据论断, 没有则空数组"]}'
)


def judge_report_faithfulness(summary: str, transcript: str) -> dict:
    return _call(_FAITH_SYSTEM, (
        f"面试逐字记录:\n{transcript[:9000]}\n\n"
        f"报告总结:\n{summary}\n\n总结忠实于记录吗?"
    ))


# ---- 4. 项目题 faithfulness (RAGAS 思想) ----

_PROJ_SYSTEM = (
    "你是面试出题溯源审计员。项目深挖题只能针对候选人**真实提及过**的项目/经历"
    "(来源: 简历 或 自我介绍)。判断题目中提到的项目/系统/经历是否都能在给定"
    "材料中找到出处; 题目里凭空引入候选人没提过的项目 = 编造。"
    '严格输出 JSON: {"grounded": true/false, '
    '"invented": ["题目中凭空出现的项目/经历, 没有则空数组"]}'
)


def judge_project_question_faithfulness(
    question: str, resume: str, intro_text: str,
) -> dict:
    return _call(_PROJ_SYSTEM, (
        f"候选人简历:\n{resume}\n\n候选人自我介绍:\n{intro_text or '(无)'}\n\n"
        f"项目深挖题: {question}\n\n题目提到的项目/经历都有出处吗?"
    ))
