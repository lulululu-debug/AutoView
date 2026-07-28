"""Sprint 8.2 task 3 —— 确定性回放 + golden trace 回归护栏。

- 回放单元: REPLAY_MODE 命中返录制 / miss 抛 ReplayDivergence (不静默) /
  未开回放不受 store 影响
- diff 单元: 运行期噪声 (ts/span_id/path/latency) 不算分歧; 篡改一条录制
  响应能被定位; 长度不一致能报
- golden 回归 (PG+Redis gated): 三场 golden 各自回放重跑, 决策序列 diff
  为空 —— 改 prompt/policy 阈值会在这里红, 属预期信号 (确认变化合理后
  重录 scripts/record_golden_traces.py)

跑法: python -m unittest evals.test_trace_replay
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src import llm  # noqa: E402
from src.schemas import DecisionSpan, DecisionTrace, LLMCallRecord  # noqa: E402
from src.trace import replay  # noqa: E402
from src.trace.diff import decision_signature, diff_traces  # noqa: E402

_GOLDEN_DIR = Path(__file__).parent / "data" / "golden_traces"


def _load_golden(name: str) -> DecisionTrace:
    with (_GOLDEN_DIR / f"{name}.json").open(encoding="utf-8") as f:
        return DecisionTrace.model_validate(json.load(f))


class ReplayUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)
        self.addCleanup(lambda: os.environ.pop("REPLAY_MODE", None))
        self.addCleanup(replay.clear_store)

    def test_hit_returns_recorded(self) -> None:
        from src.cache import llm_cache
        key = llm_cache.make_key("sys", "usr", llm.DEFAULT_MODEL, 1024)
        replay.set_store({key: "录制的回答"})
        os.environ["REPLAY_MODE"] = "1"
        self.assertEqual(llm.complete("sys", "usr"), "录制的回答")

    def test_miss_raises_divergence(self) -> None:
        replay.set_store({"other-hash": "x"})
        os.environ["REPLAY_MODE"] = "1"
        with self.assertRaises(replay.ReplayDivergence):
            llm.complete("sys", "usr-not-recorded")

    def test_no_store_raises(self) -> None:
        os.environ["REPLAY_MODE"] = "1"
        with self.assertRaises(replay.ReplayDivergence):
            llm.complete("sys", "usr")

    def test_replay_off_ignores_store(self) -> None:
        replay.set_store({})
        out = llm.complete("sys", "usr")
        self.assertTrue(llm.is_stub(out))

    def test_store_from_trace_excludes_embedding(self) -> None:
        t = DecisionTrace(session_id="s", llm_calls=[
            LLMCallRecord(kind="chat", request_hash="h1", model="m",
                          response="r1", path="stub"),
            LLMCallRecord(kind="embedding", request_hash="h2", model="m",
                          response="vector:abc", path="stub"),
        ])
        store = replay.store_from_trace(t)
        self.assertEqual(store, {"h1": "r1"})


class DiffUnitTests(unittest.TestCase):
    @staticmethod
    def _trace(**kw) -> DecisionTrace:
        return DecisionTrace(
            session_id=kw.get("session_id", "s"),
            spans=[DecisionSpan(
                name="followup_decision", question_id=kw.get("qid", "q-1"),
                attributes={"decided": True, "sufficiency": 0.4},
                ts=kw.get("ts", 1.0),
            )],
            llm_calls=[LLMCallRecord(
                kind="chat", request_hash="h1", model="m",
                response=kw.get("response", "resp"),
                path=kw.get("path", "stub"),
                latency_ms=kw.get("latency", 0.0),
            )],
        )

    def test_noise_fields_not_divergence(self) -> None:
        a = self._trace(ts=1.0, path="stub", latency=0.0, qid="q-a", session_id="s1")
        b = self._trace(ts=99.0, path="replay", latency=5.5, qid="q-b", session_id="s2")
        self.assertEqual(diff_traces(a, b), [])

    def test_tampered_response_located(self) -> None:
        a = self._trace()
        b = self._trace(response="TAMPERED")
        out = diff_traces(a, b)
        self.assertEqual(len(out), 1)
        self.assertIn("llm", out[0])

    def test_length_mismatch_reported(self) -> None:
        a = self._trace()
        b = self._trace()
        b.spans.append(DecisionSpan(name="extra", attributes={}))
        self.assertTrue(any("长度不一致" in d for d in diff_traces(a, b)))

    @unittest.skipUnless(
        (_GOLDEN_DIR / "campus-early-finalize.json").exists(), "golden 未录制",
    )
    def test_tamper_real_golden_located(self) -> None:
        """doc eval (c): 篡改真实 golden 的一条录制响应, diff 定位到具体条目。"""
        golden = _load_golden("campus-early-finalize")
        tampered = golden.model_copy(deep=True)
        tampered.llm_calls[0].response = "TAMPERED"
        out = diff_traces(golden, tampered)
        self.assertEqual(len(out), 1)
        self.assertIn(tampered.llm_calls[0].request_hash[:8], out[0].replace("'", ""))


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 PG + Redis 跑 golden 回归",
)
class GoldenReplayTests(unittest.TestCase):
    """golden 重放 -> 决策序列逐项一致 (完成标准 'diff 为空')。"""

    @classmethod
    def setUpClass(cls) -> None:
        from evals.golden_scenarios import isolate_environment
        from src.db import init_db
        cls._saved_env = isolate_environment()
        init_db()

    @classmethod
    def tearDownClass(cls) -> None:
        from evals.golden_scenarios import restore_environment
        restore_environment(cls._saved_env)

    def tearDown(self) -> None:
        os.environ.pop("REPLAY_MODE", None)
        replay.clear_store()

    def _replay_scenario(self, name: str) -> None:
        from evals.golden_scenarios import SCENARIOS, run_scenario
        from src import db
        golden = _load_golden(name)
        sc = next(s for s in SCENARIOS if s["name"] == name)
        replay.set_store(replay.store_from_trace(golden))
        os.environ["REPLAY_MODE"] = "1"
        try:
            sid = run_scenario(sc)
        finally:
            os.environ.pop("REPLAY_MODE", None)
        new = db.load_decision_trace(sid)
        self.assertIsNotNone(new)
        divergences = diff_traces(golden, new)
        self.assertEqual(divergences, [], f"{name} 回放决策序列漂移")
        self.assertEqual(
            len(decision_signature(golden)), len(decision_signature(new)),
        )
        # 回放路径确实生效 (chat 全走 replay, 零真实调用)
        self.assertTrue(all(
            c.path == "replay" for c in new.llm_calls if c.kind == "chat"
        ))

    def test_campus_full(self) -> None:
        self._replay_scenario("campus-full")

    def test_lateral_deep(self) -> None:
        self._replay_scenario("lateral-deep")

    def test_campus_early_finalize(self) -> None:
        self._replay_scenario("campus-early-finalize")


if __name__ == "__main__":
    unittest.main()
