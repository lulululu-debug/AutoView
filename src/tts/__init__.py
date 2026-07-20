"""统一 TTS 调用入口 —— Sprint 6-2。

面试官语音的唯一合成点, 与 src/llm (chat) / src/embeddings (embedding) 同款
「单一调用点」模式: 业务层 (orchestrator / api) 只调 synthesize(), 不接触
任何厂商 SDK / HTTP 细节。

Provider 按 region 路由 (sprint.md Sprint 6 设计决策):
- TTS_PROVIDER=volc  国内 lane: 火山语音 (豆包), 中文自然度最佳
- TTS_PROVIDER=azure 海外 lane: Azure Speech, zh-CN 神经语音稳定可用
- 未配置 / 未知值 / 缺 key -> 返回 None (= 本模块的 "stub"), 前端静默退纯文字

硬约束 (CLAUDE.md: 新增 LLM 调用必带 timeout + fallback, TTS 同款):
- 每次 HTTP 调用带 10s timeout
- synthesize() **绝不 raise** —— 任何异常 (缺 key / 网络 / 厂商报错 / 超时)
  一律返回 None。媒体层挂了不能拖垮面试主链路, 文字问答是永远的保底。

Redis 缓存 (透明, 与 llm.complete 同款):
- key 含 (text, provider, voice), 换音色/厂商不撞老缓存
- 命中不打厂商 API; 未命中合成后写回; Redis 不可用静默直连
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.request
import uuid
from xml.sax.saxutils import escape as _xml_escape

from src.cache import tts_cache

log = logging.getLogger(__name__)

# 两个 provider 都要 mp3 输出, API 层与前端 <audio> 统一按这个 MIME 走
AUDIO_MIME = "audio/mpeg"

_TIMEOUT_SECONDS = 10.0
_VOLC_DEFAULT_VOICE = "BV700_streaming"          # 灿灿, 通用中文女声
_AZURE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"    # 晓晓, 通用中文女声


def is_configured() -> bool:
    """前端 media config 探测用 (Sprint 6-4): 当前部署能否合成语音。
    只查配置齐不齐, 不打厂商 API —— 配置齐但厂商挂了仍由 synthesize
    的 None 回退兜住。"""
    provider = os.environ.get("TTS_PROVIDER", "").strip().lower()
    if provider == "volc":
        return bool(
            os.environ.get("VOLC_TTS_APPID") and os.environ.get("VOLC_TTS_TOKEN")
        )
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_SPEECH_KEY")
            and os.environ.get("AZURE_SPEECH_REGION")
        )
    return False


def synthesize(text: str) -> bytes | None:
    """把面试官文本合成为 mp3 音频。透明 Redis 缓存。

    返回 None 的情况 (调用方一律按「无音频」降级, 不要区分对待):
    - TTS_PROVIDER 未配置 / 未知值
    - provider 对应的 key/region 缺失
    - 厂商调用异常: 网络 / 超时 / 返回错误码 / 空音频
    """
    text = text.strip()
    if not text:
        return None

    provider = os.environ.get("TTS_PROVIDER", "").strip().lower()
    if provider not in ("volc", "azure"):
        if provider:
            log.warning("未知 TTS_PROVIDER=%s, 跳过语音合成", provider)
        return None

    voice = _voice_for(provider)
    cache_key = tts_cache.make_key(text, provider, voice)
    cached = tts_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        if provider == "volc":
            audio = _synthesize_volc(text, voice)
        else:
            audio = _synthesize_azure(text, voice)
    except Exception:
        # 绝不向上抛: 面试主链路不能被 TTS 故障拖垮
        log.warning(
            "TTS 合成失败 provider=%s, 前端将静默退纯文字", provider, exc_info=True,
        )
        return None

    if audio:
        tts_cache.set(cache_key, audio)
    return audio


def _voice_for(provider: str) -> str:
    if provider == "volc":
        return os.environ.get("VOLC_TTS_VOICE") or _VOLC_DEFAULT_VOICE
    return os.environ.get("AZURE_TTS_VOICE") or _AZURE_DEFAULT_VOICE


def _synthesize_volc(text: str, voice: str) -> bytes | None:
    """火山语音 HTTP 合成 (非流式 query 模式)。
    文档: openspeech.bytedance.com /api/v1/tts, 成功 code=3000, data 为 base64。
    注意 Authorization 用的是火山特有的 "Bearer;{token}" 分号格式, 不是笔误。"""
    appid = os.environ.get("VOLC_TTS_APPID")
    token = os.environ.get("VOLC_TTS_TOKEN")
    if not appid or not token:
        return None
    cluster = os.environ.get("VOLC_TTS_CLUSTER", "volcano_tts")

    payload = {
        "app": {"appid": appid, "token": token, "cluster": cluster},
        "user": {"uid": "ai-interview"},
        "audio": {"voice_type": voice, "encoding": "mp3", "speed_ratio": 1.0},
        "request": {"reqid": str(uuid.uuid4()), "text": text, "operation": "query"},
    }
    req = urllib.request.Request(
        "https://openspeech.bytedance.com/api/v1/tts",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer;{token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("code") != 3000:
        log.warning(
            "火山 TTS 返回错误 code=%s message=%s",
            body.get("code"), body.get("message"),
        )
        return None
    data = body.get("data")
    return base64.b64decode(data) if data else None


def _synthesize_azure(text: str, voice: str) -> bytes | None:
    """Azure Speech REST 合成。响应体即 mp3 bytes。"""
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        return None

    ssml = (
        "<speak version='1.0' xml:lang='zh-CN'>"
        f"<voice name='{voice}'>{_xml_escape(text)}</voice>"
        "</speak>"
    )
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "ai-interview-platform",  # Azure 要求非空 UA
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        return resp.read() or None
