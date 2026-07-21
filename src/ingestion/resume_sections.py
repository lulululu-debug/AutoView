"""Resume 语义分段 —— Sprint F。

把 parse-resume 出来的纯文本切成语义段 (个人信息/教育/项目一/项目二/实习一...)。
project/internship/work 段是项目深挖题的出题单元: planner.resolve_lazy_questions
按段轮询, 一段一题定向深挖, 取代"按维度语义召回 top-3 混合切片"。

三级降级链 (CLAUDE.md: 新增 LLM 调用必带 timeout + 启发式 fallback):
1. llm_anchor  LLM 只输出每段的【首行锚点】, 代码按锚点在原文定位切割。
               段文本永远是原文连续子串, LLM 无法改写/漏抄简历内容
               (简历是候选人原始材料, 保真 = 公平性底线)。
               锚点定位失败 / 乱序 / 有效段 < 2 → 降级 2。
2. heuristic   节标题词表 + 日期区间行特征切分, 零 LLM 成本, 确定性。
3. whole_text  整份简历一个段, 让下游至少有兜底出题材料。

切片正确性由构造保证: 按锚点位置对原文做**连续**切分, 所有段拼回 == 原文,
不存在丢字。失败模式只可能是"边界切错位置", 后续 Phase 2 (parse-resume 返回
分段给候选人确认) 提供人工兜底。
"""
from __future__ import annotations

import json
import logging
import re

from src import llm
from src.schemas import (
    RESUME_DEEPDIVE_TYPES,
    RESUME_SECTION_TYPES,
    ResumeSection,
)

log = logging.getLogger(__name__)

_SEGMENT_TIMEOUT = 20.0        # 离线后台任务, 比 Assessor(10s) 宽松, 但仍防卡死
_SEGMENT_MAX_TOKENS = 1200     # 只输出锚点不复述正文, 1200 足够 ~20 段
_MIN_TEXT_CHARS = 80           # 过短文本直接 whole_text, 没有切的价值
_MIN_LLM_SECTIONS = 2          # LLM 路径至少定位出 2 段才算成功, 否则降级

