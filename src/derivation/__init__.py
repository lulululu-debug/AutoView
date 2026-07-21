"""Sprint B: 反向出题模块 —— 从 KnowledgeChunk 派生 DerivedQuestion 列表。

设计取舍:
- 不与 agent 体系混在一起 (assessor/evaluator/...): derivation 是离线流水线
  产物, 不进面试链路; agent 是面试运行时的同步组件。两者并发模型不同。
- LLM 失败 / JSON 非法 → 返回空 list, **绝不**降级到模板硬塞题。审核队列宁可
  少题, 也不能进无意义题污染 HR 工作流。
- prompt_version = sha256(_DERIVE_SYSTEM)[:8], system 改一个字符就失效, 配合
  src.llm 的 Redis 缓存 (cache_key = sha256(system+user+model+max_tokens))
  自动让旧 prompt 的结果失效, 新 prompt 重新调 LLM。

新增 LLM 调用约束 (CLAUDE.md):
- 必带 timeout: 默认 30s (Assessor 是 10s, derivation 离线场景宽松些, 但仍
  限制防卡死整批)
- 必带 fallback: 见上, 失败返空 list 不阻塞下一个 chunk
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re

from pydantic import ValidationError

from src import llm
from src.schemas import DerivedQuestion, KnowledgeChunk

log = logging.getLogger(__name__)

_DERIVE_KNOWLEDGE_SYSTEM = """你是一名资深技术面试官。
现在给你一段技术知识片段, 请反向出 1 到 5 道面试题, 并为每道题给出评分要点。

出题原则:
- 题目必须能用片段内容回答, 不要凭空脱离片段
- 短片段 (< 300 字) 出 1-2 道; 长片段出 3-5 道, 角度多样
- qtype 至少覆盖以下一种, 长片段尽量多样:
  - concept: "什么是 X" / "X 的作用是什么"
  - compare: "X 和 Y 有什么区别" / "X 相比 Y 的优劣"
  - scenario: "什么情况下用 X" / "X 在 Y 场景下怎么处理"
  - followup: "X 的实现原理" / "X 有什么坑 / 注意点"
- difficulty: easy=基础概念照搬, medium=需要理解机制, hard=需要深度实践或对比
- key_points 是评分时的踩分点, 3-5 条, 每条短句, 不要照抄题目

严格按以下 JSON 输出, 不要任何前后文字 / 代码块包装:
{
  "questions": [
    {
      "question_text": "...",
      "qtype": "concept",
      "difficulty": "easy",
      "key_points": ["...", "...", "..."]
    }
  ]
}
"""

# Sprint upload: 场景题专用 prompt. 跟 knowledge 区别在题目形态:
# knowledge 偏理论/原理 ("什么是 X / X 的实现"); scenario 偏现场决策
# ("线上 X 出错怎么排查 / Y 场景该用什么方案"). 同样输出 JSON 结构.
_DERIVE_SCENARIO_SYSTEM = """你是一名资深技术面试官。
现在给你一段技术知识片段, 请反向出 1 到 5 道【场景题】, 并为每道题给出评分要点。

场景题原则 (跟普通知识题不同):
- 题目必须给出一个【具体情境】, 让候选人现场推理决策, 而不是回顾经历
- 情境要贴合片段内容里的技术点, 让"懂这段内容"的人能答得更好
- 候选人需要根据情境做权衡 / 排查 / 选型 / 应急处理, 而不是背概念
- 短片段 (< 300 字) 出 1-2 道; 长片段出 3-5 道, 情境角度多样
- qtype 标记题目子类型 (但都是场景题):
  - scenario: 给情境让决策 ("线上 X 服务 ... 你怎么办?")
  - compare: 给两种方案让选择 ("场景下用 A 还是 B?")
  - followup: 在情境下追问后果 ("如果 X, 那 Y 会怎么样?")
  - concept: 罕见; 场景里偶尔需要先解释一个概念再答
- difficulty: easy=情境直接对应单一答案 / medium=需要权衡 / hard=需要多维度决策
- key_points 是评分时的踩分点, 3-5 条, 反映候选人应该想到的关键决策

