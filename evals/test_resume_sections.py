"""Resume 语义分段 eval —— Sprint F。

锁四件事:
1. 启发式路径: 节标题 + 日期条目切分正确, 类型/标题正确, 内容零丢失
2. LLM 锚点路径: 锚点定位 → 原文连续切分, 内容零丢失, 前导补 personal_info
3. 降级链: 锚点定位失败 / LLM stub / 非法 JSON → heuristic → whole_text
4. 保真: 所有段的 text 都是原文子串 (LLM 无法改写简历内容)

LLM 全部 monkeypatch, 不烧 token, 不依赖环境有无 OPENAI_API_KEY。
"""
from __future__ import annotations

import json
import re
import unittest

from src.ingestion import resume_sections
from src.schemas import RESUME_DEEPDIVE_TYPES


_RESUME = """郑某
2002.08 | 138-0000-0000 | test@example.com

教育经历
某大学 计算机工程硕士 2025.09~2027.06
GPA 3.9

项目经历
智能法律咨询系统
2024.03-2024.08
基于 LangChain 和 OpenAI 实现 ReAct 多工具集成。
负责后端 API 与检索模块。

教务管理系统
2023.09-2024.01
设计 Redis 缓存队列 + 乐观锁的防超卖方案。
支撑选课高峰 5000 QPS。

实习经历
某科技公司 后端开发实习生 2024.06-2024.09
参与订单服务重构, 引入幂等键。

专业技能
Python / Go / Redis / Milvus
"""


