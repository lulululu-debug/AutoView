"""火山引擎流式语音识别客户端 —— Sprint 6-4。

自研实现火山 ASR v2 的二进制 WS 协议 (官方无异步 Python SDK 时代的标准做法)。
协议要点 (来源: openspeech.bytedance.com 流式识别文档, 改动前先对着最新文档核对):

帧 = 4 字节头 + payload:
  byte0: (protocol_version=0b0001 << 4) | (header_size=0b0001, 单位 4 字节) = 0x11
  byte1: (message_type << 4) | flags
         client full request  = 0b0001, flags=0
         client audio-only    = 0b0010, flags=0b0010 表示最后一片
         server full response = 0b1001
         server error         = 0b1111
  byte2: (serialization << 4) | compression
         JSON=0b0001 / raw=0b0000; gzip=0b0001
  byte3: reserved
payload = 4 字节大端长度 + gzip 压缩体 (error 帧多一个 4 字节错误码前缀)。

识别结果: result_type=full 时每次响应给**累计全文**, 天然适配前端
"整体替换预览"的契约 (SttEvent 注释)。服务端在最后一片处理完后, 响应帧
sequence 为负 (payload JSON 里), 即定稿信号。

降级: 本文件任何异常都被 src/stt.create_stream / API WS 代理捕获,
前端退打字路径 —— 协议实现错了不会伤害面试主链路, 只会没有语音输入。
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import uuid

from src.stt import SttEvent, SttStream

log = logging.getLogger(__name__)

_WS_URL = "wss://openspeech.bytedance.com/api/v2/asr"
_CONNECT_TIMEOUT_SECONDS = 10.0
_RECEIVE_TIMEOUT_SECONDS = 15.0


# ---- 二进制帧打包 / 解包 (纯函数, eval 直接测) ----

def build_full_request(payload: dict) -> bytes:
    """client full request: JSON + gzip。"""
    raw = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    header = bytes([0x11, 0x10, 0x11, 0x00])
    return header + len(raw).to_bytes(4, "big") + raw


def build_audio_request(chunk: bytes, last: bool) -> bytes:
    """client audio-only request: raw + gzip; last=True 置结束 flag。"""
    raw = gzip.compress(chunk)
    header = bytes([0x11, 0x22 if last else 0x20, 0x01, 0x00])
    return header + len(raw).to_bytes(4, "big") + raw


def parse_server_frame(frame: bytes) -> dict | None:
    """解服务端帧成 JSON dict; 错误帧返回 {"__error_code": ..., ...body}。
    无法识别的帧返回 None (调用方忽略)。"""
    if len(frame) < 8:
        return None
    header_size = (frame[0] & 0x0F) * 4
    message_type = frame[1] >> 4
    compression = frame[2] & 0x0F
    payload = frame[header_size:]

    if message_type == 0b1001:  # full server response
        size = int.from_bytes(payload[:4], "big")
        body = payload[4:4 + size]
        error_code = None
    elif message_type == 0b1111:  # server error
        error_code = int.from_bytes(payload[:4], "big")
        size = int.from_bytes(payload[4:8], "big")
        body = payload[8:8 + size]
    else:
        return None

    if compression == 0b0001:
        body = gzip.decompress(body)
    data = json.loads(body.decode("utf-8"))
    if error_code is not None:
        data["__error_code"] = error_code
    return data


def extract_text(data: dict) -> str:
    """从响应 JSON 拿累计全文; 结构不符返回空串。"""
    result = data.get("result")
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return str(result[0].get("text", ""))
    return ""


class VolcSttStream(SttStream):
    """一次火山流式识别会话。用 create_stream() 建, 不要直接实例化。"""

    def __init__(self, ws) -> None:  # ws: websockets client connection
        self._ws = ws
        self._finished = False
        self._closed = False

    @classmethod
    async def connect(cls) -> "VolcSttStream":
        import asyncio

        import websockets  # 惰性 import, 同 src/llm 的 openai SDK 处理

        appid = os.environ["VOLC_STT_APPID"]
        token = os.environ["VOLC_STT_TOKEN"]
        cluster = os.environ.get("VOLC_STT_CLUSTER", "volcengine_streaming_common")

        ws = await asyncio.wait_for(
            websockets.connect(
                _WS_URL,
                additional_headers={"Authorization": f"Bearer; {token}"},
                max_size=10 * 1024 * 1024,
            ),
            timeout=_CONNECT_TIMEOUT_SECONDS,
        )
        request = {
            "app": {"appid": appid, "cluster": cluster, "token": token},
            "user": {"uid": "ai-interview"},
            "request": {
                "reqid": str(uuid.uuid4()),
                "nbest": 1,
                "result_type": "full",
                "show_utterances": False,
                "sequence": 1,
                "workflow": (
                    "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuate"
                ),
            },
            "audio": {
                "format": "raw",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
        }
        await ws.send(build_full_request(request))
        return cls(ws)

    async def send_audio(self, chunk: bytes) -> None:
        if self._finished or self._closed:
            return
        await self._ws.send(build_audio_request(chunk, last=False))

    async def finish(self) -> None:
        if self._finished or self._closed:
            return
        self._finished = True
        # 空音频 + last flag 收尾, 触发服务端定稿
        await self._ws.send(build_audio_request(b"", last=True))

    async def receive(self) -> SttEvent | None:
        import asyncio

        if self._closed:
            return None
        try:
            frame = await asyncio.wait_for(
                self._ws.recv(), timeout=_RECEIVE_TIMEOUT_SECONDS,
            )
        except Exception:
            # 连接关闭 / 超时: 已定稿则视为正常结束, 否则报错给前端
            if self._finished:
                return SttEvent(kind="done")
            return SttEvent(kind="error", message="转写服务连接中断")

        if isinstance(frame, str):
            return SttEvent(kind="partial", text="")  # 火山不发文本帧, 防御性忽略

        data = parse_server_frame(frame)
        if data is None:
            return SttEvent(kind="partial", text="")

        if "__error_code" in data or data.get("code") not in (1000, None):
            log.warning("火山 ASR 错误响应: %s", data)
            return SttEvent(
                kind="error",
                message=str(data.get("message", "转写服务返回错误")),
            )

        text = extract_text(data)
        # sequence < 0 = 服务端对最后一片的定稿响应
        if int(data.get("sequence", 1)) < 0:
            return SttEvent(kind="final", text=text)
        return SttEvent(kind="partial", text=text)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close()
        except Exception:
            pass
