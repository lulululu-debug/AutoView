"""Sprint 6.7 task 2 —— 飞书 blocks->md 转换 + 清洗金标。纯函数零 infra。

金标样本全部来自 2026-07-18 手工整理飞书文档时踩过的真实坑
(memory: corpus-md-conventions): 裸 uuid 图片行、公众号导流语、
《后续文章》导航、错位加粗、拍平表格。

跑法: python -m unittest evals.test_feishu_clean
"""
from __future__ import annotations

import os
import unittest

from evals._test_db import swap_to_test_url

swap_to_test_url()
os.environ.pop("OPENAI_API_KEY", None)

from src.knowledge_pipeline.feishu_clean import (  # noqa: E402
    blocks_to_md,
    clean_md,
)


def _b(bid: str, btype: int, text: str = "", **extra) -> dict:
    from src.knowledge_pipeline.feishu_clean import _FIELD_BY_TYPE
    block: dict = {"block_id": bid, "block_type": btype, **extra}
    field = _FIELD_BY_TYPE.get(btype)
    if field and text:
        block[field] = {"elements": [{"text_run": {"content": text}}]}
    return block


class BlocksToMdTests(unittest.TestCase):
    def test_typical_document_structure(self) -> None:
        """标题层级映射: page->H1, heading1->H2, heading2->H3 —— 切片器契约。"""
        blocks = [
            _b("p", 1, "Redis 数据库"),
            _b("h1", 3, "缓存三大问题"),
            _b("h2", 4, "缓存击穿"),
            _b("t1", 2, "热点 key 过期瞬间大量请求打到 DB。"),
            _b("li1", 12, "逻辑过期"),
            _b("li2", 12, "互斥锁"),
        ]
        title, md = blocks_to_md(blocks)
        self.assertEqual(title, "Redis 数据库")
        self.assertIn("# Redis 数据库", md)
        self.assertIn("## 缓存三大问题", md)
        self.assertIn("### 缓存击穿", md)
        self.assertIn("- 逻辑过期", md)

    def test_image_and_divider_skipped(self) -> None:
        blocks = [
            _b("p", 1, "T"),
            _b("img", 27),
            _b("div", 22),
            _b("t", 2, "正文"),
        ]
        _, md = blocks_to_md(blocks)
        self.assertIn("正文", md)
        self.assertEqual(md.count("\n\n"), 1)  # 只有 H1 与正文两段

    def test_ordered_list_numbering_resets(self) -> None:
        blocks = [
            _b("o1", 13, "第一"), _b("o2", 13, "第二"),
            _b("t", 2, "间隔"),
            _b("o3", 13, "重新开始"),
        ]
        _, md = blocks_to_md(blocks)
        self.assertIn("1. 第一", md)
        self.assertIn("2. 第二", md)
        self.assertIn("1. 重新开始", md)

    def test_table_rebuild(self) -> None:
        """拍平表格 -> md 表格 (上次手工重建的活)。2x2: 表头 + 一行。"""
        blocks = [
            _b("tbl", 31, table={"property": {"row_size": 2, "column_size": 2}},
               children=["c1", "c2", "c3", "c4"]),
            {"block_id": "c1", "block_type": 32, "children": ["ct1"]},
            {"block_id": "c2", "block_type": 32, "children": ["ct2"]},
            {"block_id": "c3", "block_type": 32, "children": ["ct3"]},
            {"block_id": "c4", "block_type": 32, "children": ["ct4"]},
            _b("ct1", 2, "版本"), _b("ct2", 2, "连接复用"),
            _b("ct3", 2, "HTTP/1.1"), _b("ct4", 2, "支持"),
        ]
        _, md = blocks_to_md(blocks)
        self.assertIn("| 版本 | 连接复用 |", md)
        self.assertIn("|---|---|", md)
        self.assertIn("| HTTP/1.1 | 支持 |", md)
        self.assertNotIn("ct1", md)  # cell 子块不重复出现在正文

    def test_code_block_with_language(self) -> None:
        blocks = [{
            "block_id": "c", "block_type": 14,
            "code": {
                "style": {"language": "python"},
                "elements": [{"text_run": {"content": "print('hi')"}}],
            },
        }]
        _, md = blocks_to_md(blocks)
        self.assertIn("```python\nprint('hi')\n```", md)

    def test_bold_style_rendered_fixed(self) -> None:
        """加粗 style + 内容带边缘空格 -> **text** 不留内侧空格。"""
        blocks = [{
            "block_id": "t", "block_type": 2,
            "text": {"elements": [
                {"text_run": {"content": "结论: "}},
                {"text_run": {"content": " 幂等键 ",
                              "text_element_style": {"bold": True}}},
            ]},
        }]
        _, md = blocks_to_md(blocks)
        self.assertIn("**幂等键**", md)
        self.assertNotIn("** 幂等键", md)


class CleanMdTests(unittest.TestCase):
    """raw_content 形态输入的主力清洗 —— 全部真实坑样本。"""

    def test_uuid_and_image_lines_dropped(self) -> None:
        md = clean_md(
            "正文\n"
            "0a1b2c3d-1234-5678-9abc-def012345678\n"
            "divider-cover.gif\n"
            "继续"
        )
        self.assertNotIn("0a1b2c3d", md)
        self.assertNotIn(".gif", md)
        self.assertIn("正文", md)
        self.assertIn("继续", md)

    def test_promo_lines_dropped(self) -> None:
        md = clean_md("知识点。\n觉得有用请关注公众号「xx」, 一键三连!\n下一节。")
        self.assertNotIn("公众号", md)
        self.assertIn("知识点。", md)

    def test_nav_lines_dropped(self) -> None:
        md = clean_md("正文\n《后续文章》: 第 3 篇\n下一篇: HTTP/2\n结尾")
        self.assertNotIn("后续文章", md)
        self.assertNotIn("下一篇", md)

    def test_spaced_bold_fixed(self) -> None:
        md = clean_md("这是 ** 重点 ** 与 **也是重点 ** 的修复")
        self.assertIn("**重点**", md)
        self.assertIn("**也是重点**", md)
        # 内侧空格必须消失 (闭合 ** 后跟空格是合法 md, 不在禁项)
        self.assertNotIn("** 重点", md)
        self.assertNotIn("也是重点 **", md)

    def test_blank_lines_collapsed(self) -> None:
        md = clean_md("a\n\n\n\n\nb")
        self.assertEqual(md, "a\n\nb\n")


if __name__ == "__main__":
    unittest.main()
