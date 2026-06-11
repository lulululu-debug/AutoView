"""种子题库填充脚本 —— Sprint 3-3。

职责: LLM 生成(或 fallback 模板) 题目 -> 写 PG (真理之源) -> 向量化 -> 写 Milvus
       (检索副本)。可重跑, 按内容哈希作 question_id 保证幂等。

用法:
    python -m scripts.seed_questions                   # 默认 backend role, 15/dim
    python -m scripts.seed_questions --per 25          # 每维度 25 道
    python -m scripts.seed_questions --dry-run         # 只打印, 不入库

设计:
- PG 是真理之源: Milvus 文件丢了可从 PG 重建; 反过来不行
- 写入顺序: PG -> embed -> Milvus
  * PG 写挂直接抛, 让重跑修
  * Milvus 写挂只 warning, PG 已有真理 (脚本可以单独再跑 reindex 步骤)
- ANTHROPIC_API_KEY 缺时 LLM 走 stub, 本脚本检测到 stub 自动切 fallback 模板,
  保证离线开发也能填出可用题库
- OPENAI_API_KEY 缺时 embed 走 stub 向量(全零), upsert_question 自动跳过 Milvus,
  PG 仍正常写

题目质量是 Sprint 5 (HR 端复核) 才优化的事; Sprint 3-3 关心的是"题库走通"。
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from dataclasses import dataclass

from src import db, embeddings, llm, vector_store
from src.schemas import SeedQuestion

log = logging.getLogger(__name__)

ROLE_FAMILY = "backend"
PER_COMPETENCY_DEFAULT = 15


@dataclass(frozen=True)
class _CompetencySpec:
    name: str
    description: str


_COMPETENCIES = [
    _CompetencySpec(
        name="技术深度",
        description="对岗位核心技术栈的理解深度与实践经验",
    ),
    _CompetencySpec(
        name="沟通协作",
        description="表达清晰度、跨职能协作经验、推动事情落地的能力",
    ),
]

# ---------- LLM 路径 ----------

_LLM_SYSTEM = (
    "你是一名资深技术面试设计专家。"
    "你的任务是生成一批高质量的开放式中文面试题, 用于真实招聘。"
)


def _llm_prompt(competency: _CompetencySpec, n: int) -> str:
    return (
        f"为「后端工程师」岗位的「{competency.name}」维度生成 {n} 道开放式中文面试题。\n"
        "\n"
        "要求:\n"
        "- 每行一题, 不加编号, 不加任何前后缀\n"
        "- 题目应可深挖, 避免 yes/no 题\n"
        "- 题目应贴合后端工程师实际场景\n"
        "\n"
        f"维度释义: {competency.description}"
    )


_NUMBER_PREFIX_RE = re.compile(r"^[\d①-⑳]+[.\、)\.]\s*|^[-*•·]\s*")


def _clean_line(line: str) -> str:
    """去掉可能的编号 / markdown bullet 前缀, strip 空白。"""
    s = line.strip()
    s = _NUMBER_PREFIX_RE.sub("", s)
    return s.strip()


def _parse_llm_output(text: str) -> list[str]:
    return [s for s in (_clean_line(l) for l in text.splitlines()) if s]


# ---------- Fallback 模板 (LLM 不可用时兜底) ----------

_FALLBACK: dict[str, list[str]] = {
    "技术深度": [
        "在你做过的系统中, 如何识别并定位性能瓶颈? 请讲一个具体案例。",
        "你曾如何设计一个高并发场景下的限流方案? 描述你的取舍。",
        "谈谈你对分布式事务的理解, 以及在实际项目中是如何处理的。",
        "介绍一次你做过的数据库优化, 包括前因后果和数据指标。",
        "你如何在生产环境排查一个偶发的、难以复现的 bug?",
        "缓存与数据库一致性如何保证? 请举一个你做过的实例。",
        "你如何设计一个幂等的接口? 背后的考量是什么?",
        "在做容量规划时, 你通常关注哪些指标? 为什么?",
        "你如何评估一个新技术/组件是否适合引入生产?",
        "谈谈你做过的最复杂的微服务拆分, 遇到的难点与解法。",
        "你如何设计一个支持万级 QPS 的消息消费系统?",
        "你在 SLO/SLA 设计上有什么实践? 怎么和产品/SRE 对齐?",
        "你如何处理服务之间的强耦合? 请举一个你重构过的例子。",
        "你做过最有挑战的链路追踪 case 是什么? 怎么定位?",
        "你如何设计一个支持热升级的后端服务?",
    ],
    "沟通协作": [
        "当你和非技术同事就方案产生分歧时, 你通常如何推进?",
        "你如何向 PM 解释一个技术债的影响? 请举例。",
        "在一次跨团队协作中, 你遇到的最大障碍是什么? 怎么解决的?",
        "你如何为一个有争议的设计决策争取团队支持?",
        "描述一次你和 SRE/运维就稳定性方案有分歧的经历。",
        "当 deadline 临近但方案还不成熟时, 你怎么处理?",
        "你如何向新人传递团队的技术规范?",
        "在 code review 中遇到对方风格冲突时, 你怎么沟通?",
        "你怎么向上汇报一个项目的风险与进度?",
        "描述一次你推动跨部门规范落地的经历。",
        "当你的方案被推翻后, 你通常会怎么做?",
        "你如何处理团队内的技术意见分歧?",
        "在多团队协作 incident 中, 你是如何拉齐认知的?",
        "你怎么向业务方说明一个高成本但必要的技术改造?",
        "你的团队 onboarding 流程是怎样的? 你做过哪些改进?",
    ],
}


def _fallback_questions(competency: _CompetencySpec, n: int) -> list[str]:
    pool = _FALLBACK.get(competency.name, [])
    return pool[:n]


# ---------- 核心: 生成 + 双写 ----------

def _question_id(role_family: str, competency: str, text: str) -> str:
    """内容哈希作主键, 让重跑天然幂等。"""
    raw = f"{role_family}|{competency}|{text.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _generate_for_competency(
    competency: _CompetencySpec, n: int,
) -> tuple[list[str], str]:
    """返回 (题目列表, 来源)。LLM 不可用时退到 fallback 模板。"""
    raw = llm.complete(
        _LLM_SYSTEM, _llm_prompt(competency, n), max_tokens=2000,
    )
    if llm.is_stub(raw):
        log.info("LLM 不可用 (stub), 改用 fallback 模板: %s", competency.name)
        return _fallback_questions(competency, n), "fallback_template"

    texts = _parse_llm_output(raw)
    if len(texts) < n:
        log.warning(
            "LLM 只产出 %d 道题 (要 %d 道) for %s, 用 fallback 补足",
            len(texts), n, competency.name,
        )
        existing = set(texts)
        for fb in _fallback_questions(competency, n):
            if fb not in existing:
                texts.append(fb)
                if len(texts) >= n:
                    break
    return texts[:n], "llm_generated"


@dataclass
class SeedResult:
    pg_written: int                          # 真的入了 PG 的题数
    milvus_written: int                      # 真的入了 Milvus 的题数 (stub 向量不算)
    questions: list[SeedQuestion]            # 本次入库的题目


def seed_backend_questions(
    *,
    per_competency: int = PER_COMPETENCY_DEFAULT,
    dry_run: bool = False,
) -> SeedResult:
    """跑题库填充。返回详细结果便于脚本/eval 输出。"""
    all_questions: list[SeedQuestion] = []
    pg_written = 0
    milvus_written = 0

    for comp in _COMPETENCIES:
        texts, source = _generate_for_competency(comp, per_competency)
        for t in texts:
            qid = _question_id(ROLE_FAMILY, comp.name, t)
            q = SeedQuestion(
                question_id=qid,
                role_family=ROLE_FAMILY,
                competency=comp.name,
                text=t,
                source=source,
            )
            all_questions.append(q)
            if dry_run:
                continue

            # 1) PG 真理之源
            db.save_seed_question(q)
            pg_written += 1

            # 2) embed + Milvus 检索副本; embed stub 时 upsert 自动跳过
            vec = embeddings.embed(t)
            ok = vector_store.upsert_question(
                question_id=qid,
                role_family=ROLE_FAMILY,
                competency=comp.name,
                text=t,
                embedding=vec,
            )
            if ok:
                milvus_written += 1

    return SeedResult(
        pg_written=pg_written,
        milvus_written=milvus_written,
        questions=all_questions,
    )


# ---------- CLI ----------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="填 backend 种子题库")
    parser.add_argument(
        "--per", type=int, default=PER_COMPETENCY_DEFAULT,
        help=f"每个维度的题数 (默认 {PER_COMPETENCY_DEFAULT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="不写库, 只打印将要生成的题目",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="详细日志",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # 确保 schema 已迁移
    if not args.dry_run:
        db.init_db()
        vector_store.init_collections()

    result = seed_backend_questions(
        per_competency=args.per, dry_run=args.dry_run,
    )

    print(f"\n[seed_questions] 候选题目数: {len(result.questions)}")
    if args.dry_run:
        print("[seed_questions] dry-run, 未写库:")
        for q in result.questions:
            print(f"  ({q.competency}) {q.text}")
    else:
        print(f"[seed_questions] PG 写入: {result.pg_written}")
        print(
            f"[seed_questions] Milvus 写入: {result.milvus_written}"
            + (" (embed stub 时 0 正常)" if result.milvus_written == 0 else ""),
        )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
