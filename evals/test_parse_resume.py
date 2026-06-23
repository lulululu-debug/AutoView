"""Sprint 5.8 task 1: PDF/docx Resume 解析护栏。

护栏分两层:
1. parser 单元 (src.resume_parser): 解析 + 校验 + normalize 行为, 不走 HTTP。
2. API 端点 (POST /jobs/{id}/candidates/parse-resume): mock fixture 跑 TestClient,
   验状态码 + 错误映射 + job 不存在场景。

fixture 文件 (PDF + docx) 用 pypdf / python-docx 程序生成, 不放二进制进 repo。
"""
from __future__ import annotations

import io
import os
import unittest

# Sprint 5.9 patch: swap to TEST_POSTGRES_URL 防 TRUNCATE 抹掉 dev DB.
from evals._test_db import swap_to_test_url  # noqa: E402
swap_to_test_url()

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
os.environ.pop("OPENAI_API_KEY", None)

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

from src.resume_parser import (  # noqa: E402
    MAX_BYTES,
    MIN_TEXT_CHARS,
    ResumeParseError,
    parse_resume,
)

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_PDF_MIME = "application/pdf"


def _build_docx_bytes(*paragraphs: str) -> bytes:
    """In-memory docx with given paragraphs. Returns raw bytes."""
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_pdf_bytes() -> bytes:
    """In-memory PDF. pypdf 不能直接写文字 (要 reportlab), 但能造一个
    有效空白 PDF; 用于"mime+ext 匹配但内容为空 -> MIN_TEXT_CHARS 拒"路径。"""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


_LONG_RESUME = (
    "张三 / 后端工程师 / 4 年经验。"
    "订单系统 P99 优化: 800ms -> 350ms, 用 Redis 二级缓存 + 本地热点缓存。"
    "对账中台从 0 到 1: 日处理 2 亿笔, 漏对率 0.4‰ -> 0.02‰。"
    "比如和产品就误杀率灰度的争议, 我们拉数据 + 5% 灰度, 结果 0.4% 收尾。"
)


class ParserUnitTests(unittest.TestCase):

    def test_docx_happy_path(self):
        blob = _build_docx_bytes(_LONG_RESUME)
        text = parse_resume(filename="r.docx", mime=_DOCX_MIME, blob=blob)
        self.assertIn("订单系统", text)
        self.assertGreaterEqual(len(text), MIN_TEXT_CHARS)

    def test_docx_paragraphs_normalized(self):
        """超过 2 个连续空段应被压成 1 段间距, 行尾空白被 strip。"""
        blob = _build_docx_bytes(
            "段一: " + "x" * 50,
            "",
            "",
            "",
            "段二: " + "y" * 50,
        )
        text = parse_resume(filename="r.docx", mime=_DOCX_MIME, blob=blob)
        self.assertNotIn("\n\n\n", text, "不应保留 >2 连续换行")

    def test_size_limit_rejected(self):
        blob = b"a" * (MAX_BYTES + 1)
        with self.assertRaises(ResumeParseError) as cm:
            parse_resume(filename="r.docx", mime=_DOCX_MIME, blob=blob)
        self.assertIn("超过", str(cm.exception))

    def test_unsupported_mime_rejected(self):
        with self.assertRaises(ResumeParseError) as cm:
            parse_resume(
                filename="r.txt", mime="text/plain", blob=b"random bytes" * 30,
            )
        self.assertIn("仅接受", str(cm.exception))

    def test_mime_ext_mismatch_rejected(self):
        """声明 PDF mime 但文件名是 .docx -> 双判拦截。"""
        with self.assertRaises(ResumeParseError) as cm:
            parse_resume(
                filename="r.docx", mime=_PDF_MIME,
                blob=_build_docx_bytes(_LONG_RESUME),
            )
        self.assertIn("文件类型不被支持", str(cm.exception))

    def test_parsed_text_too_short_rejected(self):
        """空白 PDF -> extract_text 返空 -> < MIN_TEXT_CHARS, 拒。"""
        blob = _build_pdf_bytes()
        with self.assertRaises(ResumeParseError) as cm:
            parse_resume(filename="r.pdf", mime=_PDF_MIME, blob=blob)
        self.assertIn(f"< {MIN_TEXT_CHARS}", str(cm.exception))

    def test_corrupted_blob_rejected_gracefully(self):
        """随机 bytes 当 docx 给, 内部库报错 -> 包装成 ResumeParseError。
        不向上抛 raw 栈, 候选人看到友好消息。"""
        with self.assertRaises(ResumeParseError) as cm:
            parse_resume(
                filename="r.docx", mime=_DOCX_MIME, blob=b"not really docx" * 30,
            )
        # 内部异常类型不该暴露给用户; 只看 detail 含 "解析失败"
        self.assertIn("解析失败", str(cm.exception))


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL 走 API",
)
class ParseResumeEndpointTests(unittest.TestCase):
    """走 FastAPI TestClient 跑端点, 验状态码 + 错误映射。"""

    @classmethod
    def setUpClass(cls):
        from api.main import create_app
        from src import db
        db.init_db()
        cls.app = create_app()
        cls.client = TestClient(cls.app)
        # 用真 job_id (load_job 会校验), 每个 TestCase 用一个干净 job
        cls.job_id = cls._create_test_job(cls.client)

    @classmethod
    def tearDownClass(cls):
        from src.db.base import session_scope
        from src.db.models import JobORM
        with session_scope() as s:
            s.query(JobORM).filter(JobORM.title == "parse-resume-test").delete()

    @staticmethod
    def _create_test_job(client) -> str:
        r = client.post("/jobs", json={
            "title": "parse-resume-test",
            "jd": "test",
            "requirements": [],
            "company_materials": "",
        })
        assert r.status_code == 201, f"create job failed {r.status_code}"
        return r.json()["job_id"]

    def test_endpoint_happy_path(self):
        blob = _build_docx_bytes(_LONG_RESUME)
        r = self.client.post(
            f"/jobs/{self.job_id}/candidates/parse-resume",
            files={"file": ("resume.docx", blob, _DOCX_MIME)},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("parsed_text", body)
        self.assertIn("订单系统", body["parsed_text"])

    def test_endpoint_wrong_mime_returns_422(self):
        r = self.client.post(
            f"/jobs/{self.job_id}/candidates/parse-resume",
            files={"file": ("resume.txt", b"hello world " * 20, "text/plain")},
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("仅接受", r.json()["detail"])

    def test_endpoint_short_parsed_returns_422(self):
        blob = _build_pdf_bytes()
        r = self.client.post(
            f"/jobs/{self.job_id}/candidates/parse-resume",
            files={"file": ("resume.pdf", blob, _PDF_MIME)},
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("解析后文本", r.json()["detail"])

    def test_endpoint_unknown_job_returns_404(self):
        blob = _build_docx_bytes(_LONG_RESUME)
        r = self.client.post(
            "/jobs/ghost-job/candidates/parse-resume",
            files={"file": ("resume.docx", blob, _DOCX_MIME)},
        )
        self.assertEqual(r.status_code, 404)
        self.assertIn("ghost-job", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