严格按以下 JSON 输出, 不要任何前后文字 / 代码块包装:
{
  "questions": [
    {
      "question_text": "...",
      "qtype": "scenario",
      "difficulty": "medium",
      "key_points": ["...", "...", "..."]
    }
  ]
}
"""

_STARRED_HINT = "注意: 这段内容是作者标的【精选/重点】, 出题应更深入, 偏向 medium/hard。\n"

_VALID_QTYPES = frozenset({"concept", "compare", "scenario", "followup"})
_VALID_DIFFICULTIES = frozenset({"easy", "medium", "hard"})
_VALID_CATEGORIES = ("knowledge", "scenario")
_MAX_QUESTIONS_PER_CHUNK = 5
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_TOKENS = 1500


def _system_prompt_for(category: str) -> str:
    if category == "scenario":
        return _DERIVE_SCENARIO_SYSTEM
    return _DERIVE_KNOWLEDGE_SYSTEM


def prompt_version(category: str = "knowledge") -> str:
    """system prompt + category 的稳定指纹; 改 prompt 或换 category 都失效。
    draft_id 与 LLM cache 都依赖它, 让"切 category 重跑同 chunk"得到独立 draft。"""
    raw = (_system_prompt_for(category) + "|" + category).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


def derive_chunk(
    chunk: KnowledgeChunk,
    *,
    category: str = "knowledge",
    timeout: float = _DEFAULT_TIMEOUT,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> list[DerivedQuestion]:
    """单 chunk → 1-5 道题。LLM 失败 / 非法 JSON / 字段非法 → 返空 list。
    CLI 端负责拼 chunk_id / dataset_id / prompt_version / model 落 draft 表。
    Sprint upload: category 决定用 knowledge 还是 scenario prompt."""
    if category not in _VALID_CATEGORIES:
        log.warning("derive_chunk(%s) unknown category=%r, fallback knowledge",
                    chunk.chunk_id, category)
        category = "knowledge"
    user_prompt = _build_user_prompt(chunk)
    system = _system_prompt_for(category)

    try:
        raw = llm.complete(
            system, user_prompt,
            max_tokens=max_tokens, timeout=timeout,
        )
    except Exception as e:
        log.warning("derive_chunk(%s) LLM error: %s", chunk.chunk_id, e)
        return []

    if not raw or llm.is_stub(raw):
        log.info("derive_chunk(%s) stub/empty -> skip", chunk.chunk_id)
        return []

    return _parse_derived(raw, chunk_id=chunk.chunk_id)


def llm_model_name() -> str:
    """记进 QuestionDraft.llm_model 用; 跟 src.llm.complete 的解析逻辑保持一致。"""
    return os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")


def _build_user_prompt(chunk: KnowledgeChunk) -> str:
    domain_line = (
        f"领域: {chunk.domain} / {chunk.topic}"
        if chunk.topic else f"领域: {chunk.domain}"
    )
    parts = [
        domain_line,
        f"知识点路径: {' > '.join(chunk.heading_path)}",
    ]
    if chunk.doc_title:
        parts.append(f"出处文档: {chunk.doc_title}")
    parts.append("")
    if chunk.is_starred:
        parts.append(_STARRED_HINT)
    parts.append("知识片段内容:")
    parts.append(chunk.text)
    return "\n".join(parts)


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_derived(raw: str, *, chunk_id: str) -> list[DerivedQuestion]:
    """LLM 输出健壮解析: 即便混了 ```json 包装或前后说明文字, 取第一个 {...}
    完整块尝试 parse。校验失败的单题跳过, 不阻塞同批其他题。"""
    payload = _extract_json_object(raw)
    if payload is None:
        log.warning("derive(%s) no JSON object in output; first 200 chars: %r",
                    chunk_id, raw[:200])
        return []

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        log.warning("derive(%s) JSON parse error: %s; payload[:200]=%r",
                    chunk_id, e, payload[:200])
        return []

    items = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        log.warning("derive(%s) JSON missing 'questions' list", chunk_id)
        return []

    out: list[DerivedQuestion] = []
    for raw_q in items[:_MAX_QUESTIONS_PER_CHUNK]:
        if not isinstance(raw_q, dict):
            continue
        try:
            q = DerivedQuestion.model_validate(raw_q)
        except ValidationError as e:
            log.info("derive(%s) skip invalid question: %s", chunk_id, e)
            continue
        if q.qtype not in _VALID_QTYPES:
            log.info("derive(%s) skip invalid qtype=%r", chunk_id, q.qtype)
            continue
        if q.difficulty not in _VALID_DIFFICULTIES:
            log.info("derive(%s) skip invalid difficulty=%r", chunk_id, q.difficulty)
            continue
        if not q.question_text.strip():
            continue
        out.append(q)
    return out


def _extract_json_object(text: str) -> str | None:
    """匹配最外层 {...}, 容忍 ```json ... ``` 包装或前后说明文字。"""
    m = _JSON_BLOCK_RE.search(text)
    return m.group(0) if m else None


def make_draft_id(
    *, chunk_id: str, question_text: str, category: str = "knowledge",
) -> str:
    """draft_id = sha256(chunk_id + prompt_version(category) + question_text)[:16].
    同 chunk 同 prompt 同题文本 = 同 id (upsert 幂等); 改 prompt / 换 category
    重跑得到全新 draft_id 即便题文本巧合相同, 便于审计 prompt 演进。"""
    raw = f"{chunk_id}|{prompt_version(category)}|{question_text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]
