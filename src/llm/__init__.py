"""统一 LLM 调用入口。

所有 agent 通过 complete() 调 LLM, 不直接使用 anthropic SDK。
读取 ANTHROPIC_API_KEY / ANTHROPIC_MODEL 环境变量。
未配置 key 或 SDK 不可用时, 返回前缀为 "[stub]" 的占位文本,
便于骨架阶段在本地端到端跑通; 调用方可识别该前缀并回退到模板。
"""
from __future__ import annotations

import os

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
STUB_PREFIX = "[stub]"


def complete(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 1024,
) -> str:
    """单次同步 LLM 调用, 返回纯文本(已 strip)。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _stub(user)
    try:
        from anthropic import Anthropic
    except ImportError:
        return _stub(user)

    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts: list[str] = []
    for block in resp.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def is_stub(text: str) -> bool:
    """判断 complete() 返回的是否为 stub 输出。"""
    return text.lstrip().lower().startswith(STUB_PREFIX)


def _stub(user: str) -> str:
    first_line = user.splitlines()[0] if user else ""
    return f"{STUB_PREFIX} {first_line[:120]}"