def _normalized(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _coverage_equal(sections, original: str) -> bool:
    """段拼回 (忽略空白) == 原文 (忽略空白) —— 零丢字断言。"""
    return _normalized("".join(sec.text for sec in sections)) == _normalized(original)


class _PatchLLMBase(unittest.TestCase):
    """monkeypatch src.llm.complete; resume_sections 通过模块属性调用, 直接换。"""

    def _patch_llm(self, fn):
        import src.llm as llm_mod
        self._orig = llm_mod.complete
        llm_mod.complete = fn
        self.addCleanup(self._restore)

    def _restore(self):
        import src.llm as llm_mod
        llm_mod.complete = self._orig


class HeuristicSegmentTests(_PatchLLMBase):
    def setUp(self):
        # LLM 恒 stub → 走启发式
        self._patch_llm(lambda system, user, **kw: "[stub] x")

    def test_sections_types_and_titles(self):
        secs = resume_sections.segment_resume(_RESUME)
        types = [s.type for s in secs]
        self.assertEqual(types[0], "personal_info")
        self.assertIn("education", types)
        self.assertEqual(types.count("project"), 2, f"应切出两个项目: {types}")
        self.assertEqual(types.count("internship"), 1)
        self.assertIn("skills", types)
        titles = [s.title for s in secs if s.type == "project"]
        self.assertTrue(any("法律" in t for t in titles), titles)
        self.assertTrue(any("教务" in t for t in titles), titles)
        for s in secs:
            self.assertEqual(s.source, "heuristic")

    def test_zero_loss_and_substring(self):
        secs = resume_sections.segment_resume(_RESUME)
        self.assertTrue(_coverage_equal(secs, _RESUME), "分段不能丢内容")
        for s in secs:
            # 保真: 每段都是原文出现过的内容 (逐行子串校验, 段是按行切的)
            for line in s.text.split("\n"):
                self.assertIn(line, _RESUME)

    def test_deepdive_sections_available(self):
        secs = resume_sections.segment_resume(_RESUME)
        deep = [s for s in secs if s.type in RESUME_DEEPDIVE_TYPES]
        self.assertEqual(len(deep), 3, "2 项目 + 1 实习 = 3 个深挖段")


class LLMAnchorSegmentTests(_PatchLLMBase):
    def test_anchor_slicing_and_preamble(self):
        payload = json.dumps({"sections": [
            {"type": "education", "title": "教育经历", "first_line": "教育经历"},
            {"type": "project", "title": "智能法律咨询系统",
             "first_line": "智能法律咨询系统"},
            {"type": "project", "title": "教务管理系统",
             "first_line": "教务管理系统"},
            {"type": "internship", "title": "某科技公司",
             "first_line": "某科技公司 后端开发实习生 2024.06-2024.09"},
            {"type": "skills", "title": "专业技能", "first_line": "专业技能"},
        ]}, ensure_ascii=False)
        self._patch_llm(lambda system, user, **kw: payload)

        secs = resume_sections.segment_resume(_RESUME)
        self.assertTrue(all(s.source == "llm_anchor" for s in secs))
        # LLM 没标开头 → 前导文本自动补 personal_info
        self.assertEqual(secs[0].type, "personal_info")
        self.assertIn("郑某", secs[0].text)
        self.assertTrue(_coverage_equal(secs, _RESUME), "锚点切分不能丢内容")
        projects = [s for s in secs if s.type == "project"]
        self.assertEqual(len(projects), 2)
        self.assertIn("LangChain", projects[0].text)
        self.assertNotIn("教务管理", projects[0].text, "项目一不该混入项目二内容")
        # "项目经历" 这个节标题行落在教育段与项目一之间, 归入前一段, 不丢
        self.assertTrue(any("项目经历" in s.text for s in secs))

    def test_bogus_anchor_falls_back_to_heuristic(self):
        payload = json.dumps({"sections": [
            {"type": "project", "title": "x", "first_line": "这行不存在于简历里"},
            {"type": "skills", "title": "y", "first_line": "这行也不存在"},
        ]}, ensure_ascii=False)
        self._patch_llm(lambda system, user, **kw: payload)
        secs = resume_sections.segment_resume(_RESUME)
        self.assertTrue(all(s.source == "heuristic" for s in secs),
                        "锚点全定位失败应降级启发式")

    def test_malformed_json_falls_back(self):
        self._patch_llm(lambda system, user, **kw: "好的, 我来分段: [不是json")
        secs = resume_sections.segment_resume(_RESUME)
        self.assertTrue(all(s.source == "heuristic" for s in secs))

    def test_adjacent_same_type_merged_but_deepdive_kept(self):
        """"教育经历"标题行与条目被 LLM 锚成两段 → 合并;
        相邻两个 project 段绝不合并 (一段一题契约)。"""
        payload = json.dumps({"sections": [
            {"type": "education", "title": "教育经历", "first_line": "教育经历"},
            {"type": "education", "title": "某大学",
             "first_line": "某大学 计算机工程硕士 2025.09~2027.06"},
            {"type": "project", "title": "智能法律咨询系统",
             "first_line": "智能法律咨询系统"},
            {"type": "project", "title": "教务管理系统",
             "first_line": "教务管理系统"},
            {"type": "skills", "title": "专业技能", "first_line": "专业技能"},
        ]}, ensure_ascii=False)
        self._patch_llm(lambda system, user, **kw: payload)
        secs = resume_sections.segment_resume(_RESUME)
        types = [s.type for s in secs]
        self.assertEqual(types.count("education"), 1, f"相邻同类应合并: {types}")
        self.assertEqual(types.count("project"), 2, "deep-dive 段禁止合并")
        self.assertTrue(_coverage_equal(secs, _RESUME), "合并不能丢内容")

    def test_invalid_type_coerced_to_other(self):
        payload = json.dumps({"sections": [
            {"type": "banana", "title": "教育", "first_line": "教育经历"},
            {"type": "skills", "title": "技能", "first_line": "专业技能"},
        ]}, ensure_ascii=False)
        self._patch_llm(lambda system, user, **kw: payload)
        secs = resume_sections.segment_resume(_RESUME)
        self.assertTrue(all(
            s.type in ("personal_info", "other", "skills") for s in secs
        ), [s.type for s in secs])


class NormalizeConfirmedTests(unittest.TestCase):
    """Phase 2: 候选人确认分段的规范化 —— API 边界安全护栏。"""

    def test_source_forced_and_type_coerced(self):
        out = resume_sections.normalize_confirmed_sections([
            {"type": "project", "title": "项目A", "text": "内容A",
             "source": "llm_anchor"},           # 客户端伪造 source 无效
            {"type": "banana", "title": "?", "text": "内容B"},
        ])
        self.assertEqual([s.source for s in out],
                         ["user_confirmed", "user_confirmed"])
        self.assertEqual(out[1].type, "other")

    def test_empty_text_dropped(self):
        out = resume_sections.normalize_confirmed_sections([
            {"type": "project", "title": "空段", "text": "   "},
            {"type": "project", "title": "有货", "text": "真内容"},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].title, "有货")

    def test_caps_enforced(self):
        many = [
            {"type": "other", "title": f"t{i}", "text": "x" * 30_000}
            for i in range(80)
        ]
        out = resume_sections.normalize_confirmed_sections(many)
        self.assertEqual(len(out), resume_sections._MAX_CONFIRMED_SECTIONS)
        self.assertTrue(all(
            len(s.text) <= resume_sections._MAX_SECTION_CHARS for s in out
        ))

    def test_missing_title_gets_default(self):
        out = resume_sections.normalize_confirmed_sections([
            {"type": "internship", "title": "", "text": "实习内容"},
        ])
        self.assertTrue(out[0].title, "空标题应补默认")


class LastResortTests(_PatchLLMBase):
    def setUp(self):
        self._patch_llm(lambda system, user, **kw: "[stub] x")

    def test_no_headers_whole_text(self):
        text = "这是一段没有任何简历结构的文字。" * 20
        secs = resume_sections.segment_resume(text)
        self.assertEqual(len(secs), 1)
        self.assertEqual(secs[0].source, "whole_text")
        self.assertEqual(_normalized(secs[0].text), _normalized(text))

    def test_too_short_whole_text(self):
        secs = resume_sections.segment_resume("张三 后端")
        self.assertEqual(len(secs), 1)
        self.assertEqual(secs[0].source, "whole_text")


if __name__ == "__main__":
    unittest.main()
