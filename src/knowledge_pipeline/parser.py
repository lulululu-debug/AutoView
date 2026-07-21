"""Sprint upload: md parser + chunk 切分 lib。

抽自 scripts/ingest_md_corpus.py, 把所有"md → KnowledgeChunk list"逻辑做成
可复用的纯函数库, CLI (ingest_md_corpus) 和 HTTP upload endpoint 共用。

设计取舍跟 ingest_md_corpus 一致 (探查 docs/java/basis 后定):
- frontmatter 自己 parse 不引 PyYAML, 只取需要字段
- 切到 H3 叶子标题, H4 算进当前 chunk
- quality_tag 启发式 (link 比例 / 字数下限/上限)
- chunk_id = sha256(text)[:16], 跨 dataset 同内容自动去重 (PK 覆盖)
- 0 新依赖
"""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from src.schemas import KnowledgeChunk


# ---------- 文件级 normalize ----------

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def normalize(body: str) -> str:
    """跟内容质量无关的归一化: 剥 HTML 注释, 图片换成占位符避免 url 污染
    hash + embedding, 多空行折叠, 行尾去空白。"""
    body = _HTML_COMMENT_RE.sub("", body)
    body = _IMAGE_RE.sub(
        lambda m: f"[图: {m.group(1).strip() or '无描述'}]", body,
    )
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    body = _MULTI_NEWLINE_RE.sub("\n\n", body)
    return body.strip()


# ---------- frontmatter (轻量 YAML) ----------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """提取 YAML 头里的 title / description / category / tag(list);
    嵌套字段 (head / sitemap) 整段跳过。无 frontmatter 时 meta={} body=raw。"""
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw

    block = m.group(1)
    body = raw[m.end():]

    meta: dict = {"title": "", "description": "", "category": "", "tag": []}
    in_tag_list = False
    in_nested_block = False

    for line in block.split("\n"):
        if not line.strip():
            continue
        is_indented = line.startswith((" ", "\t"))

        if not is_indented:
            in_tag_list = False
            in_nested_block = False
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = _strip_quotes(val.strip())

            if key == "tag":
                in_tag_list = True
                meta["tag"] = []
            elif key in ("head", "sitemap"):
                in_nested_block = True
            elif key in meta and val:
                meta[key] = val
            continue

        if in_nested_block:
            continue
        if in_tag_list:
            stripped = line.strip()
            if stripped.startswith("- "):
                meta["tag"].append(_strip_quotes(stripped[2:].strip()))

    return meta, body


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


# ---------- heading-based split ----------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^(```|~~~)")
_MAX_SPLIT_LEVEL = 3        # 切到 H3; H4+ 算进当前 chunk


def split_to_sections(body: str) -> list[tuple[list[str], str]]:
    """按 heading 切 section, 只在 level ≤ _MAX_SPLIT_LEVEL 时切。
    代码块 ``` ... ``` 内的 # 不算 heading."""
    sections: list[tuple[list[str], str]] = []
    cur_path: list[tuple[int, str]] = []
    cur_buf: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(cur_buf).strip()
        if text and cur_path:
            sections.append(([t for _, t in cur_path], text))
        cur_buf.clear()

    for line in body.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            cur_buf.append(line)
            continue
        if in_fence:
            cur_buf.append(line)
            continue

        m = _HEADING_RE.match(line)
        if not m:
            cur_buf.append(line)
            continue

        level = len(m.group(1))
        title = m.group(2).strip()
        if level > _MAX_SPLIT_LEVEL:
            cur_buf.append(line)
            continue

        flush()
        while cur_path and cur_path[-1][0] >= level:
            cur_path.pop()
        cur_path.append((level, title))

    flush()
    return sections


# ---------- quality_tag (chunk 级判定) ----------

_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
_LOW_VALUE_CHARS = 100
_OVERSIZE_CHARS = 3000
_NAV_LINK_RATIO = 0.4


def classify_quality(text: str) -> str:
    """启发式: link 占比高 = 导航页, 极短 = 低价值, 极长 = 超大。"""
    total = len(text)
    if total < _LOW_VALUE_CHARS:
        return "low_value"
    link_chars = sum(len(m.group()) for m in _LINK_RE.finditer(text))
    if total > 0 and link_chars / total > _NAV_LINK_RATIO:
        return "navigation"
    if total > _OVERSIZE_CHARS:
        return "oversize"
    return "ok"


# ---------- path → domain / topic ----------

def parse_path(rel_path: PurePosixPath) -> tuple[str, str]:
    """rel_path 第一级 = domain, 第二级 = topic (若仍是目录)。"""
    parts = rel_path.parts
    domain = parts[0] if len(parts) >= 1 else ""
    if len(parts) >= 3:
        return domain, parts[1]
    return domain, ""


# ---------- 单文件 → chunks ----------

_STAR_PREFIXES = ("⭐", "⭐️")


def build_chunks(
    rel_path: PurePosixPath,
    raw: str,
    *,
    source_name: str,
    commit: str,
    dataset_id: str,
) -> list[KnowledgeChunk]:
    """单 md 文件 → list[KnowledgeChunk]. 空标题 / 全空 chunk 自动过滤。"""
    meta, body = parse_frontmatter(raw)
    body = normalize(body)
    sections = split_to_sections(body)

    domain, topic = parse_path(rel_path)
    chunks: list[KnowledgeChunk] = []
    for heading_path, text in sections:
        leaf = heading_path[-1] if heading_path else ""
        is_starred = leaf.startswith(_STAR_PREFIXES)
        full_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunks.append(KnowledgeChunk(
            chunk_id=full_hash[:16],
            source_repo=source_name,
            source_commit=commit,
            dataset_id=dataset_id,
            file_path=str(rel_path),
            doc_title=meta.get("title", ""),
            doc_tags=meta.get("tag", []),
            domain=domain,
            topic=topic,
            heading_path=heading_path,
            is_starred=is_starred,
            text=text,
            char_count=len(text),
            content_hash=full_hash,
            quality_tag=classify_quality(text),
        ))
    return chunks
