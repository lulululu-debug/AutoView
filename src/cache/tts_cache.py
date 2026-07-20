"""TTS 合成音频的 Redis 缓存 —— Sprint 6-2。

为什么:
- 同一段面试官提问文本, 每个中断恢复 / 刷新都会重拉音频; TTS 按字符计费,
  不缓存等于反复烧钱。plan 内题目文本固定, 命中率极高。
- key 拍进 (text, provider, voice): 换 provider / 换音色自然不撞老缓存,
  与 llm_cache 把 model 拍进 key 是同一个道理。

设计:
- key: "tts:<sha256(canonical)>", canonical = json.dumps({text, provider, voice})。
- value: base64 字符串。get_redis() 是 decode_responses=True 的 str 客户端,
  直接存原始 bytes 会在 decode 时炸, 所以走 base64 (音频几十 KB, 膨胀 1/3 可接受)。
- TTL: 默认 7 天, TTS_CACHE_TTL_SECONDS 覆盖。改音色 / 换 provider 走新 key,
  老条目自然 TTL 冲刷。

降级:
- Redis 未配置 / 报错时 get/set 静默 None / no-op, TTS 直连 provider,
  与 llm_cache 同款 —— 缓存层永远不能是主链路的故障点。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Optional

from src.cache.base import RedisNotConfigured, get_redis

_KEY_PREFIX = "tts:"
_DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 天


def make_key(text: str, provider: str, voice: str) -> str:
    """对 TTS 输入做规范化哈希。影响输出的字段 (text/provider/voice) 全拍进去。"""
    canonical = json.dumps(
        {"text": text, "provider": provider, "voice": voice},
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


def _ttl_seconds() -> int:
    raw = os.environ.get("TTS_CACHE_TTL_SECONDS")
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_TTL_SECONDS
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def get(key: str) -> Optional[bytes]:
    """命中返回音频 bytes; 未命中 / Redis 不可用 / 值损坏 一律返回 None。"""
    try:
        r = get_redis()
    except RedisNotConfigured:
        return None
    try:
        raw = r.get(key)
        if raw is None:
            return None
        return base64.b64decode(raw)
    except Exception:
        # 网络抖动 / base64 损坏都不应该影响 TTS 主链路
        return None


def set(key: str, audio: bytes) -> None:
    """写入缓存; Redis 不可用 / 报错时静默忽略。"""
    if not audio:
        return
    try:
        r = get_redis()
    except RedisNotConfigured:
        return
    try:
        r.set(key, base64.b64encode(audio).decode("ascii"), ex=_ttl_seconds())
    except Exception:
        return
