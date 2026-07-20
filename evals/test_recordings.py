"""Sprint 6-5 eval —— 录制归档存储 + 上传端点 + 留存清理护栏。

守住的契约:
- media_store 惰性配置: 未配置时 is_configured=False / append 抛
  MediaStoreNotConfigured / media_ref=None / purge 静默空 —— 录制是增强不是依赖。
- session_id 路径安全: 非 [A-Za-z0-9_-] 一律 InvalidSessionId (防目录穿越)。
- 分片按序 append = 字节级拼接 (顺序由前端串行链保证, 这里锁"追加不改写")。
- 留存清理: 只删过期 (mtime), 不动新文件。
- POST /interviews/{sid}/recordings: 404 无会话 / 409 未配置 / 204 落盘。
- InterviewSession.media_ref: 默认 None, 老 JSON 缺字段兼容。

跑法:
    python -m unittest evals.test_recordings
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src import media_store  # noqa: E402
from src.schemas import InterviewSession  # noqa: E402


class _MediaDirCase(unittest.TestCase):
    """setUp 建独立临时目录当 MEDIA_STORAGE_DIR, tearDown 还原。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("MEDIA_STORAGE_DIR")
        os.environ["MEDIA_STORAGE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("MEDIA_STORAGE_DIR", None)
        else:
            os.environ["MEDIA_STORAGE_DIR"] = self._saved
        self._tmp.cleanup()


class MediaStoreUnconfiguredTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop("MEDIA_STORAGE_DIR", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["MEDIA_STORAGE_DIR"] = self._saved

    def test_is_configured_false(self) -> None:
        self.assertFalse(media_store.is_configured())

    def test_append_raises(self) -> None:
        with self.assertRaises(media_store.MediaStoreNotConfigured):
            media_store.append_chunk("s1", b"x")

    def test_media_ref_none(self) -> None:
        self.assertIsNone(media_store.media_ref("s1"))

    def test_purge_silent_empty(self) -> None:
        self.assertEqual(media_store.purge_older_than(1), [])


class MediaStoreTests(_MediaDirCase):
    def test_append_concat_roundtrip(self) -> None:
        media_store.append_chunk("sess-a", b"\x1aE\xdf\xa3head")
        media_store.append_chunk("sess-a", b"|chunk2")
        media_store.append_chunk("sess-a", b"")  # 空片 no-op
        path = media_store.media_ref("sess-a")
        assert path is not None
        self.assertEqual(Path(path).read_bytes(), b"\x1aE\xdf\xa3head|chunk2")

    def test_media_ref_none_when_no_file(self) -> None:
        self.assertIsNone(media_store.media_ref("never-recorded"))

    def test_invalid_session_id_rejected(self) -> None:
        for bad in ("../evil", "a/b", "a\\b", "", "a b", "斜杠/"):
            with self.assertRaises(media_store.InvalidSessionId, msg=bad):
                media_store.append_chunk(bad, b"x")

    def test_purge_only_removes_stale(self) -> None:
        media_store.append_chunk("old-sess", b"old")
        media_store.append_chunk("new-sess", b"new")
        old_path = Path(media_store.media_ref("old-sess"))  # type: ignore[arg-type]
        stale_mtime = time.time() - 91 * 86400
        os.utime(old_path, (stale_mtime, stale_mtime))

        removed = media_store.purge_older_than(90)

        self.assertEqual(removed, ["old-sess.webm"])
        self.assertIsNone(media_store.media_ref("old-sess"))
        self.assertIsNotNone(media_store.media_ref("new-sess"))


class SessionMediaRefSchemaTests(unittest.TestCase):
    def test_default_none(self) -> None:
        s = InterviewSession(plan_id="p", job_id="j")
        self.assertIsNone(s.media_ref)

    def test_old_json_without_field(self) -> None:
        """老 Redis/PG 里的 session JSON 缺 media_ref 字段必须能反序列化。"""
        s = InterviewSession.model_validate(
            {"session_id": "s", "plan_id": "p", "job_id": "j"},
        )
        self.assertIsNone(s.media_ref)


@unittest.skipUnless(os.environ.get("REDIS_URL"), "需要 REDIS_URL 才能跑")
class RecordingUploadApiTests(_MediaDirCase):
    """POST /interviews/{sid}/recordings 三态。"""

    def setUp(self) -> None:
        super().setUp()
        from fastapi.testclient import TestClient

        from api.main import create_app
        from src import cache

        self.client = TestClient(create_app())
        self.session = InterviewSession(plan_id="p-rec-eval", job_id="j")
        cache.save_session(self.session)

    def tearDown(self) -> None:
        from src import cache
        cache.delete_session(self.session.session_id)
        super().tearDown()

    def test_upload_appends_in_order(self) -> None:
        sid = self.session.session_id
        r1 = self.client.post(f"/interviews/{sid}/recordings", content=b"AAA")
        r2 = self.client.post(f"/interviews/{sid}/recordings", content=b"BBB")
        self.assertEqual((r1.status_code, r2.status_code), (204, 204))
        path = media_store.media_ref(sid)
        assert path is not None
        self.assertEqual(Path(path).read_bytes(), b"AAABBB")

    def test_unknown_session_404(self) -> None:
        r = self.client.post("/interviews/ghost-sess/recordings", content=b"x")
        self.assertEqual(r.status_code, 404)

    def test_unconfigured_409(self) -> None:
        saved = os.environ.pop("MEDIA_STORAGE_DIR")
        try:
            r = self.client.post(
                f"/interviews/{self.session.session_id}/recordings", content=b"x",
            )
            self.assertEqual(r.status_code, 409)
        finally:
            os.environ["MEDIA_STORAGE_DIR"] = saved


if __name__ == "__main__":
    unittest.main()
