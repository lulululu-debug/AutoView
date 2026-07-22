"""Assessor Agent — Sprint 5.6 起每答一题就跑一次, 输出结构化 AnswerAssessment。

定位 vs Evaluator:
- Evaluator 只在面试结束跑一次, 输入完整 session, 输出 EvaluationReport, 是
  同步重调用 (一次 LLM 长生成 + RAG)。
- Assessor 在每 turn 跑, 输入单 (question, answer), 输出 AnswerAssessment, 是
  实时轻调用 (gpt-4o-mini + 10s 超时), 让 Interviewer 决策追问。两者并发模型 +
  延迟预算都不同, 所以独立模块, 绝不揉进 Evaluator。

合规约束 (CLAUDE.md):
- sufficiency / confidence 数字是 LLM-as-judge 的中间产物, 校准前不可信,
  绝对不展示给 HR UI 或候选人。只在 orchestrator 内做追问决策 + 落库审计。
- 上线前必须跑 calibration eval (20-30 条人工标注样本对齐 sufficiency 阈值);
  eval 不过 -> ASSESSOR_ENABLED 不开, 走原 _needs_followup 启发式。
- 双路径永远共存: LLM 调用失败/超时一律走启发式 fallback,
  绝不让 "LLM 挂了整条链路就挂" 进入面试链路。

启发式 fallback 设计:
- 沿用 Sprint 0 的 "长度 + specificity hints" 思路, 把它转成 sufficiency 数值。
- LLM 失败时 Assessor 仍能返回结构合理的 AnswerAssessment, missing_signals 是空
  数组, followup_goal 是通用文本 (没有 LLM 给的精准目标)。
- 这样 Interviewer 的下游决策路径不需要分 "Assessor 真用 vs Assessor 挂了" 两套,
  always 拿一个 AnswerAssessment 看就行。
"""
from __future__ import annotations

import json
import logging
import re

from src import llm
from src.schemas import (
    AnswerAssessment,
    CandidateAnswer,
    InterviewPlan,
    InterviewSession,
    JobContext,
    ProfileAspect,
    Question,
    QuestionCategory,
)

log = logging.getLogger(__name__)

# Sprint 5.6: 强制 mini 模型 + 短超时, 保证每 turn 的延迟可预测。
# 不读 OPENAI_CHAT_MODEL env, 因为生产 chat model 可能被切到更贵更慢的,
# Assessor 必须用便宜快的。
ASSESSOR_MODEL = "gpt-4o-mini"
ASSESSOR_TIMEOUT_SECONDS = 10.0
ASSESSOR_MAX_TOKENS = 600

# 启发式阈值: 与 Sprint 0 的 _needs_followup 思路一致, 用作 LLM 不可用时的兜底
_HINT_TOKENS = ("例如", "比如", "当时", "结果", "我们", "用了", "选择", "% ", "%")
_HEURISTIC_LEN_FULL = 120          # 字数到这个量级视为 sufficiency 上限附近
_HEURISTIC_HINT_BONUS = 0.2        # 含 hint +0.2 (有具体证据)

# Sprint 6.5 F1: 加分类别评分锚点。背景: 首次全量仿真批次发现"教科书式正确
# 废话" (内容正确但零第一人称细节) 与真实经历作答同分 (adv-copy-paste Δ-0.4),
# 跑题型仍拿 74 —— 旧 prompt 只说"判断信号是否充分", LLM 默认把"说得对"当
# "信号充分"。锚点按题目类别区分: knowledge 题讲对原理本来就该得分 (防矫枉
# 过正伤及校招), 经历向题目 (project/scenario/self_intro) 必须第一人称。
# 纪律: 本 prompt 任何改动 = 重跑 sim/calibrate_assessor (真 LLM 金标) +
# sim 对抗批次复验, 见 EVALUATION.md。
_SYSTEM_PROMPT = (
    "你是一名严苛的面试评估官, 任务是判断候选人对当前问题的回答信号是否充分,"
    "并指出缺什么、亮点在哪、是否需要追问以及追问该聚焦什么。\n"
    "sufficiency 评分锚点 (严格执行):\n"
    "- 答非所问 / 滑向无关话题 (职业规划、福利、闲聊等) → ≤ 0.2。\n"
    "- project_experience / scenario / self_intro 类题目: 只有**第一人称的具体"
    "经历**才是高分信号 —— 本人做了什么决策、为什么、量化结果 (数字/规模/指标)、"
    "踩过的坑。内容正确但通用化、像百科或博客摘抄、没有任何本人经历细节、"
    "任何人都能背出来的回答 → ≤ 0.35, 并在 missing_signals 写明"
    "\"缺个人实践细节\", followup_goal 指向其真实参与与具体贡献。\n"
    "- knowledge 类题目: 不强制个人经历。讲清概念原理、能对比取舍、说明适用"
    "场景 → 0.65-0.85; 再结合本人实践佐证 → 0.85+; 只有纯名词堆砌、背诵式罗列"
    "而讲不出为什么 → ≤ 0.5。\n"
    "- 有真实经历但缺量化数据或深度 → 0.4-0.6, followup_goal 聚焦缺口。\n"
    "**必须严格输出 JSON**, 不要任何解释、前后缀或代码块标记。"
)