_SEGMENT_SYSTEM = (
    "你是简历结构分析器。给定一份简历纯文本, 识别它的语义分段。"
    "只输出每段的边界锚点, 不要复述或改写简历内容。\n"
    "分段规则:\n"
    "- type 取值: personal_info(姓名/联系方式等开头信息), education(教育经历), "
    "project(单个项目经历), internship(单段实习经历), work(单段正式工作经历), "
    "skills(技能清单), award(获奖/荣誉), other(无法归类)\n"
    "- 项目/实习/工作经历下的【每一个条目单独成段】: 三个项目 = 三个 project 段\n"
    "- first_line 必须逐字复制该段在原文中的第一行 (整行, 保留原有空格与标点), "
    "这是切割锚点, 改动一个字都会定位失败\n"
    "- title 给该段一个短标题: 项目/实习/工作段用项目名或公司名, 其他段用类别名\n"
    "- 段按原文出现顺序排列\n"
    '严格输出 JSON, 不要任何解释: {"sections": [{"type": "project", '
    '"title": "...", "first_line": "..."}, ...]}'
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def segment_resume(resume_text: str) -> list[ResumeSection]:
    """入口: 三级降级, 永远返回非空列表 (最差 whole_text 单段)。"""
    text = (resume_text or "").strip()
    if len(text) < _MIN_TEXT_CHARS:
        return _whole_text(text)

    sections = _segment_via_llm(text)
    if sections is not None:
        return sections

    sections = _segment_heuristic(text)
    if sections is not None:
        return sections

    return _whole_text(text)


def _whole_text(text: str) -> list[ResumeSection]:
    return [ResumeSection(
        type="other", title="简历全文", text=text, source="whole_text",
    )]


# ---------- 1) LLM 锚点路径 ----------

def _segment_via_llm(text: str) -> list[ResumeSection] | None:
    """LLM 出锚点 → 代码按锚点切原文。任何失败返 None 走启发式。"""
    try:
        raw = llm.complete(
            _SEGMENT_SYSTEM,
            f"简历全文:\n{text[:12000]}",
            max_tokens=_SEGMENT_MAX_TOKENS,
            timeout=_SEGMENT_TIMEOUT,
        )
    except Exception as e:
        log.warning("segment_resume LLM error: %s", e)
        return None
    if not raw or llm.is_stub(raw):
        return None

    m = _JSON_RE.search(raw)
    if m is None:
        log.warning("segment_resume: LLM 输出无 JSON; head=%r", raw[:120])
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        log.warning("segment_resume: JSON 解析失败: %s", e)
        return None
    items = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return None

    # 逐段定位锚点; 强制单调递增 (乱序锚点丢弃, 其文本并入前一段)
    located: list[tuple[int, str, str]] = []   # (pos, type, title)
    search_from = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        pos = _locate_anchor(text, str(it.get("first_line") or ""), search_from)
        if pos < 0:
            continue
        stype = str(it.get("type") or "other")
        if stype not in RESUME_SECTION_TYPES:
            stype = "other"
        located.append((pos, stype, str(it.get("title") or "").strip()))
        search_from = pos + 1

    if len(located) < _MIN_LLM_SECTIONS:
        log.info(
            "segment_resume: 锚点定位不足 (%d/%d), 降级启发式",
            len(located), len(items),
        )
        return None

    # 开头有前导文本 (通常姓名/联系方式) 且 LLM 没覆盖 → 补隐式 personal_info 段
    if located[0][0] > 0:
        preamble = text[: located[0][0]].strip()
        if preamble:
            located.insert(0, (0, "personal_info", "个人信息"))
        else:
            located[0] = (0, located[0][1], located[0][2])

    out: list[ResumeSection] = []
    for i, (pos, stype, title) in enumerate(located):
        end = located[i + 1][0] if i + 1 < len(located) else len(text)
        seg = text[pos:end].strip()
        if not seg:
            continue
        out.append(ResumeSection(
            type=stype,
            title=title or _default_title(stype, len(out)),
            text=seg,
            source="llm_anchor",
        ))
    if len(out) < _MIN_LLM_SECTIONS:
        return None
    return _merge_adjacent_non_deepdive(out)


def _merge_adjacent_non_deepdive(
    sections: list[ResumeSection],
) -> list[ResumeSection]:
    """相邻同类型的非 deep-dive 段合并 —— LLM 常把"教育经历"标题行和条目行
    分别锚成两段, 前端编辑器里全是碎卡片。project/internship/work **不合并**:
    一段一题是出题粒度契约, 合并会把两个项目并成一题。"""
    merged: list[ResumeSection] = []
    for s in sections:
        if (
            merged
            and s.type == merged[-1].type
            and s.type not in RESUME_DEEPDIVE_TYPES
        ):
            prev = merged[-1]
            merged[-1] = prev.model_copy(update={
                "text": prev.text + "\n" + s.text,
                "title": prev.title or s.title,
            })
        else:
            merged.append(s)
    return merged


def _locate_anchor(text: str, first_line: str, search_from: int) -> int:
    """在 text[search_from:] 里找锚点行, 返回**行首**绝对偏移; 找不到返 -1。
    宽松程度递进: 原样 → strip 后 → 前 20 字前缀 (LLM 偶尔截断长行)。"""
    fl = first_line.strip()
    if not fl:
        return -1
    for needle in (first_line, fl, fl[:20] if len(fl) >= 8 else ""):
        if not needle:
            continue
        idx = text.find(needle, search_from)
        if idx >= 0:
            # 锚点必须落在行首语义上 —— 回退到本行开头, 防前缀匹配切到行中
            return text.rfind("\n", 0, idx) + 1
    return -1


# ---------- 2) 启发式路径 ----------

# 节标题词表: 匹配"整行基本就是标题"的行 (短行 + 以词表开头)
_HEADER_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("education", ("教育经历", "教育背景", "Education")),
    ("project", ("项目经历", "项目经验", "项目实践", "Projects", "Project")),
    ("internship", ("实习经历", "实习经验", "Internship")),
    ("work", ("工作经历", "工作经验", "Work Experience", "Employment")),
    ("skills", ("专业技能", "技能清单", "技能", "Skills")),
    ("award", ("获奖", "荣誉奖项", "荣誉", "奖项", "Awards", "Honors")),
    ("other", ("自我评价", "个人评价", "校园经历", "Summary")),
)
_MAX_HEADER_LINE_CHARS = 16    # 标题行都很短; 长行含词表词多半是正文引用

# 条目边界: 含日期区间的行 (2023.01-2023.06 / 2023年1月至今 / 2023.01~present)
_DATE_RANGE_RE = re.compile(
    r"\d{4}\s*[./年]\s*\d{1,2}\s*月?\s*[-–—~〜至到]+\s*"
    r"(?:\d{4}\s*[./年]\s*\d{1,2}\s*月?|至今|现在|now|present)",
    re.IGNORECASE,
)


def _header_type_of(line: str) -> str | None:
    s = line.strip().strip(":： 　")
    if not s or len(s) > _MAX_HEADER_LINE_CHARS:
        return None
    for stype, words in _HEADER_TYPES:
        for w in words:
            if s == w or s.startswith(w):
                return stype
    return None


