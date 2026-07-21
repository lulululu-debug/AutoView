"""Sprint B+D: query 文本 → matched topic 列表 (跟题库题目实际挂的 topic 做语义匹配).

knowledge 题召回时, 用 HR 配置的 aspect (维度名+描述) 与 LLM 抽出的 resume skill 作为
query, 跟候选 topic 算 cosine 距离, 距离 < 阈值的视为 matched, 后续把
Milvus questions 召回限定在 topic IN matched_topics 内.

候选 topic 来自 db.list_question_topics(): 题库里至少挂着一道题的 topic 值,
chunk 级优先 (语料目录二级, 粒度 = 子主题), dataset 级兜底 —— 与 Milvus 行的
写入逻辑同一优先级 (Sprint E, 此前只用 dataset.topic, 一个大 dataset 内部没法
按子主题选题)。

设计取舍:
- threshold 用 cosine 距离 (越小越像), 默认 0.45 (相似度 > 0.55)。
- stub embedding (无 OPENAI key) → 返空匹配, 调用方自然 fallback 走原 RAG 路径.
- 没 topic 元数据 (老 dataset 且无 chunk 谱系) → 返空, fallback 同上.
- topic embedding 在单次 match 内缓存一次, 多 query 共用; 调用方多次调用之间不缓存
  (依赖 src.embeddings 的 Redis cache, OPENAI 调用本身不重发).
"""
from __future__ import annotations

import json
import logging
import re

import numpy as np

from src import db, embeddings, llm

log = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.45     # cosine distance; 0 完全相同, 2 完全相反

# ---- LLM 兜底匹配 (Sprint E) ----
# 短技能词 (如 "LangChain", "RAG") 与 topic 标签的 embedding 距离实测 0.66+,
# 远超阈值 —— "LangChain 是 Agent 框架" 是世界知识, 不体现在短字符串的向量
# 距离里。embedding 匹配剩下的 unmatched skill 走一次 LLM 归类兜底;
# LLM 失败 / stub / 超时 → 返空 = 维持纯 embedding 结果, 不引入新单点依赖。
_LLM_MATCH_SYSTEM = (
    "你是技能归类器。给定一组候选人技能和一组题库主题 (含说明),"
    "判断每个技能落在哪些主题的考察范围内。\n"
    "判断标准: 候选人写了该技能, 面试官用该主题的题目去考察 TA 是否合理?"
    "合理就归入。\n"
    "- 框架/工具/协议归入它所服务的领域主题。例如: 某 Agent 开发框架、"
    "LLM SDK、RAG 检索组件 → Agent/大模型类主题; 某单元测试框架 → 测试类主题;"
    " 某 Java Web 框架 → Java 类主题\n"
    "- 通用编程语言、前端三件套等与所有主题都不特定相关的技能 → 空列表\n"
    "- 主题名必须逐字取自给定主题列表, 不要自创; 一个技能可属于多个主题\n\n"
    "先逐个技能一句话思考它是什么、服务什么领域, 最后输出 JSON"
    ' (含所有技能, 未归类的给空列表): {"技能名": ["主题名", ...], ...}'
)
_LLM_MATCH_TIMEOUT = 15.0     # 含逐技能思考, 比纯 JSON 输出略放宽
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def match_topics_for_queries(
    queries: list[str],
    *,
    threshold: float = _DEFAULT_THRESHOLD,
) -> dict[str, list[str]]:
    """每个 query 返回 matched topic 列表 (按相似度从高到低)。

    queries: aspect 描述串 / resume skill 名词等任意中英文。
    返回 dict[query → list[topic]]; 没匹配的 query 是空 list。
    题库为空 / 全空 topic / stub embedding → 所有 query 返空 list。
    """
    if not queries:
        return {}

    topics = db.list_question_topics()
    if not topics:
        log.info("match_topics: 无可匹配 topic (题库为空或题目全无 topic)")
        return {q: [] for q in queries}

    # 一次性 embed 所有 topic, 进 cache
    topic_vecs: dict[str, np.ndarray] = {}
    for t in topics:
        v = embeddings.embed(t)
        if embeddings.is_stub_vector(v):
            log.info("match_topics: stub embedding, 全部返空匹配")
            return {q: [] for q in queries}
        topic_vecs[t] = _normalize(np.array(v, dtype=np.float32))

    out: dict[str, list[str]] = {}
    for q in queries:
        qv = embeddings.embed(q)
        if embeddings.is_stub_vector(qv):
            out[q] = []
            continue
        qn = _normalize(np.array(qv, dtype=np.float32))
        scored: list[tuple[str, float]] = []
        for topic, tn in topic_vecs.items():
            dist = 1.0 - float(qn @ tn)    # cosine distance, normalize 后 dot = cosine
            if dist < threshold:
                scored.append((topic, dist))
        scored.sort(key=lambda x: x[1])
        out[q] = [t for t, _ in scored]
    return out


