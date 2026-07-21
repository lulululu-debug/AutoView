"""知识库审核 API —— Sprint C。

挂在 /admin/* 前缀下, 全部 require_hr_user (跟 /hr/* 同权限)。
单独 /admin/ 而不挂 /hr/ 下是为了后续可拆出独立的"题库 admin" role,
url 不需要重命名。

端点:
- GET   /admin/datasets                          dataset 总览 (chunk / draft / seed 统计)
- GET   /admin/datasets/{ds}/chunks              该 dataset 下所有 chunk + 各状态 draft 数
- GET   /admin/chunks/{cid}/drafts               单 chunk 上下文 + 全部 drafts (审核页用)
- PATCH /admin/drafts/{did}                      改 draft 题文 / key_points (仅 pending)
- POST  /admin/drafts/{did}/approve              单题 approve → 写 SeedQuestion + Milvus
- POST  /admin/drafts/{did}/reject               单题 reject (留底不入题库)
- POST  /admin/chunks/{cid}/bulk-approve         打包 approve 该 chunk 所有 pending

approve 时立即触发 embed + Milvus 入库, HR 审核完该题立刻可被 Planner 召回。
Milvus 写挂不阻塞 PG 一致性 (PG 已 commit), 静默降级 + 日志。
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from api.schemas import (
    ApproveDraftRequest,
    ChunkWithDraftStats,
    ChunkWithDraftsResponse,
    DatasetSummary,
    EditDraftRequest,
)
from src import auth, db, embeddings, vector_store
from src.schemas import QuestionDraft, SeedQuestion, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

HrUser = Annotated[User, Depends(auth.require_hr_user)]


# ---------- 读 ----------

@router.get("/datasets", response_model=list[DatasetSummary])
def list_datasets(_user: HrUser) -> list[dict]:
    """所有 dataset 的统计; 按 chunk 数倒序。"""
    return db.list_datasets_summary()


@router.get("/diag/milvus-questions-count")
def diag_milvus_count(_user: HrUser) -> dict:
    """Sprint C 调试: 查 Milvus questions 集合行数 (从 API server 进程内调用,
    避免外部脚本与 server 抢 milvus_lite.db 文件锁导致 DataDirLockedError)。"""
    try:
        n = vector_store.count_questions()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "count": n}


@router.get("/diag/milvus-stats")
def diag_milvus_stats(_user: HrUser) -> dict:
    """questions + documents 两个集合的行数总览."""
    try:
        nq = vector_store.count_questions()
    except Exception as e:
        nq = f"error: {e}"
    try:
        nd = vector_store.count_documents()
    except Exception as e:
        nd = f"error: {e}"
    return {"questions": nq, "documents": nd}


@router.post("/diag/plan-preview")
def diag_plan_preview(body: dict, _user: HrUser) -> dict:
    """Sprint B+D 验证: 给一个简化 job + resume, 跑 plan() 返回 knowledge stage
    题目 + matched_topics (从 log 不好抓, 直接调用 topic_match 同步算一份).

    body 字段: title, jd, resume, aspects (list of {name, description, competency_id})。
    不持久化 job/candidate, 纯内存跑 (job_id / candidate_id 随机 uuid)."""
    from src.agents.planner import (
        plan, _compute_knowledge_topic_matching, _build_competencies,
    )
    from src.schemas import (
        JobContext, CandidateProfile, ProfileAspect, Track,
    )

    aspects_raw = body.get("aspects") or []
    aspects = [
        ProfileAspect(
            competency_id=a.get("competency_id", "comp:tech"),
            name=a.get("name", ""),
            description=a.get("description", ""),
        )
        for a in aspects_raw
        if a.get("name")
    ]
    job = JobContext(
        title=body.get("title", "测试岗"),
        jd=body.get("jd", ""),
        requirements=body.get("requirements") or [],
        company_materials="",
        role_family=body.get("role_family", "backend"),
        track=Track(body.get("track", "lateral")),
        aspects=aspects,
    )
    candidate = CandidateProfile(resume=body.get("resume", ""))

    tech, _ = _build_competencies()
    matched, unmatched, _detail = _compute_knowledge_topic_matching(
        job, candidate, tech,
    )

    p = plan(job, candidate)
    knowledge = []
    for r in p.rounds:
        if r.stage.value != "knowledge":
            continue
        for q in r.questions:
            knowledge.append({
                "competency_id": q.competency_id,
                "category": q.category.value,
                "source_question_id": q.source_question_id,
                "text": q.text,
            })
    return {
        "matched_topics": matched,
        "unmatched_skills": unmatched,
        "knowledge_questions": knowledge,
    }


@router.get("/diag/milvus-search-sample")
def diag_milvus_search_sample(_user: HrUser) -> dict:
    """Sprint D-lite 调试: 用一个固定 query 召回 top-3, 看新字段
    (dataset_id / topic / difficulty) 是否真的写进了 Milvus。"""
    vec = embeddings.embed("Java 反射机制的实现原理")
    if embeddings.is_stub_vector(vec):
        return {"error": "stub embedding"}
    hits = vector_store.search_questions(
        embedding=vec, top_k=3,
        role_family="backend", competency="技术深度", category="knowledge",
    )
    return {"hits": [
        {
            "question_id": h.get("question_id"),
            "topic": h.get("topic"),
            "difficulty": h.get("difficulty"),
            "dataset_id": h.get("dataset_id"),
            "text": (h.get("text") or "")[:80],
        }
        for h in hits
    ]}


@router.post("/diag/reseed-milvus-questions")
def reseed_milvus_questions(_user: HrUser) -> dict:
    """Sprint C: drop Milvus questions 集合 → 重建 → 从 PG seed_questions 全表
    重新 embed + upsert. 让 Milvus 跟 PG 严格一致, 清掉 stale 行 (历史
    seed_questions.py 多次跑留下的 / PG 删过但 Milvus 没回收的)。

    documents 集合不动 (JD/Resume RAG 数据不影响 stale 题库)。
    Embedding 已经走 Redis cache, 重灌大部分 cache hit 秒级完成。
    """
    client = vector_store.get_client()
    before = vector_store.count_questions()

    if client.has_collection(vector_store.COLLECTION_QUESTIONS):
        client.drop_collection(vector_store.COLLECTION_QUESTIONS)
    vector_store.init_collections()  # 幂等: 重建 questions, documents 已存在跳过

    # Sprint D-lite: 一次性预加载映射, 避免 reseed 769 行时 769 次单行 SQL。
    # Sprint E: topic = dataset/chunk 复合标签, 与 _embed_and_upsert_milvus
    # 同一条 compose_question_topic 构造规则。
    topic_by_dataset = {d.dataset_id: d.topic for d in db.list_datasets()}
    topic_by_draft = db.map_draft_chunk_topics()

    seeds = db.list_seed_questions()
    upserted = skipped_stub = errored = 0
    for seed in seeds:
        vec = embeddings.embed(seed.text)
        if embeddings.is_stub_vector(vec):
            skipped_stub += 1
            continue
        topic = db.compose_question_topic(
            topic_by_draft.get(seed.source_draft_id or "", ""),
            topic_by_dataset.get(seed.dataset_id, ""),
        )
        try:
            vector_store.upsert_question(
                question_id=seed.question_id,
                role_family=seed.role_family,
                competency=seed.competency,
                text=seed.text,
                embedding=vec,
                category=seed.category.value,
                dataset_id=seed.dataset_id,
                topic=topic,
                difficulty=seed.difficulty,
            )
            upserted += 1
        except Exception:
            log.exception("reseed: failed for %s", seed.question_id)
            errored += 1

    after = vector_store.count_questions()
    return {
        "before": before, "after": after,
        "pg_seed_total": len(seeds),
        "upserted": upserted, "skipped_stub": skipped_stub, "errored": errored,
        "removed_stale": max(0, before - after) if upserted else 0,
    }


@router.post("/datasets/{dataset_id}/bulk-approve-all")
def bulk_approve_all_pending(
    dataset_id: str,
    body: ApproveDraftRequest,
    bg: BackgroundTasks,
    _user: HrUser,
) -> dict:
    """该 dataset 所有 pending draft 一次 approve. 走后台任务, 立即返 202,
    HR 在 /hr/admin 刷新看 pending → 0, approved 上涨。

    每个 chunk 调一次 db.bulk_approve_chunk (PG 单事务), 再 embed + Milvus.
    某 chunk 失败不阻塞下一个 (log + continue), Milvus 失败同样降级 (PG 一致即可)."""
    pending = db.count_question_drafts(dataset_id=dataset_id, review_status="pending")
    if pending == 0:
        return {"scheduled": False, "reason": "no pending drafts"}
    bg.add_task(
        _approve_all_pending_in_dataset,
        dataset_id=dataset_id,
        competency_id=body.competency_id,
        role_family=body.role_family,
    )
    return {"scheduled": True, "to_approve_estimate": pending}


def _approve_all_pending_in_dataset(
    *, dataset_id: str, competency_id: str, role_family: str,
) -> None:
    """后台任务: 遍历 pending chunks → bulk_approve_chunk + embed + Milvus.
    HR 在 admin 主页 refresh 能看 pending 数字下降。"""
    log.info("bulk-approve-all start: dataset=%s competency=%s",
             dataset_id, competency_id)
    chunks = db.list_chunks_with_draft_stats(dataset_id)
    pending_chunks = [c for c in chunks if c["n_pending"] > 0]
    log.info("  pending chunks: %d", len(pending_chunks))

    total_seeds = 0
    for i, c in enumerate(pending_chunks, 1):
        try:
            seeds = db.bulk_approve_chunk(
                c["chunk_id"],
                competency_id=competency_id,
                role_family=role_family,
            )
            for seed in seeds:
                _embed_and_upsert_milvus(seed)
            total_seeds += len(seeds)
            if i % 10 == 0 or i == len(pending_chunks):
                log.info(
                    "  bulk-approve-all progress: [%d/%d] chunk=%s +%d seeds (total %d)",
                    i, len(pending_chunks), c["chunk_id"], len(seeds), total_seeds,
                )
        except Exception:
            log.exception("  bulk-approve-all: chunk %s failed", c["chunk_id"])
    log.info("bulk-approve-all done: dataset=%s total %d seeds",
             dataset_id, total_seeds)


@router.get(
    "/datasets/{dataset_id}/chunks",
    response_model=list[ChunkWithDraftStats],
)
def list_chunks(dataset_id: str, _user: HrUser) -> list[dict]:
    """该 dataset 下所有 chunk + 各状态 draft 数, 按 pending 数倒序。"""
    return db.list_chunks_with_draft_stats(dataset_id)


@router.get(
    "/chunks/{chunk_id}/drafts",
    response_model=ChunkWithDraftsResponse,
)
def get_chunk_drafts(chunk_id: str, _user: HrUser) -> dict:
    """单 chunk 上下文 + 全部 drafts (按状态/难度/类型排序)。404 表示
    chunk 不存在; chunk 存在但无 draft 是合法的 (返 drafts=[])。"""
    result = db.get_chunk_with_drafts(chunk_id)
    if result is None:
        raise HTTPException(404, f"chunk {chunk_id} not found")
    return {
        "chunk": result["chunk"].model_dump(mode="json"),
        "drafts": [d.model_dump(mode="json") for d in result["drafts"]],
    }


# ---------- 写 ----------

@router.patch("/drafts/{draft_id}", response_model=QuestionDraft)
def edit_draft(
    draft_id: str, body: EditDraftRequest, _user: HrUser,
) -> QuestionDraft:
    """改题文 / key_points; status 保持 pending; 改完前端再调 approve。
    405 = 已 approved/rejected 不可改 (强约束: 审核后改字段会让 SeedQuestion
    跟 draft 不一致, 索性禁掉)。"""
    try:
        return db.edit_question_draft(
            draft_id,
            question_text=body.question_text,
            key_points=body.key_points,
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(404, msg)
        raise HTTPException(409, msg)


@router.post("/drafts/{draft_id}/approve", response_model=SeedQuestion)
def approve_draft(
    draft_id: str, body: ApproveDraftRequest, _user: HrUser,
) -> SeedQuestion:
    """approve 单道 draft: 写 SeedQuestion + embed + 入 Milvus。
    幂等: 重复 approve 同 draft 返回相同 SeedQuestion, Milvus 同 question_id
    被 upsert 覆盖。"""
    try:
        seed = db.approve_question_draft(
            draft_id,
            competency_id=body.competency_id,
            role_family=body.role_family,
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(404, msg)
        raise HTTPException(400, msg)
    _embed_and_upsert_milvus(seed)
    return seed


@router.post("/drafts/{draft_id}/reject", response_model=QuestionDraft)
def reject_draft(draft_id: str, _user: HrUser) -> QuestionDraft:
    """reject 单道 draft: 状态置 rejected 留底, 不进 SeedQuestion 不动 Milvus。"""
    try:
        return db.reject_question_draft(draft_id)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(404, msg)
        raise HTTPException(409, msg)


@router.post(
    "/chunks/{chunk_id}/bulk-approve",
    response_model=list[SeedQuestion],
)
def bulk_approve_chunk(
    chunk_id: str, body: ApproveDraftRequest, _user: HrUser,
) -> list[SeedQuestion]:
    """该 chunk 所有 pending → approved (写 SeedQuestion 单事务). 已 approved /
    rejected 的不动. 返回新写的 SeedQuestion 列表, 顺序按 draft_id 稳定。
    每道顺次 embed + Milvus, 个别 embed 失败不阻塞整批 (PG 已 commit)。"""
    try:
        seeds = db.bulk_approve_chunk(
            chunk_id,
            competency_id=body.competency_id,
            role_family=body.role_family,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    for seed in seeds:
        _embed_and_upsert_milvus(seed)
    return seeds


# ---------- 内部: embed + Milvus 复用 seed_questions.py 三步流 ----------

def _embed_and_upsert_milvus(seed: SeedQuestion) -> None:
    """复用 scripts/seed_questions.py 的写法: embed stub → 跳过 Milvus 不污染向量空间。
    Milvus 任何 error → 静默 + 日志, PG 已 commit 不回滚, HR 审核体验不卡。
    可代价: Milvus 缺该题 → Planner 召回不到, 但 PG 真理之源在, 后续可补 reseed 脚本。

    Sprint D-lite: 同步把 dataset_id / topic / difficulty (seed 自带) 写进
    Milvus 新字段, 让召回侧可按这三字段过滤。
    Sprint E: topic 从 dataset 级细化为「dataset/chunk 复合标签」
    (seed.source_draft_id → draft → chunk.topic, 构造规则见
    db.compose_question_topic); chunk topic 空 (上传平铺文件 / 无 draft 谱系
    的老题) 退化为纯 dataset.topic, 老数据行为不变。lookup 失败/没填时 topic
    走空串, 不影响 upsert (Milvus 字段允许空字符串)."""
    vec = embeddings.embed(seed.text)
    if embeddings.is_stub_vector(vec):
        log.info(
            "skip Milvus upsert for %s: stub embedding (OPENAI_API_KEY 未配)",
            seed.question_id,
        )
        return
    chunk_topic = ""
    if seed.source_draft_id:
        chunk_topic = db.get_chunk_topic_for_draft(seed.source_draft_id)
    ds = db.get_dataset(seed.dataset_id)
    topic = db.compose_question_topic(chunk_topic, ds.topic if ds else "")
    try:
        vector_store.upsert_question(
            question_id=seed.question_id,
            role_family=seed.role_family,
            competency=seed.competency,
            text=seed.text,
            embedding=vec,
            category=seed.category.value,
            dataset_id=seed.dataset_id,
            topic=topic,
            difficulty=seed.difficulty,
        )
    except Exception:
        log.exception(
            "Milvus upsert failed for %s; PG row已 commit, 可后续补 reseed",
            seed.question_id,
        )
