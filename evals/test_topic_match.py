"""topic_match LLM 兜底归类的结构性护栏 —— Sprint E。

覆盖:
- stub 模式 (无 OPENAI_API_KEY) llm_match_skills 必须返 {} —— 维持纯 embedding
  结果, 不给 plan 注入 stub 噪声
- _parse_llm_skill_matches 的校验: LLM 自造的主题/技能被丢弃、空列表跳过、
  容忍 ```json 包装

不测真实 LLM 归类质量 (那是在线行为, 无 key 环境无法回归)。
"""
from __future__ import annotations

import os
import unittest

# evals 强制走 LLM stub 路径
os.environ.pop("OPENAI_API_KEY", None)


class LlmMatchStubTests(unittest.TestCase):
    def setUp(self):
        # pymilvus import 副作用会把 .env 塞回 os.environ, setUp 里再 pop 才稳
        os.environ.pop("OPENAI_API_KEY", None)

    def test_stub_returns_empty(self):
        from src.agents.planner import topic_match
        out = topic_match.llm_match_skills(
            ["LangChain", "RAG"], topics=["AI Agent", "MCP 协议"],
        )
        self.assertEqual(out, {})

    def test_empty_skills_or_topics_return_empty(self):
        from src.agents.planner import topic_match
        self.assertEqual(topic_match.llm_match_skills([], topics=["X"]), {})
        self.assertEqual(
            topic_match.llm_match_skills(["LangChain"], topics=[]), {},
        )


class ParseLlmSkillMatchesTests(unittest.TestCase):
    def _parse(self, raw, topics=("AI Agent", "MCP 协议"), skills=("LangChain", "RAG")):
        from src.agents.planner.topic_match import _parse_llm_skill_matches
        return _parse_llm_skill_matches(
            raw, valid_topics=list(topics), skills=list(skills),
        )

    def test_valid_output(self):
        out = self._parse('{"LangChain": ["AI Agent"], "RAG": ["AI Agent", "MCP 协议"]}')
        self.assertEqual(out, {
            "LangChain": ["AI Agent"],
            "RAG": ["AI Agent", "MCP 协议"],
        })

    def test_invented_topic_dropped(self):
        # LLM 自造主题 "Python 基础" 必须被丢; 剩下合法的保留
        out = self._parse('{"LangChain": ["Python 基础", "AI Agent"]}')
        self.assertEqual(out, {"LangChain": ["AI Agent"]})

    def test_invented_skill_dropped(self):
        out = self._parse('{"Kubernetes": ["AI Agent"]}')
        self.assertEqual(out, {})

    def test_empty_list_skill_omitted(self):
        out = self._parse('{"LangChain": [], "RAG": ["AI Agent"]}')
        self.assertEqual(out, {"RAG": ["AI Agent"]})

    def test_tolerates_markdown_fence(self):
        out = self._parse('```json\n{"RAG": ["AI Agent"]}\n```')
        self.assertEqual(out, {"RAG": ["AI Agent"]})

    def test_tolerates_reasoning_preamble(self):
        # prompt 让模型先逐技能思考再输出 JSON, 解析必须容忍前置推理文字
        out = self._parse(
            "RAG 是检索增强生成, 服务于 Agent/大模型领域。\n"
            "LangChain 是 Agent 开发框架。\n"
            '{"RAG": ["AI Agent"], "LangChain": ["AI Agent"]}'
        )
        self.assertEqual(
            out, {"RAG": ["AI Agent"], "LangChain": ["AI Agent"]},
        )

    def test_garbage_returns_empty(self):
        self.assertEqual(self._parse("不是 JSON"), {})
        self.assertEqual(self._parse('["not", "a", "dict"]'), {})


if __name__ == "__main__":
    unittest.main()
