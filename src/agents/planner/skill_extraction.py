"""Sprint B+D: 从 Resume 抽出候选人提到的技术清单 (LLM).

输出 list[str] 让 plan() 拿来跟 topic 做语义匹配:
- matched topic → Milvus 召回带 topic 过滤
- unmatched skill → 落 skill_backlog 让 HR 扩库 (D2 设计)

设计取舍:
- LLM JSON mode 严格解析; 失败 / stub / 非 list 返 []. plan 走 fallback (无 skill).
- Resume 截前 4000 字 (足够覆盖技能板块, 避免长 PII 进 prompt 浪费 token).
- timeout 10s (跟 Assessor 一致, 别让面试链路被 LLM 卡住).
"""
from __future__ import annotations

import json
import logging
import re

from src import llm

log = logging.getLogger(__name__)


_SKILL_SYSTEM = (
    "你是简历解析助手。从下面 Resume 中抽出候选人明确提到的"
    "**技术名称** (编程语言 / 框架 / 中间件 / 数据库 / 工具 / 云服务)。\n"
    "规则:\n"
    "- 只抽 Resume 里**明确写出**的技术名词, 不要根据职位推测\n"
    "- 同义/简写归一化为大众通用名 (e.g. 'k8s' → 'Kubernetes', 'mq' → 'Kafka' 仅当上下文清晰)\n"
    "- 不要抽方法论 / 软技能 / 抽象概念 (e.g. '敏捷', '沟通', '架构设计' 不要)\n"
    "- 最多 20 个, 重要 / 高频的优先\n\n"
    '严格 JSON 输出, 不要任何前后文字: {"skills": ["...", "..."]}'
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_MAX_RESUME_CHARS = 4000
_MAX_SKILLS = 20
_TIMEOUT = 10.0


def extract_skills(resume_text: str) -> list[str]:
    """从 resume 抽 list[skill]; LLM stub / 错误 / 非法 JSON → 返 [].
    skill 用原文字符串, 不做大小写归一化 (后续语义匹配能对齐)."""
    text = (resume_text or "").strip()
    if not text:
        return []

    user = f"Resume:\n{text[:_MAX_RESUME_CHARS]}"
    try:
        raw = llm.complete(_SKILL_SYSTEM, user, max_tokens=400, timeout=_TIMEOUT)
    except Exception as e:
        log.warning("extract_skills LLM error: %s", e)
        return []

    if not raw or llm.is_stub(raw):
        return []

    m = _JSON_RE.search(raw)
    if not m:
        log.info("extract_skills: no JSON in output; first 200 chars: %r", raw[:200])
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        log.info("extract_skills JSON parse error: %s", e)
        return []

    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, list):
        return []

    out: list[str] = []
    for s in skills[:_MAX_SKILLS]:
        if not isinstance(s, str):
            continue
        s = s.strip()
        if s and s not in out:    # 顺手去重 (保留 LLM 给的顺序)
            out.append(s)
    return out
