"""Sprint 3 收官: RAG 端到端 provenance eval。

定位:
- 之前每个组件 (planner/evaluator/ingestion) 都有自己的 RAG eval, 验证"我自己
  这一步召回对了"。本 eval 把整条 API 链路串起来跑一遍, 验证 source 字段
  能从 plan/report 一路追到 PG 真实数据。
- 关键: sprint.md "完成标准 = 可追溯到召回片段" 在这里成为 hard assertion。
- 也作综合的"跨 job/candidate 隔离"测试, 验证 source_id 过滤在端到端真有效。

实现:
- 用 TestClient 走 POST /jobs, POST /candidates, POST /interviews, GET /report
  全套 API 端点
- monkey-patch src.embeddings.embed 为固定向量, 让 Milvus search 必中,
  避免依赖真 OpenAI key 也保证测试稳定
- LLM 走 stub (OPENAI_API_KEY pop 掉), summary/题目用各自的 fallback 路径,
  但 source_id / chunk_ids 该填就填 (RAG 召回路径独立于 LLM 是否真用)
"""
from __future__ import annotations

import os
import tempfile
import unittest


def _fixed_vector(dim: int = 1536) -> list[float]:
    """固定向量让所有 search COSINE 距离=0, 全部命中。"""
    v = [0.0] * dim
    v[0] = 1.0
    return v


@unittest.skipUnless(
    os.environ.get("POSTGRES_URL") and os.environ.get("REDIS_URL"),
    "需要 POSTGRES_URL + REDIS_URL 跑端到端 API",
)
class _RagE2EBase(unittest.TestCase):
    """共享: Milvus 临时 DB + PG init + embed monkey-patch + API TestClient。"""

    @classmethod
    def setUpClass(cls):
        cls._db = tempfile.mktemp(suffix=".db")
        os.environ.pop("MILVUS_URI", None)
        os.environ["MILVUS_LITE_URI"] = cls._db
        from fastapi.testclient import TestClient
        from src.db import init_db
        from src.vector_store import init_collections
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        init_db()           # 含增量迁移加 rag_context_chunk_ids 列
        init_collections()
        from api.main import create_app
        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls):
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        for ext in ("", "-shm", "-wal", ".lock"):
            try:
                os.unlink(cls._db + ext)
            except OSError:
                pass

    def setUp(self):
        # pymilvus.settings.load_dotenv() 已加回 OPENAI 等; e2e 路径走 stub LLM
        # 让题目/summary 走 fallback, 但 RAG 召回路径独立, source 字段仍要正确填写
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)

        # 清表
        import psycopg
        url = os.environ["POSTGRES_URL"].replace("+psycopg", "")
        with psycopg.connect(url) as conn:
            conn.execute(
                "TRUNCATE seed_questions, jobs, candidates, interview_plans, "
                "interview_sessions, evaluation_reports CASCADE"
            )
        from src import cache
        cache.get_redis().flushdb()
        # 清 Milvus collection 内容
        from src.vector_store import drop_collections, init_collections
        from src.vector_store.base import reset_client_for_testing
        reset_client_for_testing()
        drop_collections()
        init_collections()

        # monkey-patch: ingestion / planner / evaluator 都从 src.embeddings 拿 embed,
        # 改 module 属性一处影响所有调用方
        from src import embeddings as emb_mod
        self._original_embed = emb_mod.embed

        def _fake(text, **_kw):
            return _fixed_vector()

        emb_mod.embed = _fake

    def tearDown(self):
        from src import embeddings as emb_mod
        emb_mod.embed = self._original_embed

    def _seed_questions(self, *, role_family: str, mapping: dict[str, str]) -> None:
        """同时落 PG (seed_questions 表) + Milvus (questions collection)。"""
        from src.db import save_seed_question
        from src.schemas import SeedQuestion
        from src.vector_store import upsert_question

        for competency, qid in mapping.items():
            text = f"种子题 [{qid}] for {competency}"
            sq = SeedQuestion(
                question_id=qid,
                role_family=role_family,
                competency=competency,
                text=text,
                source="e2e_seed",
            )
            save_seed_question(sq)
            upsert_question(
                question_id=qid,
                role_family=role_family,
                competency=competency,
                text=text,
                embedding=_fixed_vector(),
            )