def llm_match_skills(
    skills: list[str],
    topics: list[str] | None = None,
) -> dict[str, list[str]]:
    """LLM 兜底: 把 embedding 没匹配上的技能归类到题库 topic。
    返回 dict[skill → list[topic]] (只含归类成功的 skill)。
    LLM stub / 超时 / 非法 JSON → 返 {} (调用方维持纯 embedding 结果)。"""
    if not skills:
        return {}
    if topics is None:
        topics = db.list_question_topics()
    if not topics:
        return {}

    # dataset 描述给 LLM 当归类依据; 复合 topic "X/y" 用 X 的描述。
    # PG 不可用时描述留空 (归类只靠 topic 名), 不阻塞。
    try:
        desc_by_topic = {
            d.topic: (d.description or "").strip()
            for d in db.list_datasets() if d.topic.strip()
        }
    except Exception:
        desc_by_topic = {}
    topic_lines = []
    for t in topics:
        base = t.split("/", 1)[0]
        desc = desc_by_topic.get(base, "")
        topic_lines.append(f"- {t}" + (f": {desc}" if desc else ""))

    user = (
        "题库主题列表:\n" + "\n".join(topic_lines)
        + "\n\n候选人技能:\n" + "\n".join(f"- {s}" for s in skills)
    )
    try:
        raw = llm.complete(
            _LLM_MATCH_SYSTEM, user,
            max_tokens=1200, timeout=_LLM_MATCH_TIMEOUT,
        )
    except Exception as e:
        log.warning("llm_match_skills LLM error: %s", e)
        return {}
    if not raw or llm.is_stub(raw):
        return {}
    return _parse_llm_skill_matches(raw, valid_topics=topics, skills=skills)


def _parse_llm_skill_matches(
    raw: str, *, valid_topics: list[str], skills: list[str],
) -> dict[str, list[str]]:
    """解析 + 校验 LLM 归类输出:
    - 只保留输入 skills 里的键 (防 LLM 自造技能)
    - 只保留 valid_topics 里逐字存在的主题 (防 LLM 自造主题)
    - 空列表的 skill 不进结果 (语义 = 未归类)"""
    m = _JSON_RE.search(raw)
    if not m:
        log.info("llm_match_skills: no JSON; first 200: %r", raw[:200])
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        log.info("llm_match_skills JSON parse error: %s", e)
        return {}
    if not isinstance(data, dict):
        return {}

    skill_set = set(skills)
    topic_set = set(valid_topics)
    out: dict[str, list[str]] = {}
    for skill, ts in data.items():
        if skill not in skill_set or not isinstance(ts, list):
            continue
        kept = [t for t in ts if isinstance(t, str) and t in topic_set]
        if kept:
            out[skill] = kept
    return out


def union_matched_topics(matches: dict[str, list[str]]) -> list[str]:
    """合并所有 query 的 matched topic 取并集, 按"首次出现顺序"稳定排序
    (字典 + 列表保留顺序)。"""
    seen: dict[str, None] = {}
    for ts in matches.values():
        for t in ts:
            seen.setdefault(t, None)
    return list(seen.keys())


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n == 0.0:
        return v
    return v / n
