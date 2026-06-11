"""中文友好的固定长度切片。

为什么不引 langchain RecursiveTextSplitter:
- 引入太重 (langchain 自带一堆依赖), 对中文也没有更好的处理
- 我们的需求简单: 500 字一片, 末尾 50 字重叠, 优先在句号断开

算法:
- 滑动窗口: 每窗最长 max_chars, 步长 max_chars - overlap
- 每窗末尾向前查找最近的句末符号 (。！？.!?\\n), 找到则在此断开
- 找不到就硬切 (避免单段超长卡死)
- 段落连接处 (\\n\\n) 算自然边界
"""
from __future__ import annotations

# 句末标点 + 换行 (中英文混排都覆盖)
_SENTENCE_ENDS = set("。！？.!?\n")
# 句末符号回退最大距离: 不要为了找断点把切片压得太短, 限制在窗口的后 20%
_BREAK_LOOKBACK_RATIO = 0.2


def chunk_text(text: str, *, max_chars: int = 500, overlap: int = 50) -> list[str]:
    """把文本切成至多 max_chars 字的若干片, 相邻片重叠 overlap 字。

    特殊情况:
    - 空 / 全空白: 返空列表
    - 文本本身 <= max_chars: 返单元素列表
    - overlap >= max_chars: 抛 ValueError, 避免无限循环
    """
    if overlap >= max_chars:
        raise ValueError(
            f"overlap ({overlap}) 必须小于 max_chars ({max_chars})"
        )
    if overlap < 0 or max_chars <= 0:
        raise ValueError(
            f"overlap 与 max_chars 必须为非负 / 正整数: {overlap}, {max_chars}"
        )

    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    i = 0
    n = len(text)
    lookback = int(max_chars * _BREAK_LOOKBACK_RATIO)

    while i < n:
        end = min(i + max_chars, n)

        # 如果还没到结尾, 向前找句末符号断开
        if end < n:
            limit = max(i + 1, end - lookback)
            for j in range(end, limit, -1):
                # text[j-1] 是 j 位置之前的字符
                if text[j - 1] in _SENTENCE_ENDS:
                    end = j
                    break

        chunk = text[i:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= n:
            break

        # 下一片往前回退 overlap 字。保证至少前进 1 字, 避免极端情况死循环。
        next_i = end - overlap
        i = next_i if next_i > i else i + 1

    return chunks