class RagE2EProvenanceTests(_RagE2EBase):
    """端到端: API 跑通后, plan / report 上所有 source 字段必须能追回 PG/Milvus。"""

    def test_full_pipeline_provenance(self):
        from src.db import load_seed_question

        # 1) 灌种子题 (PG + Milvus 双写)
        self._seed_questions(
            role_family="backend",
            mapping={
                "技术深度": "seed-tech-e2e",
                "沟通协作": "seed-comm-e2e",
            },
        )

        # 2) POST /jobs (含 JD + 公司资料, BG ingest 走 monkey-patched embed)
        job_body = {
            "title": "后端工程师",
            "jd": "负责交易系统的稳定性与性能, 熟悉分布式与数据库优化。" * 15,
            "requirements": ["分布式", "数据库优化"],
            "company_materials": "公司以交易系统为核心, 强调稳定性与数据驱动。" * 15,
        }
        r = self.client.post("/jobs", json=job_body)
        self.assertEqual(r.status_code, 201)
        job_id = r.json()["job_id"]

        # 3) POST /jobs/{id}/candidates (BG: ingest_resume + planner 并行)
        cand_body = {
            "resume": (
                "张三 / 后端 / 4 年。" * 10
                + "电商订单 P99 从 800ms 优化到 350ms。" * 10
                + "对账中台从 0 到 1, 日处理 2 亿笔。" * 10
            ),
            "projects": ["P99 优化", "对账中台"],
        }
        r = self.client.post(f"/jobs/{job_id}/candidates", json=cand_body)
        self.assertEqual(r.status_code, 202)
        cand_id = r.json()["candidate_id"]

        # 4) GET plan: 验所有 source 字段
        r = self.client.get(f"/jobs/{job_id}/candidates/{cand_id}/plan")
        self.assertEqual(r.status_code, 200)
        plan = r.json()

        questions = plan["rounds"][0]["questions"]
        knowledge_q = [q for q in questions if q["category"] == "knowledge"]
        project_q = [q for q in questions if q["category"] == "project_experience"]

        # 4a) knowledge 题: source_question_id 必须指向真实 SeedQuestion (PG 中能查到)
        self.assertEqual(len(knowledge_q), 2)
        for q in knowledge_q:
            src_id = q.get("source_question_id")
            self.assertIsNotNone(src_id, f"knowledge 题缺 source_question_id: {q}")
            seed = load_seed_question(src_id)
            self.assertIsNotNone(
                seed, f"source_question_id {src_id} 在 PG seed_questions 找不到",
            )
            self.assertEqual(seed.role_family, "backend")
            # 知识题维度应当与种子维度一致 (Milvus filter 应当生效)
            self.assertIn(seed.competency, {"技术深度", "沟通协作"})

        # 4b) project 题: source_chunk_ids 非空, 且全部指向本 candidate 的 resume 切片
        self.assertEqual(len(project_q), 2)
        for q in project_q:
            chunk_ids = q.get("source_chunk_ids", [])
            self.assertGreater(
                len(chunk_ids), 0, f"project 题缺 chunk_ids: {q}",
            )
            for cid in chunk_ids:
                self.assertTrue(
                    cid.startswith(f"{cand_id}:resume:"),
                    f"chunk_id 不属于本 candidate: {cid}",
                )

        # 5) POST /interviews + 一路答到 done + GET /report
        r = self.client.post("/interviews", json={"candidate_id": cand_id})
        self.assertEqual(r.status_code, 201)
        sid = r.json()["session_id"]

        # 用 Sprint 0 验过的长答案: 首答短触发追问, 后四答 >60 字且含
        # specificity hints 不触发, 总 5 turn 到 done
        proven_answers = [
            "做过一些性能优化, 主要是慢查询和缓存。",
            "比如去年大促前, 订单查询 P99 从 800ms 涨到 2s。"
            "我们排查发现是某个复合索引被改后失效, 同时 Redis 出现热点 key 击穿。"
            "我加回索引并改造为本地缓存 + Redis 二级缓存, 最终 P99 回到 350ms。",
            "通常我会先用数据让对方理解我担心的点, 比如拉一份线上回放或历史 case,"
            "再一起定义可灰度的中间方案; 我们组上半年的风控规则争议就是这么收的。",
            "对账中台那段最有挑战。日处理 2 亿笔, 早期对账延迟超过 30 分钟。"
            "我们把单表对账改成分桶 + 并行 worker, 引入幂等键, 用 Kafka 做回放,"
            "结果延迟降到 3 分钟, 漏对率从 0.4‰ 降到 0.02‰。",
            "上半年风控那次, 产品要求 24 小时内全量, 我担心误杀率。"
            "我拉了 SRE 一起跑离线回放, 用数据让产品同意先灰度 5%, 一周后再全量,"
            "误杀率从 3.1% 降到 0.4% 才放开。",
        ]
        for ans in proven_answers:
            r = self.client.post(f"/interviews/{sid}/answers", json={"text": ans})
            self.assertEqual(r.status_code, 200)
            if r.json()["done"]:
                break

        r = self.client.get(f"/interviews/{sid}/report")
        self.assertEqual(r.status_code, 200)
        report = r.json()

        # 6) report.rag_context_chunk_ids 应当指向本 job 的 JD / 公司资料切片
        rag_ids = report.get("rag_context_chunk_ids", [])
        self.assertGreater(
            len(rag_ids), 0, "evaluator 应当召回到 ingest 进 Milvus 的 JD/公司资料",
        )
        for cid in rag_ids:
            self.assertTrue(
                cid.startswith(job_id),
                f"rag chunk 应当属于本 job, 实际: {cid}",
            )
            self.assertTrue(
                ":jd:" in cid or ":cm:" in cid,
                f"rag chunk 应当来自 JD 或公司资料 (resume 走 candidate_id, 不该混入): {cid}",
            )