_USER_TEMPLATE_BASE = (
    "题目类别: {category}\n"
    "题目: {question_text}\n"
    "候选人回答: {answer_text}\n"
)

_USER_TEMPLATE_ASPECTS = (
    "\n该题归属维度的画像 aspect 候选 (用短标签 A0/A1/... 引用):\n"
    "{aspects_block}\n"
    "在 covered_aspects 字段返回上面短标签列表中**本回答确实覆盖到**的标签 "
    "(精确, 不强行套), 没覆盖的不要列。\n"
)

_USER_TEMPLATE_JSON = (
    "\n请按下方 JSON schema 输出评估结果 (字段全填, 不要省略):\n"
    "{{\n"
    '  "sufficiency": <0.0-1.0 之间的浮点数, 1.0 = 信号充分, 0.0 = 完全没说到点>,\n'
    '  "confidence": <0.0-1.0 之间的浮点数, 表示你对该判断的把握度>,\n'
    '  "missing_signals": [<缺失信号的中文短句, 如 "缺量化数据" / "没讲为什么">],\n'
    '  "strengths": [<回答里的亮点, 中文短句>],\n'
    '  "concerns": [<虽然说了但让你担心的点, 中文短句>],\n'
    '  "followup_goal": "<若决定追问, 应当追什么的中文短句, 不追则空串>",\n'
    '  "stop_reason": "<不建议追问的理由, sufficient_signals/low_value/diminishing_returns 之一; 应追问则空串>",\n'
    '  "covered_aspects": [<本回答覆盖到的 aspect 短标签, 如 "A0", "A2"; 没有则空数组>]\n'
    "}}"
)


class _LLMStubFallback(RuntimeError):
    """LLM 走 stub (无 key / SDK 不可用), 不算"失败", 静默 fallback。"""


def assess(
    question: Question,
    answer: CandidateAnswer,
    session: InterviewSession,
    plan: InterviewPlan,
    job: JobContext | None = None,
) -> AnswerAssessment:
    """Assessor 入口: 单 (question, answer) -> AnswerAssessment。

    Sprint 5.9: 新加 job 参数, 用来取本题所在 competency 的 aspect 候选列表.
    返回的 AnswerAssessment.covered_aspects 是这次回答 covered 的 aspect_id 列表
    (LLM 路径走 prompt 让 LLM 选; 启发式 fallback 用名字关键词匹配兜底).
    self_intro 题 competency_id=None -> 不参与 aspect 匹配, covered_aspects=[]。

    Sprint 5.9 patch: 抗"复制粘贴同一段答案刷多题"。LLM-as-judge 只看单 turn,
    无法察觉本回答与前序回答字面完全一致 (实战遇到候选人粘贴同一段答 10 道
    knowledge 题, 拿到 0.8 sufficiency × 10 的 case). 入口先做硬规则短路:
    如本答案与 session.answers 中任一前序非空答案完全相同 (strip 后), 不调
    LLM, 直接返 sufficiency=0 / covered=[] / 触发追问, 让 Interviewer 不再
    把"粘贴的同一段"算作有效信号。
    """
    dup_idx = _find_duplicate_prior_answer(answer, session)
    if dup_idx is not None:
        return AnswerAssessment(
            question_id=question.question_id,
            sufficiency=0.0,
            confidence=1.0,
            missing_signals=[f"本回答与第 {dup_idx} 道题答案字面相同, 疑似复制粘贴"],
            strengths=[],
            concerns=["复制粘贴前序回答"],
            followup_goal="请针对本题给出独立的回答, 不要复用前面的答案",
            stop_reason="",
            covered_aspects=[],
        )

    relevant_aspects = _relevant_aspects(question, job)
    # 优先走 LLM; 任何异常 (超时 / API 报错 / JSON 解析失败 / schema 校验失败)
    # 一律 fallback 到启发式, 让 Interviewer 始终能拿到一个有效 AnswerAssessment。
    try:
        return _assess_via_llm(question, answer, relevant_aspects)
    except _LLMStubFallback:
        # 期望中的 stub 路径 (dev / eval), 不写 log.exception 防噪
        pass
    except Exception:
        log.exception(
            "Assessor LLM 路径失败, 走启发式 fallback (question_id=%s)",
            question.question_id,
        )
    return _heuristic_assessment(question, answer, relevant_aspects)


