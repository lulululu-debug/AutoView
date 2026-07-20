"""媒体能力探测 —— Sprint 6-4。

GET /media/config: 告诉前端本部署配了哪些媒体能力。
- stt_enabled: 麦克风入口显不显示 (false 时候选人只能打字, 打字永远可用)
- tts_enabled: 面试官音频拉不拉 (false 时前端可跳过 fetch 省一次 204 往返)

为什么独立 /media prefix 而不挂 /interviews 下: interviews router 有
GET /{session_id} 动态路由, /interviews/media-config 会被它先吞掉。
"""
from __future__ import annotations

from fastapi import APIRouter

from api.schemas import MediaConfig
from src import media_store, stt, tts

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/config", response_model=MediaConfig)
def media_config() -> MediaConfig:
    """部署级能力开关; 无鉴权 (不含敏感信息, 候选人端要用)。"""
    return MediaConfig(
        stt_enabled=stt.is_configured(),
        tts_enabled=tts.is_configured(),
        recording_enabled=media_store.is_configured(),
    )