class RagE2EIsolationTests(_RagE2EBase):
    """端到端跨 job/candidate 隔离: A 的数据绝不能被 B 的 plan/report 召回。"""

    def test_other_job_resume_not_pulled_into_my_plan(self):
        """B 的 plan 不应被 A 的 resume 污染 (source_id 过滤跨候选人有效)。"""
        # job + candidate A: 灌进自己的 resume
        r = self.client.post("/jobs", json={
            "title": "job A", "jd": "x" * 200,
        })
        job_a = r.json()["job_id"]
        r = self.client.post(f"/jobs/{job_a}/candidates", json={
            "resume": "A 的简历内容, 包含 A 项目。" * 20,
        })
        cand_a = r.json()["candidate_id"]
        # 触发完 plan
        self.client.get(f"/jobs/{job_a}/candidates/{cand_a}/plan")

        # job + candidate B
        r = self.client.post("/jobs", json={
            "title": "job B", "jd": "y" * 200,
        })
        job_b = r.json()["job_id"]
        r = self.client.post(f"/jobs/{job_b}/candidates", json={
            "resume": "B 的简历内容, 包含 B 项目。" * 20,
        })
        cand_b = r.json()["candidate_id"]
        r = self.client.get(f"/jobs/{job_b}/candidates/{cand_b}/plan")
        self.assertEqual(r.status_code, 200)
        plan_b = r.json()

        # B 的 project 题 chunk_ids 不应包含 A 的
        for q in plan_b["rounds"][0]["questions"]:
            for cid in q.get("source_chunk_ids", []):
                self.assertFalse(
                    cid.startswith(cand_a),
                    f"B 的 plan 召到了 A 的 resume chunk: {cid}",
                )

    def test_other_job_jd_not_pulled_into_my_report(self):
        """job-B 的 report 不应召到 job-A 的 JD/公司资料切片。"""
        # job A: 单独灌一份 JD
        r = self.client.post("/jobs", json={
            "title": "job A", "jd": "A 公司的 JD 内容。" * 30,
        })
        job_a = r.json()["job_id"]

        # job B + 候选人 + 走完流程
        r = self.client.post("/jobs", json={
            "title": "job B", "jd": "B 公司的 JD 内容。" * 30,
            "company_materials": "B 公司的资料。" * 30,
        })
        job_b = r.json()["job_id"]
        r = self.client.post(f"/jobs/{job_b}/candidates", json={
            "resume": "候选人简历内容。" * 30,
        })
        cand_b = r.json()["candidate_id"]
        r = self.client.post("/interviews", json={"candidate_id": cand_b})
        sid = r.json()["session_id"]
        # Sprint 0 验过的长答案 (首答短触发追问, 其余 >60 字 + specificity hints)
        proven_answers = [
            "做过一些性能优化, 主要是慢查询和缓存。",
            "比如去年大促前, 订单查询 P99 从 800ms 涨到 2s。"
            "我们排查发现是某个复合索引被改后失效, 同时 Redis 出现热点 key 击穿。"
            "我加回索引并改造为本地缓存 + Redis 二级缓存, 最终 P99 回到 350ms。",
            "通常我会先用数据让对方理解我担心的点, 比如拉一份线上回放或历史 case,"
            "再一起定义可灰度的中间方案; 我们组上半年的风控规则争议就是这么收的。",
            "对账中台那段最有挑战。日处理 2 亿笔, 早期对账延迟超过 30 分钟。"
            "我们把单表对账改成分桶 + 并行 worker, 引入幂等键, 用 Kafka 做回放,"
            "结果延迟降到 3 分钟, 漏对率从 0.4‰ 降到 0.02‰。",
            "上半年风控那次, 产品要求 24 小时内全量, 我担心误杀率。"
            "我拉了 SRE 一起跑离线回放, 用数据让产品同意先灰度 5%, 一周后再全量,"
            "误杀率从 3.1% 降到 0.4% 才放开。",
        ]
        for ans in proven_answers:
            r = self.client.post(f"/interviews/{sid}/answers", json={"text": ans})
            if r.json()["done"]:
                break
        r = self.client.get(f"/interviews/{sid}/report")
        self.assertEqual(r.status_code, 200)
        report = r.json()

        # report 召回到的 chunks 必须全部属于 job-B
        for cid in report.get("rag_context_chunk_ids", []):
            self.assertTrue(
                cid.startswith(job_b),
                f"B 的 report 召到了非本 job 的 chunk: {cid}",
            )
            self.assertFalse(
                cid.startswith(job_a),
                f"B 的 report 召到了 A 的 JD chunk: {cid}",
            )


if __name__ == "__main__":
    unittest.main()