# 答案过短时不算复制粘贴 ("不知道" / "好的" 这种天然会重复, 不能误判).
# 20 字符是观察值: 短于此的回答本来 sufficiency 就低, 没必要再单独标记。
_DUP_MIN_LEN = 20


def _find_duplicate_prior_answer(
    answer: CandidateAnswer, session: InterviewSession,
) -> int | None:
    """如本答案与之前某一道题的答案 strip 后字面相同, 返该前序答案的 1-based
    序号; 否则返 None。

    设计:
    - 答案 < _DUP_MIN_LEN 不查: 短回答天然容易重合, 误判成本高。
    - 调用方 (orchestrator.submit_answer) 已经把 answer 追加进 session.answers,
      所以 session.answers[-1] 是 answer 自己, 必须排除自身。
    - 只看字面完全相同 (strip 后), 不做 fuzzy: 误报代价远高于漏报。
      Fuzzy / embedding 相似度留给后续 sprint。
    """
    text = answer.text.strip()
    if len(text) < _DUP_MIN_LEN:
        return None
    for i, prior in enumerate(session.answers):
        if prior.answer_id == answer.answer_id:
            continue  # 跳过自身
        if prior.text.strip() == text:
            return i + 1
    return None


def _relevant_aspects(
    question: Question, job: JobContext | None,
) -> list[ProfileAspect]:
    """筛出本题 competency 下的 aspect 候选。self_intro 题 competency_id=None,
    所有 aspect 都不匹配, 返 []。job 为 None / job.aspects 空也返 []。"""
    if job is None or not job.aspects:
        return []
    if question.competency_id is None:
        return []
    return [a for a in job.aspects if a.competency_id == question.competency_id]


# ---------- LLM 路径 ----------

def _aspects_block(aspects: list[ProfileAspect]) -> str:
    """把 aspect 列表渲染成短标签的 markdown 列表给 LLM 看。
    标签 A0/A1/... 是临时引用, 输出时 LLM 用这些标签, 我们映射回真实 aspect_id."""
    lines = []
    for i, a in enumerate(aspects):
        lines.append(f"- A{i}: {a.name} —— {a.description}")
    return "\n".join(lines)


def _assess_via_llm(
    question: Question,
    answer: CandidateAnswer,
    aspects: list[ProfileAspect],
) -> AnswerAssessment:
    user = _USER_TEMPLATE_BASE.format(
        category=question.category.value,
        question_text=question.text,
        answer_text=answer.text,
    )
    if aspects:
        user += _USER_TEMPLATE_ASPECTS.format(aspects_block=_aspects_block(aspects))
    user += _USER_TEMPLATE_JSON
    raw = llm.complete(
        _SYSTEM_PROMPT,
        user,
        model=ASSESSOR_MODEL,
        max_tokens=ASSESSOR_MAX_TOKENS,
        timeout=ASSESSOR_TIMEOUT_SECONDS,
    )
    if not raw or llm.is_stub(raw):
        # stub (无 key / SDK 不可用): 当成 LLM 不可用, 走启发式 (静默)
        raise _LLMStubFallback("LLM stub or empty response")

    payload = _extract_json(raw)
    # pydantic 会校验 sufficiency / confidence ∈ [0, 1]
    return AnswerAssessment(
        question_id=question.question_id,
        sufficiency=float(payload["sufficiency"]),
        confidence=float(payload["confidence"]),
        missing_signals=list(payload.get("missing_signals") or []),
        strengths=list(payload.get("strengths") or []),
        concerns=list(payload.get("concerns") or []),
        followup_goal=str(payload.get("followup_goal") or ""),
        stop_reason=str(payload.get("stop_reason") or ""),
        covered_aspects=_map_aspect_tags_back(
            payload.get("covered_aspects") or [], aspects,
        ),
    )


