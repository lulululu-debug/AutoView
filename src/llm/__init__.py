"""统一 LLM 调用入口。

所有 agent 通过 complete() 调 LLM, 不直接使用 anthropic SDK。
读取 ANTHROPIC_API_KEY / ANTHROPIC_MODEL 环境变量。
未配置 key 或 SDK 不可用时, 返回前缀为 "[stub]" 的占位文本,
便于骨架阶段在本地端到端跑通; 调用方可识别该前缀并回退到模板。

Redis 缓存(Sprint 1 第 4 项):
- 命中: 直接返回缓存, 不打 API。
- 未命中: 调 LLM, 把结果写回缓存(stub 不写)。
- Redis 不可用: 缓存层静默降级, complete() 仍然工作。
- 缓存键见 src/cache/llm_cache.make_key, TTL 由 LLM_CACHE_TTL_SECONDS 控制。
"""
from __future__ import annotations

import os

from src.cache import llm_cache

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
STUB_PREFIX = "[stub]"


def complete(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 1024,
) -> str:
    """单次同步 LLM 调用, 返回纯文本(已 strip)。透明 Redis 缓存。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _stub(user)

    resolved_model = model or DEFAULT_MODEL
    cache_key = llm_cache.make_key(system, user, resolved_model, max_tokens)

    cached = llm_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from anthropic import Anthropic
    except ImportError:
        return _stub(user)

    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=resolved_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts: list[str] = []
    for block in resp.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    result = "".join(parts).strip()

    # stub 与空字符串都不入缓存: stub 应该让下次调用有机会重试到真实 API,
    # 空字符串没有复用价值且会把后续命中误判为"已无 token"。
    if result and not is_stub(result):
        llm_cache.set(cache_key, result)

    return result


def is_stub(text: str) -> bool:
    """判断 complete() 返回的是否为 stub 输出。"""
    return text.lstrip().lower().startswith(STUB_PREFIX)


def _stub(user: str) -> str:
    first_line = user.splitlines()[0] if user else ""
    return f"{STUB_PREFIX} {first_line[:120]}"
