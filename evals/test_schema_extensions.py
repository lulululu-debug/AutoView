"""Sprint 5.5 数据契约扩展的结构性护栏。

护栏对象:
- JobContext.track 默认 lateral, 非法值拒绝, 老 JSON 兼容
- QuestionCategory 4 个值齐全
- InterviewStage 4 个值齐全
- InterviewRound.stage 默认 KNOWLEDGE, 老 JSON 兼容
- InterviewSession.intro_text 默认 "", 老 JSON 兼容
- Question 能用 SELF_INTRO / SCENARIO 类别

为什么单独成文件:
- test_skeleton 是骨架/合规护栏, 已经 5 个 TestCase + 一个端到端;
  Sprint 5.5 的新契约和骨架职责不同, 单独成文件后续 5.6/5.7 加 schema 时
  顺手往这里扩, 不污染原骨架 eval。
- 全部走 stdlib unittest + pydantic 验证, 无 PG/Redis/LLM 依赖, 永远会跑。
"""
from __future__ import annotations

import os
import unittest

# 与 test_skeleton 一致: 强制 stub, 防止误打 API。
os.environ.pop("OPENAI_API_KEY", None)

from pydantic import ValidationError  # noqa: E402

from src.schemas import (  # noqa: E402
    InterviewRound,
    InterviewSession,
    InterviewStage,
    JobContext,
    Question,
    QuestionCategory,
    Track,
    Competency,
)


class JobContextTrackTests(unittest.TestCase):
    def test_default_track_is_lateral(self):
        j = JobContext(title="x", jd="x")
        self.assertIs(j.track, Track.LATERAL)

    def test_explicit_campus(self):
        j = JobContext(title="x", jd="x", track="campus")
        self.assertIs(j.track, Track.CAMPUS)

    def test_explicit_lateral(self):
        j = JobContext(title="x", jd="x", track="lateral")
        self.assertIs(j.track, Track.LATERAL)

    def test_rejects_illegal_track(self):
        with self.assertRaises(ValidationError):
            JobContext(title="x", jd="x", track="senior")

    def test_old_json_without_track_parses(self):
        # 老 PG/Redis 里的 JobContext 序列化不带 track 字段, 必须兼容。
        old = {"job_id": "j1", "title": "x", "jd": "x"}
        j = JobContext(**old)
        self.assertIs(j.track, Track.LATERAL)

    def test_track_round_trip_via_model_dump(self):
        j = JobContext(title="x", jd="x", track=Track.CAMPUS)
        dumped = j.model_dump(mode="json")
        self.assertEqual(dumped["track"], "campus")
        j2 = JobContext(**dumped)
        self.assertIs(j2.track, Track.CAMPUS)


class QuestionCategoryTests(unittest.TestCase):
    def test_four_categories_exist(self):
        names = {c.name for c in QuestionCategory}
        self.assertEqual(
            names,
            {"KNOWLEDGE", "PROJECT_EXPERIENCE", "SELF_INTRO", "SCENARIO"},
        )

    def test_self_intro_usable_on_question(self):
        q = Question(
            competency_id="c1", text="自我介绍", category=QuestionCategory.SELF_INTRO,
        )
        self.assertIs(q.category, QuestionCategory.SELF_INTRO)

    def test_scenario_usable_on_question(self):
        q = Question(
            competency_id="c1", text="线上 P99 涨 10 倍怎么处理",
            category=QuestionCategory.SCENARIO,
        )
        self.assertIs(q.category, QuestionCategory.SCENARIO)


class InterviewStageTests(unittest.TestCase):
    def test_four_stages_exist(self):
        names = {s.name for s in InterviewStage}
        self.assertEqual(
            names, {"SELF_INTRO", "KNOWLEDGE", "PROJECT", "SCENARIO"},
        )


class InterviewRoundStageTests(unittest.TestCase):
    def _round(self, **kwargs):
        c = Competency(name="x", description="x")
        q = Question(competency_id=c.competency_id, text="x")
        return InterviewRound(
            index=0, title="t", competencies=[c], questions=[q], **kwargs,
        )

    def test_default_stage_is_knowledge(self):
        r = self._round()
        self.assertIs(r.stage, InterviewStage.KNOWLEDGE)

    def test_explicit_stage(self):
        r = self._round(stage=InterviewStage.PROJECT)
        self.assertIs(r.stage, InterviewStage.PROJECT)

    def test_old_round_json_without_stage_parses(self):
        # 老 InterviewPlan JSON 落 PG 时不带 stage, 必须兼容。
        old = {
            "round_id": "r1",
            "index": 0,
            "title": "t",
            "competencies": [{"name": "x", "description": "x"}],
            "questions": [{"competency_id": "c1", "text": "x"}],
        }
        r = InterviewRound(**old)
        self.assertIs(r.stage, InterviewStage.KNOWLEDGE)


class InterviewSessionIntroTextTests(unittest.TestCase):
    def test_default_intro_text_is_empty(self):
        s = InterviewSession(plan_id="p1", job_id="j1")
        self.assertEqual(s.intro_text, "")

    def test_explicit_intro_text(self):
        s = InterviewSession(plan_id="p1", job_id="j1", intro_text="我叫小明")
        self.assertEqual(s.intro_text, "我叫小明")

    def test_old_session_json_without_intro_text_parses(self):
        # Sprint 1+ 落 Redis/PG 的老 session JSON 没有 intro_text, 必须兼容。
        old = {"session_id": "s1", "plan_id": "p1", "job_id": "j1"}
        s = InterviewSession(**old)
        self.assertEqual(s.intro_text, "")


if __name__ == "__main__":
    unittest.main()