def _segment_heuristic(text: str) -> list[ResumeSection] | None:
    """节标题切大段, 经历类大段内再按日期区间行切条目。
    一个标题都没匹配到 → 返 None (交给 whole_text)。"""
    lines = text.split("\n")
    headers: list[tuple[int, str]] = []      # (line_idx, type)
    for i, line in enumerate(lines):
        stype = _header_type_of(line)
        if stype is not None:
            headers.append((i, stype))
    if not headers:
        return None

    out: list[ResumeSection] = []

    def _emit(stype: str, title: str, seg_lines: list[str]) -> None:
        seg = "\n".join(seg_lines).strip()
        if seg:
            out.append(ResumeSection(
                type=stype,
                title=title or _default_title(stype, len(out)),
                text=seg,
                source="heuristic",
            ))

    # 前导 (第一个标题之前) = 个人信息
    if headers[0][0] > 0:
        _emit("personal_info", "个人信息", lines[: headers[0][0]])

    for h, (start, stype) in enumerate(headers):
        end = headers[h + 1][0] if h + 1 < len(headers) else len(lines)
        block = lines[start:end]
        if stype in ("project", "internship", "work"):
            _emit_entries(stype, block, _emit)
        else:
            _emit(stype, lines[start].strip().strip(":： 　"), block)

    return out or None


def _emit_entries(stype: str, block: list[str], _emit) -> None:
    """经历类大段 → 按日期区间行切条目; 没有日期行就整段一个条目。
    条目边界取日期行的**上一行** (若上一行是短标题行), 让"项目名\\n时间段"
    这种常见排版把项目名留在条目内。"""
    starts: list[int] = []
    for i, line in enumerate(block):
        if i == 0:
            continue                      # 首行是节标题
        if _DATE_RANGE_RE.search(line):
            entry_start = i
            prev = block[i - 1].strip() if i - 1 > 0 else ""
            if prev and len(prev) <= 40 and not _DATE_RANGE_RE.search(prev) \
                    and not prev.endswith(("。", ".", "；", ";")):
                entry_start = i - 1
            if not starts or entry_start > starts[-1]:
                starts.append(entry_start)

    if not starts:
        _emit(stype, block[0].strip().strip(":： 　"), block)
        return

    for k, entry_start in enumerate(starts):
        # 第一个条目从节标题行开始 (标题行 + 引导文字都并入, 保证零丢字);
        # 后续条目从各自边界行开始
        begin = 0 if k == 0 else entry_start
        end = starts[k + 1] if k + 1 < len(starts) else len(block)
        entry_lines = block[begin:end]
        # 标题从条目自身行提取 (跳过节标题与引导行)
        title = _entry_title(block[entry_start:end], stype, k)
        _emit(stype, title, entry_lines)


def _entry_title(entry_lines: list[str], stype: str, idx: int) -> str:
    """条目标题 = 首个非空行去掉日期与分隔符; 空了用默认编号。"""
    for line in entry_lines:
        s = line.strip()
        if not s:
            continue
        s = _DATE_RANGE_RE.sub("", s)
        s = s.strip(" |,，、·—-–~〜")
        return s[:40] if s else _default_title(stype, idx)
    return _default_title(stype, idx)


_TYPE_LABELS = {
    "personal_info": "个人信息", "education": "教育经历", "project": "项目",
    "internship": "实习", "work": "工作经历", "skills": "专业技能",
    "award": "获奖荣誉", "other": "其他",
}


def _default_title(stype: str, idx: int) -> str:
    return f"{_TYPE_LABELS.get(stype, '段落')}{idx + 1}"


# ---------- Phase 2: 候选人确认后的分段规范化 ----------

_MAX_CONFIRMED_SECTIONS = 60       # 防恶意超长 payload; 正常简历 < 20 段
_MAX_SECTION_CHARS = 20_000        # 单段上限, 同上


def normalize_confirmed_sections(items: list) -> list[ResumeSection]:
    """把前端提交的候选人确认分段规范化为 ResumeSection 列表 (Sprint F Phase 2)。
    - type 非法 → other; title 截断; text 为空的段丢弃
    - source 一律强制 user_confirmed (客户端不可自标来源 —— 审计字段)
    - 超过上限的段截断丢弃 (记日志)
    items 元素可以是 dict 或带同名属性的对象 (api 的 ResumeSectionIn)。"""
    out: list[ResumeSection] = []
    for it in items[:_MAX_CONFIRMED_SECTIONS]:
        get = it.get if isinstance(it, dict) else lambda k, d="": getattr(it, k, d)
        text = str(get("text", "") or "").strip()
        if not text:
            continue
        stype = str(get("type", "other") or "other")
        if stype not in RESUME_SECTION_TYPES:
            stype = "other"
        title = str(get("title", "") or "").strip()[:80]
        out.append(ResumeSection(
            type=stype,
            title=title or _default_title(stype, len(out)),
            text=text[:_MAX_SECTION_CHARS],
            source="user_confirmed",
        ))
    if len(items) > _MAX_CONFIRMED_SECTIONS:
        log.warning(
            "normalize_confirmed_sections: %d 段超上限, 截断到 %d",
            len(items), _MAX_CONFIRMED_SECTIONS,
        )
    return out
