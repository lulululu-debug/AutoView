"""统一流式 STT 调用入口 —— Sprint 6-4。

候选人语音转写的唯一接入点, 与 src/llm / src/tts 同款「单一调用点」模式:
API 层的 WS 代理只消费本模块的 SttStream 抽象, 不接触厂商协议细节。

Provider 按 region 路由 (sprint.md Sprint 6 设计决策):
- STT_PROVIDER=volc  国内 lane: 火山流式语音识别 (自研二进制 WS 协议, 见 volc.py)
- STT_PROVIDER=azure 海外 lane: **尚未实装** —— Azure Speech 的 WS 协议要单独
  适配, 先留 seam; 海外部署在此之前候选人走打字路径 (打字永远是保底)
- 未配置 / 未知值 / 缺 key -> create_stream() 返回 None, 前端隐藏麦克风入口

硬约束 (与 tts 同款):
- create_stream / send_audio / receive 的异常都不许炸到面试主链路;
  API 层把任何异常翻译成 {"type":"error"} 消息 + 关连接, 前端退打字。
- websockets 库不可用时 (裁剪部署) 与未配置同待遇 —— import 惰性, 同
  src/llm 里 openai SDK 的处理。

音频契约 (前端 stt.ts 与 volc.py 共同遵守):
- PCM 16-bit LE, 16kHz, 单声道; 前端每 ~100ms 一个二进制分片。
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class SttEvent:
    """厂商流的一次转写事件。

    kind:
    - partial: 中间假设, text 为**当前累计全文** (前端整体替换预览, 不做增量拼接)
    - final:   本次识别定稿, text 为累计全文 (前端落 textarea)
    - done:    厂商流正常结束 (final 之后)
    - error:   厂商流异常, message 给人看
    """
    kind: str
    text: str = ""
    message: str = ""


class SttStream(ABC):
    """一次语音转写会话 (连接建立 -> 推音频 -> 收事件 -> 结束)。"""

    @abstractmethod
    async def send_audio(self, chunk: bytes) -> None:
        """推一个 PCM 分片。"""

    @abstractmethod
    async def finish(self) -> None:
        """告知厂商音频结束 (触发最终定稿)。"""

    @abstractmethod
    async def receive(self) -> SttEvent | None:
        """收下一个事件; 流已关闭返回 None。"""

    @abstractmethod
    async def close(self) -> None:
        """无条件释放连接 (幂等)。"""


def is_configured() -> bool:
    """前端 media config 探测用: 当前部署能否提供语音转写。"""
    provider = os.environ.get("STT_PROVIDER", "").strip().lower()
    if provider == "volc":
        return bool(
            os.environ.get("VOLC_STT_APPID") and os.environ.get("VOLC_STT_TOKEN")
        )
    if provider == "azure":
        log.warning("STT_PROVIDER=azure 尚未实装, 前端将走打字路径")
        return False
    if provider:
        log.warning("未知 STT_PROVIDER=%s, 前端将走打字路径", provider)
    return False


async def create_stream() -> SttStream | None:
    """建一次厂商转写会话; 未配置 / 依赖缺失 / 连接失败一律返回 None。"""
    if not is_configured():
        return None
    try:
        from src.stt.volc import VolcSttStream  # websockets import 惰性在里面
        return await VolcSttStream.connect()
    except Exception:
        log.warning("STT 会话建立失败, 前端将退打字路径", exc_info=True)
        return None