def _map_aspect_tags_back(
    tags: list, aspects: list[ProfileAspect],
) -> list[str]:
    """LLM 返的是临时短标签 ['A0', 'A2'], 映射回真实 aspect_id 列表。
    没匹配上的标签 (LLM 编出新的 / 越界) 静默丢弃, 不让 schema 校验挂面试。"""
    out: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        m = re.fullmatch(r"A(\d+)", tag.strip())
        if not m:
            continue
        idx = int(m.group(1))
        if 0 <= idx < len(aspects):
            out.append(aspects[idx].aspect_id)
    return out


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict:
    """LLM 偶尔会在 JSON 外包 ```json ... ``` 或加前缀; 抓第一个 {...} 块再 parse。
    JSON 不合法直接抛, 让 assess() 走 fallback。"""
    m = _JSON_OBJECT_RE.search(raw)
    if m is None:
        raise ValueError("no JSON object in LLM output")
    return json.loads(m.group(0))


# ---------- 启发式 fallback ----------

def _heuristic_assessment(
    question: Question,
    answer: CandidateAnswer,
    aspects: list[ProfileAspect],
) -> AnswerAssessment:
    """LLM 不可用时的启发式估算 —— 思路与 Sprint 0 的 _needs_followup 一致:
    回答越长越具体, sufficiency 越高。
    Sprint 5.9: 加 covered_aspects 启发式 —— aspect.name 切 2-gram 子串,
    任一子串出现在答案文本中 → 视为 covered。粗糙但 LLM 不可用时是唯一可用信号。"""
    text = answer.text.strip()
    n = len(text)
    hit_hint = any(h in text for h in _HINT_TOKENS)

    # 长度归一到 [0, 1], 含 hint 再 +0.2 (但封顶 0.95 留余地)
    length_score = min(n / _HEURISTIC_LEN_FULL, 1.0)
    sufficiency = min(length_score + (_HEURISTIC_HINT_BONUS if hit_hint else 0.0), 0.95)

    # self_intro 题永远视为 sufficient: Interviewer 已硬豁免追问, 这里给个高分
    # 跟 FollowUpPolicy 一致 (双保险, 防 policy 阈值改了忘改启发式)。
    if question.category is QuestionCategory.SELF_INTRO:
        sufficiency = max(sufficiency, 0.9)

    # 启发式没有 LLM 的 nuance, confidence 给中等 (0.3): 告诉下游"这是兜底估算"。
    confidence = 0.3

    missing_signals: list[str] = []
    concerns: list[str] = []
    followup_goal = ""
    stop_reason = ""
    if sufficiency < 0.5:
        missing_signals.append("回答较短或缺少具体例子")
        followup_goal = "让候选人补一个具体例子, 包含时间、数据或决策过程"
    else:
        stop_reason = "sufficient_signals"

    return AnswerAssessment(
        question_id=question.question_id,
        sufficiency=round(sufficiency, 3),
        confidence=confidence,
        missing_signals=missing_signals,
        strengths=[],
        concerns=concerns,
        followup_goal=followup_goal,
        stop_reason=stop_reason,
        covered_aspects=_heuristic_covered_aspects(question.text, text, aspects),
    )


# 答案需要 >= 这个字符数, 启发式才会把问题文本一并纳入 aspect 匹配源。
# 防止"看场景吧" / "改了就行" 这种敷衍回答被问题里的关键词带偏。
_HEURISTIC_ENGAGED_LEN = 30


def _heuristic_covered_aspects(
    question_text: str, answer_text: str, aspects: list[ProfileAspect],
) -> list[str]:
    """启发式 aspect 覆盖: aspect.name 切 2-gram 子串, 任一子串出现在
    (question + answer) 文本即视为 covered. 兜底信号, 比"全空"强但远不如 LLM 精准。

    设计:
    - answer 太短 (< _HEURISTIC_ENGAGED_LEN) 视为未engaged: 只看 answer, 不沾 question.
      不然"加机器消费就行"会因为题面里有"消息队列"而被乱标。
    - answer 足够长: 把 question + answer 拼起来扫. "答案engaged 了题目" 这个语义
      是合理的弱信号; LLM 路径会再精修。

    与 calibration eval 一起被锁定, 防它静默漂移。
    """
    if len(answer_text.strip()) >= _HEURISTIC_ENGAGED_LEN:
        haystack = question_text + "\n" + answer_text
    else:
        haystack = answer_text
    covered: list[str] = []
    for asp in aspects:
        name = asp.name
        # 2-gram 子串扫: name 太短 (<2) 时直接用 name 整体
        if len(name) < 2:
            substrings = [name]
        else:
            substrings = [name[i:i + 2] for i in range(len(name) - 1)]
        if any(sub and sub in haystack for sub in substrings):
            covered.append(asp.aspect_id)
    return covered
