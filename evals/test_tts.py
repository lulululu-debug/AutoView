"""Sprint 6-2 eval —— TTS 单一调用点 + 音频缓存 + turn 音频端点护栏。

守住的契约:
- tts.synthesize **绝不 raise**: 无 TTS_PROVIDER / 未知 provider / 缺 key /
  provider 内部异常, 一律返回 None —— 媒体层挂了不能拖垮面试主链路
  (CLAUDE.md: 不能引入 "LLM 挂了整条链路就挂" 的依赖, TTS 同款)。
- tts_cache: 无 Redis 静默降级 (get None / set no-op);
  key 对 text/provider/voice 三者都敏感 (换音色/厂商不撞老缓存)。
- orchestrator.get_turn_audio: session 不存在 -> SessionNotFound,
  ref_id 不是面试官 turn -> TurnNotFound, TTS 未配置 -> None (API 层 204)。

跑法:
    python -m unittest evals.test_tts

本 eval 不 import pymilvus, 模块顶 pop OPENAI_API_KEY 有效; TTS 相关 env 在
setUp 里 pop (防其它模块的 load_dotenv 把 .env 塞回 os.environ 的老坑)。
需要 Redis 的 TestCase 在缺 REDIS_URL 时自动 skip。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src import tts  # noqa: E402
from src.cache import tts_cache  # noqa: E402
from src.cache.base import reset_client_for_testing  # noqa: E402

_TTS_ENV = (
    "TTS_PROVIDER",
    "VOLC_TTS_APPID",
    "VOLC_TTS_TOKEN",
    "VOLC_TTS_VOICE",
    "VOLC_TTS_CLUSTER",
    "AZURE_SPEECH_KEY",
    "AZURE_SPEECH_REGION",
    "AZURE_TTS_VOICE",
)


class _TtsEnvIsolatedCase(unittest.TestCase):
    """setUp 时 pop 所有 TTS env, tearDown 恢复 —— 保证测的是干净回退路径。"""

    def setUp(self) -> None:
        self._saved = {k: os.environ.pop(k, None) for k in _TTS_ENV}

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TtsStubFallbackTests(_TtsEnvIsolatedCase):
    """synthesize 的全部 None 回退分支; 任何一条抛异常都是 bug。"""

    def test_no_provider_returns_none(self) -> None:
        self.assertIsNone(tts.synthesize("请做一下自我介绍"))

    def test_unknown_provider_returns_none(self) -> None:
        os.environ["TTS_PROVIDER"] = "openai-realtime"
        self.assertIsNone(tts.synthesize("请做一下自我介绍"))

    def test_volc_without_keys_returns_none(self) -> None:
        os.environ["TTS_PROVIDER"] = "volc"
        self.assertIsNone(tts.synthesize("讲讲你最有挑战的项目"))

    def test_azure_without_keys_returns_none(self) -> None:
        os.environ["TTS_PROVIDER"] = "azure"
        self.assertIsNone(tts.synthesize("讲讲你最有挑战的项目"))

    def test_empty_text_returns_none(self) -> None:
        os.environ["TTS_PROVIDER"] = "volc"
        self.assertIsNone(tts.synthesize("   "))

    def test_provider_exception_swallowed(self) -> None:
        """provider 内部炸 (网络/超时/厂商 5xx) -> synthesize 静默 None。"""
        os.environ["TTS_PROVIDER"] = "volc"
        with mock.patch.object(
            tts, "_synthesize_volc", side_effect=RuntimeError("网络炸了"),
        ):
            # 文本带唯一后缀, 防 dev Redis 里的旧缓存条目干扰本测试
            self.assertIsNone(tts.synthesize("异常路径专用文本 eval-6-2-boom"))


class TtsCacheKeyTests(unittest.TestCase):
    """cache key 必须对所有影响输出的输入敏感 —— llm_cache 同款契约。"""

    def test_key_stable_for_same_input(self) -> None:
        a = tts_cache.make_key("你好", "volc", "BV700_streaming")
        b = tts_cache.make_key("你好", "volc", "BV700_streaming")
        self.assertEqual(a, b)

    def test_key_sensitive_to_each_field(self) -> None:
        base = tts_cache.make_key("你好", "volc", "BV700_streaming")
        self.assertNotEqual(base, tts_cache.make_key("你好吗", "volc", "BV700_streaming"))
        self.assertNotEqual(base, tts_cache.make_key("你好", "azure", "BV700_streaming"))
        self.assertNotEqual(base, tts_cache.make_key("你好", "volc", "BV701_streaming"))

    def test_key_prefix(self) -> None:
        self.assertTrue(tts_cache.make_key("x", "volc", "v").startswith("tts:"))


class TtsCacheNoRedisTests(unittest.TestCase):
    """无 Redis 时 get/set 静默降级, 不抛 —— 缓存层不能是故障点。"""

    def setUp(self) -> None:
        self._saved_url = os.environ.pop("REDIS_URL", None)
        reset_client_for_testing()

    def tearDown(self) -> None:
        if self._saved_url is not None:
            os.environ["REDIS_URL"] = self._saved_url
        reset_client_for_testing()

    def test_get_returns_none(self) -> None:
        self.assertIsNone(tts_cache.get(tts_cache.make_key("x", "volc", "v")))

    def test_set_is_noop(self) -> None:
        tts_cache.set(tts_cache.make_key("x", "volc", "v"), b"\x00audio")  # 不抛即过


@unittest.skipUnless(os.environ.get("REDIS_URL"), "需要 REDIS_URL 才能跑")
class TtsCacheRoundtripTests(unittest.TestCase):
    """base64 编解码往返: 二进制音频 (含非 UTF-8 字节) 原样取回。"""

    _KEY = tts_cache.make_key("eval-6-2-roundtrip", "volc", "BV700_streaming")

    def tearDown(self) -> None:
        try:
            from src.cache.base import get_redis
            get_redis().delete(self._KEY)
        except Exception:
            pass

    def test_binary_roundtrip(self) -> None:
        audio = bytes(range(256))  # 覆盖全部字节值, 证明不是按 UTF-8 存的
        tts_cache.set(self._KEY, audio)
        self.assertEqual(tts_cache.get(self._KEY), audio)


@unittest.skipUnless(os.environ.get("REDIS_URL"), "需要 REDIS_URL 才能跑")
class OrchestratorTurnAudioTests(_TtsEnvIsolatedCase):
    """get_turn_audio 的异常映射 + TTS 未配置时的 None 直通。"""

    def setUp(self) -> None:
        super().setUp()
        from src import cache
        from src.schemas import InterviewSession, Turn, TurnRole

        self.session = InterviewSession(plan_id="p-audio-eval", job_id="j")
        self.session.history.append(
            Turn(role=TurnRole.INTERVIEWER, text="请做一下自我介绍", ref_id="q1"),
        )
        # 候选人 turn 的 ref_id 不该被音频端点匹配到
        self.session.history.append(
            Turn(role=TurnRole.CANDIDATE, text="我是...", ref_id="a1"),
        )
        cache.save_session(self.session)

    def tearDown(self) -> None:
        from src import cache
        cache.delete_session(self.session.session_id)
        super().tearDown()

    def test_session_not_found(self) -> None:
        from src.orchestrator import SessionNotFound, get_turn_audio
        with self.assertRaises(SessionNotFound):
            get_turn_audio("不存在的-session", "q1")

    def test_turn_not_found(self) -> None:
        from src.orchestrator import TurnNotFound, get_turn_audio
        with self.assertRaises(TurnNotFound):
            get_turn_audio(self.session.session_id, "没有这个-ref")

    def test_candidate_ref_id_not_matched(self) -> None:
        """候选人回答的 ref_id 不是面试官 turn, 不能被播报。"""
        from src.orchestrator import TurnNotFound, get_turn_audio
        with self.assertRaises(TurnNotFound):
            get_turn_audio(self.session.session_id, "a1")

    def test_tts_unconfigured_returns_none(self) -> None:
        """TTS 未配置 -> None 直通 (API 层映射 204, 前端静默退文字)。"""
        from src.orchestrator import get_turn_audio
        self.assertIsNone(get_turn_audio(self.session.session_id, "q1"))


class FillerTextsTests(unittest.TestCase):
    """Sprint 6-3: 过渡语音是固定文案 (可复现约束), 不走 LLM 生成。"""

    def test_fixed_and_nonempty(self) -> None:
        from src.orchestrator import FILLER_TEXTS
        # 改数量必须同步 web/src/lib/api.ts 的 FILLER_COUNT
        self.assertEqual(len(FILLER_TEXTS), 3)
        for t in FILLER_TEXTS:
            self.assertTrue(t.strip())


@unittest.skipUnless(os.environ.get("REDIS_URL"), "需要 REDIS_URL 才能跑")
class FillerAudioTests(_TtsEnvIsolatedCase):
    """Sprint 6-3: get_filler_audio 的异常映射 + TTS 未配置直通。"""

    def setUp(self) -> None:
        super().setUp()
        from src import cache
        from src.schemas import InterviewSession

        self.session = InterviewSession(plan_id="p-filler-eval", job_id="j")
        cache.save_session(self.session)

    def tearDown(self) -> None:
        from src import cache
        cache.delete_session(self.session.session_id)
        super().tearDown()

    def test_session_not_found(self) -> None:
        """无会话 -> 404; 不给无会话方当免费 TTS 用。"""
        from src.orchestrator import SessionNotFound, get_filler_audio
        with self.assertRaises(SessionNotFound):
            get_filler_audio("不存在的-session", 0)

    def test_idx_out_of_range(self) -> None:
        from src.orchestrator import FILLER_TEXTS, TurnNotFound, get_filler_audio
        with self.assertRaises(TurnNotFound):
            get_filler_audio(self.session.session_id, -1)
        with self.assertRaises(TurnNotFound):
            get_filler_audio(self.session.session_id, len(FILLER_TEXTS))

    def test_tts_unconfigured_returns_none(self) -> None:
        from src.orchestrator import get_filler_audio
        self.assertIsNone(get_filler_audio(self.session.session_id, 0))


if __name__ == "__main__":
    unittest.main()
