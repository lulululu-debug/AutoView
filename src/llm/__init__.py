"""统一 LLM 调用入口。

所有 agent 通过 complete() 调 LLM, 不直接使用 OpenAI SDK。
读取 OPENAI_API_KEY / OPENAI_CHAT_MODEL / OPENAI_BASE_URL 环境变量。
未配置 key 或 SDK 不可用时, 返回前缀为 "[stub]" 的占位文本,
便于骨架阶段在本地端到端跑通; 调用方可识别该前缀并回退到模板。

Sprint 3 切到 OpenAI 的理由:
- Anthropic 没有 embedding API, 早期就只能用 OpenAI 做 embedding。
- 用户已有 OPENAI_API_KEY 在用, consolidate 到单一 provider:
  * key 管理 + 计费集中
  * cache key 含 model 名, 老的 anthropic 条目会自动 TTL 失效

Redis 缓存(Sprint 1):
- 命中: 直接返回缓存, 不打 API。
- 未命中: 调 LLM, 把结果写回缓存(stub 不写)。
- Redis 不可用: 缓存层静默降级, complete() 仍然工作。
"""
from __future__ import annotations

import logging
import os

from src.cache import llm_cache

DEFAULT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
STUB_PREFIX = "[stub]"

log = logging.getLogger(__name__)

# LLM_TRACE=1 时打 prompt 全文 + 返回结果到 logger, 默认关 (prompt 可能含
# 候选人 PII, 平时不希望进日志). 想 debug 一次面试就 env LLM_TRACE=1 跑.
def _trace_enabled() -> bool:
    return os.environ.get("LLM_TRACE", "").lower() in ("1", "true", "yes")


def complete(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 1024,
    timeout: float | None = None,
) -> str:
    """单次同步 LLM 调用, 返回纯文本(已 strip)。透明 Redis 缓存。
    timeout: openai SDK 级超时 (秒), Sprint 5.6 Assessor 用 10s 限制延迟突发;
    None = SDK 默认 (无显式超时)。超时会抛 openai.APITimeoutError, 调用方
    负责 try/except 把它降级到启发式 fallback。"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _stub(user)

    resolved_model = model or DEFAULT_MODEL
    cache_key = llm_cache.make_key(system, user, resolved_model, max_tokens)

    if _trace_enabled():
        # 全文打 prompt; 跑大面试时 user 体积可能很大 (resume + RAG chunks),
        # 别 truncate, debug 时就是要看全的.
        log.info(
            "LLM call model=%s max_tokens=%d cache_key=%s\n"
            "=== SYSTEM ===\n%s\n=== USER ===\n%s\n=== END PROMPT ===",
            resolved_model, max_tokens, cache_key, system, user,
        )

    cached = llm_cache.get(cache_key)
    if cached is not None:
        if _trace_enabled():
            log.info("LLM cache HIT key=%s\n=== CACHED RESULT ===\n%s\n=== END ===",
                     cache_key, cached)
        return cached

    try:
        from openai import OpenAI
    except ImportError:
        return _stub(user)

    base_url = os.environ.get("OPENAI_BASE_URL") or None
    client = OpenAI(api_key=api_key, base_url=base_url)
    create_kwargs: dict = {
        "model": resolved_model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if timeout is not None:
        create_kwargs["timeout"] = timeout
    resp = client.chat.completions.create(**create_kwargs)
    result = (resp.choices[0].message.content or "").strip()

    if _trace_enabled():
        log.info(
            "LLM cache MISS -> live call: key=%s\n=== RESULT ===\n%s\n=== END ===",
            cache_key, result,
        )

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
