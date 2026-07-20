"""Sprint 6-4 eval —— STT 单一调用点 + 火山协议帧 + WS 转写代理护栏。

守住的契约:
- stt.is_configured / create_stream: 无 STT_PROVIDER / 未知值 / azure (未实装) /
  缺 key 一律 False / None, 前端据此隐藏麦克风入口 —— 打字路径永远保底。
- volc 二进制协议的打包/解包是纯函数, 不打网络就能锁行为:
  帧头字节、last flag、gzip 往返、错误帧解析、累计全文提取。
- WS /interviews/{sid}/transcribe: session 不存在 -> error+close;
  STT 未配置 -> error+close。两条都不能把异常泄成 500。

跑法:
    python -m unittest evals.test_stt

需要 Redis 的 TestCase 缺 REDIS_URL 自动 skip; 不打任何厂商 API。
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src import stt  # noqa: E402
from src.stt import volc  # noqa: E402

_STT_ENV = (
    "STT_PROVIDER",
    "VOLC_STT_APPID",
    "VOLC_STT_TOKEN",
    "VOLC_STT_CLUSTER",
)


class _SttEnvIsolatedCase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.pop(k, None) for k in _STT_ENV}

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class SttConfigTests(_SttEnvIsolatedCase):
    """is_configured / create_stream 的回退矩阵。"""

    def test_no_provider(self) -> None:
        self.assertFalse(stt.is_configured())

    def test_unknown_provider(self) -> None:
        os.environ["STT_PROVIDER"] = "whisper-realtime"
        self.assertFalse(stt.is_configured())

    def test_azure_not_implemented(self) -> None:
        os.environ["STT_PROVIDER"] = "azure"
        self.assertFalse(stt.is_configured())

    def test_volc_without_keys(self) -> None:
        os.environ["STT_PROVIDER"] = "volc"
        self.assertFalse(stt.is_configured())

    def test_volc_with_keys(self) -> None:
        os.environ["STT_PROVIDER"] = "volc"
        os.environ["VOLC_STT_APPID"] = "a"
        os.environ["VOLC_STT_TOKEN"] = "t"
        self.assertTrue(stt.is_configured())

    def test_create_stream_none_when_unconfigured(self) -> None:
        self.assertIsNone(asyncio.run(stt.create_stream()))

    def test_create_stream_swallows_connect_failure(self) -> None:
        """配置齐但连不上厂商 (假 key/断网) -> None, 不许 raise。"""
        os.environ["STT_PROVIDER"] = "volc"
        os.environ["VOLC_STT_APPID"] = "a"
        os.environ["VOLC_STT_TOKEN"] = "t"
        from unittest import mock

        async def _boom() -> None:
            raise RuntimeError("连不上")

        with mock.patch.object(volc.VolcSttStream, "connect", _boom):
            self.assertIsNone(asyncio.run(stt.create_stream()))


class VolcFramingTests(unittest.TestCase):
    """火山二进制协议纯函数: 帧头 / flag / gzip 往返。"""

    def test_full_request_header(self) -> None:
        frame = volc.build_full_request({"a": 1})
        self.assertEqual(frame[:4], bytes([0x11, 0x10, 0x11, 0x00]))
        size = int.from_bytes(frame[4:8], "big")
        body = json.loads(gzip.decompress(frame[8:8 + size]))
        self.assertEqual(body, {"a": 1})

    def test_audio_request_last_flag(self) -> None:
        normal = volc.build_audio_request(b"\x01\x02", last=False)
        last = volc.build_audio_request(b"", last=True)
        self.assertEqual(normal[1], 0x20)
        self.assertEqual(last[1], 0x22)
        # 音频体 gzip 往返
        size = int.from_bytes(normal[4:8], "big")
        self.assertEqual(gzip.decompress(normal[8:8 + size]), b"\x01\x02")

    def _server_frame(self, payload: dict) -> bytes:
        raw = gzip.compress(json.dumps(payload, ensure_ascii=False).encode())
        header = bytes([0x11, 0x90, 0x11, 0x00])  # server full response
        return header + len(raw).to_bytes(4, "big") + raw

    def test_parse_server_response(self) -> None:
        data = volc.parse_server_frame(
            self._server_frame(
                {"code": 1000, "sequence": 2, "result": [{"text": "你好世界"}]},
            ),
        )
        assert data is not None
        self.assertEqual(data["code"], 1000)
        self.assertEqual(volc.extract_text(data), "你好世界")

    def test_parse_server_error_frame(self) -> None:
        body = gzip.compress(json.dumps({"message": "quota 超限"}).encode())
        header = bytes([0x11, 0xF0, 0x11, 0x00])  # server error
        frame = (
            header
            + (1013).to_bytes(4, "big")
            + len(body).to_bytes(4, "big")
            + body
        )
        data = volc.parse_server_frame(frame)
        assert data is not None
        self.assertEqual(data["__error_code"], 1013)
        self.assertEqual(data["message"], "quota 超限")

    def test_parse_garbage_returns_none(self) -> None:
        self.assertIsNone(volc.parse_server_frame(b"\x00\x00"))
        # 未知 message_type (client 型) 也应忽略而不是炸
        self.assertIsNone(
            volc.parse_server_frame(bytes([0x11, 0x10, 0x11, 0x00]) + b"\x00" * 8),
        )

    def test_extract_text_defensive(self) -> None:
        self.assertEqual(volc.extract_text({}), "")
        self.assertEqual(volc.extract_text({"result": "不是列表"}), "")
        self.assertEqual(volc.extract_text({"result": []}), "")


@unittest.skipUnless(os.environ.get("REDIS_URL"), "需要 REDIS_URL 才能跑")
class TranscribeWsTests(_SttEnvIsolatedCase):
    """WS 代理端点的两条拒绝路径 (session 不存在 / STT 未配置)。"""

    def setUp(self) -> None:
        super().setUp()
        from fastapi.testclient import TestClient

        from api.main import create_app
        from src import cache
        from src.schemas import InterviewSession

        self.client = TestClient(create_app())
        self.session = InterviewSession(plan_id="p-stt-eval", job_id="j")
        cache.save_session(self.session)

    def tearDown(self) -> None:
        from src import cache
        cache.delete_session(self.session.session_id)
        super().tearDown()

    def test_session_not_found(self) -> None:
        with self.client.websocket_connect(
            "/interviews/不存在的-session/transcribe",
        ) as ws:
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "error")
            self.assertIn("不存在", msg["message"])

    def test_stt_unconfigured(self) -> None:
        with self.client.websocket_connect(
            f"/interviews/{self.session.session_id}/transcribe",
        ) as ws:
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "error")
            self.assertIn("未配置", msg["message"])


if __name__ == "__main__":
    unittest.main()
