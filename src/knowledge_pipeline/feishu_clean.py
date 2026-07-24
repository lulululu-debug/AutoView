"""飞书 docx blocks -> 合规 md + 自动清洗 —— Sprint 6.7 task 2。

把 connectors.feishu.fetch_document_blocks 的原始 block 列表转成
知识管线切片器 (parser.py) 认识的 md: H1=文档标题 / H2=专题 / H3=知识点
(corpus-md-conventions 约定)。全部纯函数, 零网络零 infra, 金标 eval 直接锁。

相对上次手工整理 raw_content 的自动化升级:
- 标题层级来自 block 类型, 不再靠人眼断行
- 图片是独立 block 直接跳过 —— "裸 uuid 图片行"在源头消失
  (clean_md 仍保留 uuid 行清理, 兜底 raw_content 形态的旧输入)
- 表格从 (row_size, column_size, cells) 重建 md 表格, 不再是拍平文本

block_type 映射按 2026-07 开放平台文档实现, 升级 API 版本时对着文档核对:
1=page 2=text 3..11=heading1..9 12=bullet 13=ordered 14=code 15=quote
17=todo 22=divider 27=image 31=table 32=table_cell
"""
from __future__ import annotations

import re

_HEADING_TYPES = {3: "##", 4: "###", 5: "####"}  # heading1-3; 更深的降为加粗行
_SKIP_TYPES = {22, 27}                            # divider / image

# 内容字段名按 block_type 取 (飞书的字段名与类型一一对应)
_FIELD_BY_TYPE = {
    1: "page", 2: "text", 3: "heading1", 4: "heading2", 5: "heading3",
    6: "heading4", 7: "heading5", 8: "heading6", 9: "heading7",
    10: "heading8", 11: "heading9", 12: "bullet", 13: "ordered",
    14: "code", 15: "quote", 17: "todo",
}

# ---- 清洗规则 (实战坑样本, memory: corpus-md-conventions) ----

_UUID_LINE = re.compile(
    r"^\s*[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\s*$"
)
_BARE_IMAGE_LINE = re.compile(r"^\s*\S+\.(png|jpg|jpeg|gif|webp)\s*$", re.I)
_PROMO_MARKERS = ("关注公众号", "欢迎关注", "点赞收藏", "一键三连", "扫码关注")
_NAV_MARKERS = ("《后续文章》", "上一篇:", "上一篇：", "下一篇:", "下一篇：", "系列导航")
_SPACED_BOLD = re.compile(r"\*\*\s+([^*]+?)\s+\*\*|\*\*([^*]+?)\s+\*\*|\*\*\s+([^*]+?)\*\*")


def _elements_text(elements: list[dict]) -> str:
    """拼接 text_run 内容; 加粗按修复后的形态输出 (**text** 不留内侧空格)。"""
    parts: list[str] = []
    for el in elements or []:
        run = el.get("text_run")
        if not run:
            continue
        content = run.get("content", "")
        style = run.get("text_element_style") or {}
        if style.get("bold") and content.strip():
            stripped = content.strip()
            lead = content[: len(content) - len(content.lstrip())]
            trail = content[len(content.rstrip()):]
            parts.append(f"{lead}**{stripped}**{trail}")
        else:
            parts.append(content)
    return "".join(parts)


def _block_text(block: dict) -> str:
    field = _FIELD_BY_TYPE.get(block.get("block_type", -1))
    if not field:
        return ""
    payload = block.get(field) or {}
    return _elements_text(payload.get("elements") or [])


def _render_table(block: dict, by_id: dict[str, dict]) -> str:
    """按 (row_size, column_size) + 行优先 cell 列表重建 md 表格。"""
    prop = (block.get("table") or {}).get("property") or {}
    rows, cols = prop.get("row_size", 0), prop.get("column_size", 0)
    cell_ids = block.get("children") or []
    if not rows or not cols or len(cell_ids) < rows * cols:
        return ""

    def cell_text(cell_id: str) -> str:
        cell = by_id.get(cell_id) or {}
        texts = [
            _block_text(by_id[cid])
            for cid in (cell.get("children") or []) if cid in by_id
        ]
        return " ".join(t.strip() for t in texts if t.strip()).replace("|", "\\|")

    lines = []
    for r in range(rows):
        cells = [cell_text(cell_ids[r * cols + c]) for c in range(cols)]
        lines.append("| " + " | ".join(cells) + " |")
        if r == 0:
            lines.append("|" + "---|" * cols)
    return "\n".join(lines)


def blocks_to_md(blocks: list[dict]) -> tuple[str, str]:
    """(文档标题, md 正文)。blocks 为 API 文档序; 表格 cell 及其子块被
    表格渲染消费, 主循环跳过。"""
    by_id = {b.get("block_id", f"_{i}"): b for i, b in enumerate(blocks)}

    consumed: set[str] = set()  # 被表格消费的 cell / cell 子块
    for b in blocks:
        if b.get("block_type") == 31:
            for cid in b.get("children") or []:
                consumed.add(cid)
                cell = by_id.get(cid) or {}
                consumed.update(cell.get("children") or [])

    title = ""
    out: list[str] = []
    ordered_n = 0
    for b in blocks:
        bid = b.get("block_id", "")
        btype = b.get("block_type", -1)
        if bid in consumed or btype in _SKIP_TYPES or btype == 32:
            continue
        if btype != 13:
            ordered_n = 0

        if btype == 1:
            title = _block_text(b).strip()
            if title:
                out.append(f"# {title}")
        elif btype in _HEADING_TYPES:
            text = _block_text(b).strip()
            if text:
                out.append(f"{_HEADING_TYPES[btype]} {text}")
        elif 6 <= btype <= 11:  # heading4+ 降为加粗行 (切片器 H4 并入上级)
            text = _block_text(b).strip()
            if text:
                out.append(f"**{text}**")
        elif btype == 2 or btype == 15:
            text = _block_text(b).strip()
            if text:
                out.append(f"> {text}" if btype == 15 else text)
        elif btype == 12:
            out.append(f"- {_block_text(b).strip()}")
        elif btype == 13:
            ordered_n += 1
            out.append(f"{ordered_n}. {_block_text(b).strip()}")
        elif btype == 14:
            lang = (b.get("code") or {}).get("style", {}).get("language", "")
            out.append(f"```{lang}\n{_block_text(b).rstrip()}\n```")
        elif btype == 17:
            out.append(f"- [ ] {_block_text(b).strip()}")
        elif btype == 31:
            table = _render_table(b, by_id)
            if table:
                out.append(table)
        else:
            text = _block_text(b).strip()
            if text:
                out.append(text)

    return title, clean_md("\n\n".join(out))


def clean_md(md: str) -> str:
    """通用清洗: 对 blocks 输出做兜底, 对 raw_content 形态输入做主力清洗。"""
    out: list[str] = []
    for line in md.split("\n"):
        s = line.strip()
        if _UUID_LINE.match(s) or _BARE_IMAGE_LINE.match(s):
            continue
        if any(m in s for m in _PROMO_MARKERS):
            continue
        if any(s.startswith(m) or m in s[:16] for m in _NAV_MARKERS):
            continue
        # 错位加粗修复: ** text ** / **text ** / ** text**
        line = _SPACED_BOLD.sub(
            lambda m: f"**{(m.group(1) or m.group(2) or m.group(3)).strip()}**",
            line,
        )
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)  # 连续空行折叠
    return text.strip() + "\n"
