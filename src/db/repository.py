"""schemas(pydantic) <-> ORM 的转换与读写接口。

业务层(orchestrator / agents / 未来的 api)只用本模块的 save_*/load_*。
ORM 类型不向业务层暴露 —— 返回值统一是 src.schemas 里的 pydantic 模型。

幂等约定: save_session/save_report 用 session_id/report_id 做 upsert(merge)。
"""
from __future__ import annotations

from typing import Optional

from src.db.base import session_scope
from src.db.models import (
    CandidateORM,
    EvaluationReportORM,
    InterviewPlanORM,
    InterviewSessionORM,
    JobORM,
    DatasetORM,
    KnowledgeChunkORM,
    QuestionDraftORM,
    SkillBacklogORM,
    ReviewRecordORM,
    SeedQuestionORM,
    UserORM,
)
from src.schemas import (
    CandidateProfile,
    Dataset,
    EvaluationReport,
    InterviewPlan,
    InterviewSession,
    JobContext,
    KnowledgeChunk,
    QuestionCategory,
    QuestionDraft,
    ReviewRecord,
    SeedQuestion,
    SkillBacklog,
    User,
)


# ---------- User (Sprint 5-1) ----------

def save_user(
    *,
    user_id: str,
    username: str,
    hashed_password: str,
    role: str,
) -> None:
    """按 user_id upsert。hashed_password 必须已经过 bcrypt, 本层不再 hash。"""
    with session_scope() as s:
        s.merge(UserORM(
            user_id=user_id,
            username=username,
            hashed_password=hashed_password,
            role=role,
        ))


def load_user_by_username(username: str) -> Optional[tuple[User, str]]:
    """返回 (User pydantic, hashed_password) 或 None。
    密码 hash 仅给 verify_password 用, 上游不应进一步传播。"""
    with session_scope() as s:
        row = (
            s.query(UserORM)
            .filter(UserORM.username == username)
            .one_or_none()
        )
        if row is None:
            return None
        return (
            User(user_id=row.user_id, username=row.username, role=row.role),
            row.hashed_password,
        )


def load_user(user_id: str) -> Optional[User]:
    with session_scope() as s:
        row = s.get(UserORM, user_id)
        if row is None:
            return None
        return User(user_id=row.user_id, username=row.username, role=row.role)


# ---------- HR-side listings (Sprint 5-2) ----------

def list_jobs() -> list[JobContext]:
    """列所有职位, 按 created_at 倒序。"""
    with session_scope() as s:
        rows = s.query(JobORM).order_by(JobORM.created_at.desc()).all()
        return [
            JobContext.model_validate({
                "job_id": r.job_id,
                "title": r.title,
                "jd": r.jd,
                "requirements": r.requirements,
                "company_materials": r.company_materials,
                "track": r.track,
                "followup_policy": r.followup_policy,
                "completion_policy": r.completion_policy,
                "aspects": r.aspects,
            })
            for r in rows
        ]


def _compute_candidate_status(s, c: CandidateORM) -> dict:
    """单候选人的 status 推导, 复用给 list 与单查。
    s: 当前 session_scope; c: 已加载的 CandidateORM 行。

    状态规则 (从 PG 单一真理之源推):
    - plan_pending: 候选人入库但 plan 还没生成
    - ready:       plan 已生成, 但 session 没归档 (没面试 / 面试中 /
                   完成未 finalize)
    - completed:   session + report 都已在 PG (已 finalize)
    - reviewed:    report 之上还有 ReviewRecord

    注: 不查 Redis, 所有"短期态"都收敛成 ready, UI 上写明含义。"""
    plans = (
        s.query(InterviewPlanORM)
        .filter(InterviewPlanORM.candidate_id == c.candidate_id)
        .all()
    )
    has_plan = len(plans) > 0
    session_row = None
    report_row = None
    review_row = None
    for p in plans:
        sess = (
            s.query(InterviewSessionORM)
            .filter(InterviewSessionORM.plan_id == p.plan_id)
            .first()
        )
        if sess is None:
            continue
        session_row = sess
        rep = (
            s.query(EvaluationReportORM)
            .filter(EvaluationReportORM.session_id == sess.session_id)
            .first()
        )
        if rep is not None:
            report_row = rep
            review_row = (
                s.query(ReviewRecordORM)
                .filter(ReviewRecordORM.report_id == rep.report_id)
                .order_by(ReviewRecordORM.reviewed_at.desc())
                .first()
            )
        break

    if not has_plan:
        status = "plan_pending"
    elif report_row is None:
        status = "ready"
    elif review_row is None:
        status = "completed"
    else:
        status = "reviewed"

    return {
        "candidate_id": c.candidate_id,
        "job_id": c.job_id,
        "resume_excerpt": (
            c.resume[:200] + ("..." if len(c.resume) > 200 else "")
        ),
        "status": status,
        "session_id": session_row.session_id if session_row else None,
        "report_id": report_row.report_id if report_row else None,
        "review_decision": review_row.decision if review_row else None,
        "created_at": c.created_at,
    }


def list_candidates_with_status_for_job(job_id: str) -> list[dict]:
    """列某 job 下的候选人 + 进度状态. 单个推导走 _compute_candidate_status."""
    with session_scope() as s:
        cands = (
            s.query(CandidateORM)
            .filter(CandidateORM.job_id == job_id)
            .order_by(CandidateORM.created_at.desc())
            .all()
        )
        return [_compute_candidate_status(s, c) for c in cands]


def get_candidate_with_status(candidate_id: str) -> Optional[dict]:
    """单候选人 + 进度状态. Sprint 5-5 HR 详情页用。"""
    with session_scope() as s:
        c = s.get(CandidateORM, candidate_id)
        if c is None:
            return None
        return _compute_candidate_status(s, c)


# ---------- ReviewRecord (Sprint 5-2) ----------

def save_review_record(review: ReviewRecord) -> None:
    """按 record_id upsert, 同时记录 reviewed_at (由 server 维护时间)。"""
    payload = review.model_dump(mode="json")
    with session_scope() as s:
        s.merge(ReviewRecordORM(
            record_id=payload["record_id"],
            report_id=payload["report_id"],
            reviewer_id=payload["reviewer_id"],
            comments=payload["comments"],
            dimension_overrides=payload["dimension_overrides"],
            decision=payload["decision"],
        ))


def load_review_for_report(report_id: str) -> Optional[ReviewRecord]:
    """读 report 的最新一条 review (按 reviewed_at desc)。
    当前 PATCH 同 report_id 覆盖, 实际只一条; 留 desc 排序为未来扩展。"""
    with session_scope() as s:
        row = (
            s.query(ReviewRecordORM)
            .filter(ReviewRecordORM.report_id == report_id)
            .order_by(ReviewRecordORM.reviewed_at.desc())
            .first()
        )
        if row is None:
            return None
        return ReviewRecord.model_validate({
            "record_id": row.record_id,
            "report_id": row.report_id,
            "reviewer_id": row.reviewer_id,
            "comments": row.comments,
            "dimension_overrides": row.dimension_overrides,
            "decision": row.decision,
            "reviewed_at": row.reviewed_at,
        })


# ---------- SeedQuestion (Sprint 3-3) ----------

def _seed_orm_to_pydantic(r: SeedQuestionORM) -> SeedQuestion:
    return SeedQuestion.model_validate({
        "question_id": r.question_id,
        "role_family": r.role_family,
        "competency": r.competency,
        "text": r.text,
        "source": r.source,
        "category": r.category,
        "dataset_id": r.dataset_id,
        "source_draft_id": r.source_draft_id,
        "key_points": r.key_points,
        "difficulty": r.difficulty,
        "qtype": r.qtype,
    })


def save_seed_question(question: SeedQuestion) -> None:
    """按 question_id upsert; 同内容 = 同 id, 脚本重跑安全。
    Sprint C: 新增 5 个字段一并 upsert; 老 seed_questions.py 脚本依然
    通过 schema 默认值不感知地写入 ('default' / [] / '')."""
    payload = question.model_dump(mode="json")
    with session_scope() as s:
        s.merge(SeedQuestionORM(**payload))


def load_seed_question(question_id: str) -> Optional[SeedQuestion]:
    with session_scope() as s:
        row = s.get(SeedQuestionORM, question_id)
        if row is None:
            return None
        return _seed_orm_to_pydantic(row)


def list_seed_questions(
    *,
    role_family: Optional[str] = None,
    competency: Optional[str] = None,
    category: Optional[str] = None,
    dataset_id: Optional[str] = None,
) -> list[SeedQuestion]:
    """按可选过滤列出题库; 不带过滤就是全表。
    Sprint 5.5: category 过滤让 Planner 按 stage 取对应题源
    (knowledge / scenario).
    Sprint C: dataset_id 过滤让 'JavaGuide 审核入库的题' 可单独查."""
    with session_scope() as s:
        q = s.query(SeedQuestionORM)
        if role_family is not None:
            q = q.filter(SeedQuestionORM.role_family == role_family)
        if competency is not None:
            q = q.filter(SeedQuestionORM.competency == competency)
        if category is not None:
            q = q.filter(SeedQuestionORM.category == category)
        if dataset_id is not None:
            q = q.filter(SeedQuestionORM.dataset_id == dataset_id)
        return [
            _seed_orm_to_pydantic(r) for r in q.all()
        ]


# ---------- Job ----------

def save_job(job: JobContext) -> None:
    """按 job_id upsert。"""
    payload = job.model_dump(mode="json")
    with session_scope() as s:
        s.merge(JobORM(
            job_id=payload["job_id"],
            title=payload["title"],
            jd=payload["jd"],
            requirements=payload["requirements"],
            company_materials=payload["company_materials"],
            track=payload["track"],
            question_source=payload.get("question_source", "rag"),
            followup_policy=payload.get("followup_policy"),
            completion_policy=payload.get("completion_policy"),
            aspects=payload.get("aspects") or [],
        ))


def load_job(job_id: str) -> Optional[JobContext]:
    with session_scope() as s:
        row = s.get(JobORM, job_id)
        if row is None:
            return None
        return JobContext.model_validate({
            "job_id": row.job_id,
            "title": row.title,
            "jd": row.jd,
            "requirements": row.requirements,
            "company_materials": row.company_materials,
            "track": row.track,
            "question_source": row.question_source,
            "followup_policy": row.followup_policy,
            "completion_policy": row.completion_policy,
            "aspects": row.aspects,
        })


# ---------- Candidate ----------

def save_candidate(candidate: CandidateProfile) -> None:
    """按 candidate_id upsert; 要求 candidate.job_id 非空(由 API path param 注入)。"""
    if candidate.job_id is None:
        raise ValueError(
            "save_candidate 要求 candidate.job_id 非空; "
            "API 应当从路径参数 /jobs/{job_id}/candidates 注入"
        )
    payload = candidate.model_dump(mode="json")
    with session_scope() as s:
        s.merge(CandidateORM(
            candidate_id=payload["candidate_id"],
            job_id=payload["job_id"],
            resume=payload["resume"],
            projects=payload["projects"],
            sections=payload["sections"],
        ))


def save_candidate_sections(candidate_id: str, sections: list) -> None:
    """Sprint F: ingest 后台分段完成后单独回填 sections 列。
    不走 save_candidate 整行 merge: 分段与创建是两个时刻, 整行覆盖会把
    创建后其他字段的并发更新抹掉。sections 传 list[ResumeSection]。
    candidate 不存在时静默返回 (创建事务还没提交/已被删, 后台任务不该抛)。"""
    payload = [
        s.model_dump(mode="json") if hasattr(s, "model_dump") else s
        for s in sections
    ]
    with session_scope() as s:
        row = s.get(CandidateORM, candidate_id)
        if row is None:
            return
        row.sections = payload


def load_candidate(candidate_id: str) -> Optional[CandidateProfile]:
    with session_scope() as s:
        row = s.get(CandidateORM, candidate_id)
        if row is None:
            return None
        return CandidateProfile.model_validate({
            "candidate_id": row.candidate_id,
            "job_id": row.job_id,
            "resume": row.resume,
            "projects": row.projects,
            "sections": row.sections or [],
        })


# ---------- InterviewPlan (PG 归档版) ----------
# 注意: 与 src.cache.plan_store 区分 ——
#   db.save_plan / load_plan   持久化, HR 端可读, 走 PG
#   cache.save_plan / load_plan 进行中会话热缓存, 走 Redis, 有 TTL

def save_plan(plan: InterviewPlan, candidate_id: str) -> None:
    """plan 落 PG, 与某个 candidate_id 绑定。
    candidate_id 由调用方显式传入而非从 plan 上读, 是因为 schemas.InterviewPlan 自身
    不携带 candidate_id(plan 一旦生成对 candidate 不可知, 仅 job_id 在内)。"""
    with session_scope() as s:
        s.merge(InterviewPlanORM(
            plan_id=plan.plan_id,
            candidate_id=candidate_id,
            plan_data=plan.model_dump(mode="json"),
        ))


def load_plan(plan_id: str) -> Optional[InterviewPlan]:
    with session_scope() as s:
        row = s.get(InterviewPlanORM, plan_id)
        if row is None:
            return None
        return InterviewPlan.model_validate(row.plan_data)


def load_candidate_for_plan(plan_id: str) -> Optional[CandidateProfile]:
    """从 plan_id 反查 candidate (Sprint 5.5 task 4: orchestrator 在 submit_answer
    里 lazy resolve 项目题时需要 candidate.resume 拿 RAG 切片)。
    InterviewPlanORM.candidate_id 是 FK 列, 直接 join 出来即可。"""
    with session_scope() as s:
        plan_row = s.get(InterviewPlanORM, plan_id)
        if plan_row is None:
            return None
        c = s.get(CandidateORM, plan_row.candidate_id)
        if c is None:
            return None
        return CandidateProfile.model_validate({
            "candidate_id": c.candidate_id,
            "job_id": c.job_id,
            "resume": c.resume,
            "projects": c.projects,
            "sections": c.sections or [],
        })


def load_latest_plan_for_candidate(candidate_id: str) -> Optional[InterviewPlan]:
    """同一候选人允许多版 plan (HR 重跑 Planner), 这里返回最新生成的那个。
    用 created_at desc 而非 plan_id 排序: hex uuid 字典序无意义,
    时间戳才是"最近一次"的真正信号。"""
    with session_scope() as s:
        row = (
            s.query(InterviewPlanORM)
            .filter(InterviewPlanORM.candidate_id == candidate_id)
            .order_by(InterviewPlanORM.created_at.desc())
            .first()
        )
        if row is None:
            return None
        return InterviewPlan.model_validate(row.plan_data)


# ---------- InterviewSession ----------

def save_session(session: InterviewSession) -> None:
    """按 session_id upsert。提交后行内 timestamps 由 DB 维护。"""
    payload = session.model_dump(mode="json")
    with session_scope() as s:
        row = InterviewSessionORM(
            session_id=payload["session_id"],
            plan_id=payload["plan_id"],
            job_id=payload["job_id"],
            status=payload["status"],
            current_round=payload["current_round"],
            history=payload["history"],
            answers=payload["answers"],
            intro_text=payload["intro_text"],
            assessments=payload["assessments"],
            media_ref=payload["media_ref"],
        )
        s.merge(row)


def load_session(session_id: str) -> Optional[InterviewSession]:
    """按 session_id 读取; 不存在返回 None。"""
    with session_scope() as s:
        row = s.get(InterviewSessionORM, session_id)
        if row is None:
            return None
        return InterviewSession.model_validate(
            {
                "session_id": row.session_id,
                "plan_id": row.plan_id,
                "job_id": row.job_id,
                "status": row.status,
                "current_round": row.current_round,
                "history": row.history,
                "answers": row.answers,
                "intro_text": row.intro_text,
                "assessments": row.assessments,
                "media_ref": row.media_ref,
            }
        )


# ---------- EvaluationReport ----------

def save_report(report: EvaluationReport) -> None:
    """按 report_id upsert。要求对应的 session 已先 save_session。"""
    payload = report.model_dump(mode="json")
    with session_scope() as s:
        row = EvaluationReportORM(
            report_id=payload["report_id"],
            session_id=payload["session_id"],
            content_scores=payload["content_scores"],
            performance_observations=payload["performance_observations"],
            overall=payload["overall"],
            summary=payload["summary"],
            needs_human_review=payload["needs_human_review"],
            rag_context_chunk_ids=payload["rag_context_chunk_ids"],
            competency_coverage=payload["competency_coverage"],
        )
        s.merge(row)


def load_report(report_id: str) -> Optional[EvaluationReport]:
    with session_scope() as s:
        row = s.get(EvaluationReportORM, report_id)
        if row is None:
            return None
        return EvaluationReport.model_validate(
            {
                "report_id": row.report_id,
                "session_id": row.session_id,
                "content_scores": row.content_scores,
                "performance_observations": row.performance_observations,
                "overall": row.overall,
                "summary": row.summary,
                "needs_human_review": row.needs_human_review,
                "rag_context_chunk_ids": row.rag_context_chunk_ids,
                "competency_coverage": row.competency_coverage,
            }
        )


# ---------- KnowledgeChunk (Sprint A) ----------

def upsert_knowledge_chunks(chunks: list[KnowledgeChunk]) -> int:
    """批量 upsert; 单事务一次性提交所有 chunk, 返回写入行数。
    调用方应按"单文件一次"调用 (ingest_md_corpus 中边解析边 flush),
    单文件失败时只回滚这一文件的 chunk, 已提交的不受影响。"""
    if not chunks:
        return 0
    payloads = [c.model_dump(mode="json") for c in chunks]
    with session_scope() as s:
        for p in payloads:
            s.merge(KnowledgeChunkORM(**p))
    return len(payloads)


def count_knowledge_chunks(
    *, dataset_id: Optional[str] = None, quality_tag: Optional[str] = None,
) -> int:
    """按可选过滤计数, smoke test 验证用。"""
    with session_scope() as s:
        q = s.query(KnowledgeChunkORM)
        if dataset_id is not None:
            q = q.filter(KnowledgeChunkORM.dataset_id == dataset_id)
        if quality_tag is not None:
            q = q.filter(KnowledgeChunkORM.quality_tag == quality_tag)
        return q.count()


def list_knowledge_chunks(
    *,
    dataset_id: Optional[str] = None,
    exclude_quality_tags: Optional[list[str]] = None,
    limit: Optional[int] = None,
) -> list[KnowledgeChunk]:
    """按可选过滤列出 chunk; Sprint B derive_questions 用 (跳过 low/nav 等)。
    chunk_id 排序保证多次跑顺序稳定 (便于增量恢复时 --limit 跑前 N 不漂移)。"""
    with session_scope() as s:
        q = s.query(KnowledgeChunkORM)
        if dataset_id is not None:
            q = q.filter(KnowledgeChunkORM.dataset_id == dataset_id)
        if exclude_quality_tags:
            q = q.filter(
                ~KnowledgeChunkORM.quality_tag.in_(exclude_quality_tags)
            )
        q = q.order_by(KnowledgeChunkORM.chunk_id)
        if limit is not None:
            q = q.limit(limit)
        rows = q.all()
        return [
            KnowledgeChunk.model_validate({
                "chunk_id": r.chunk_id,
                "source_repo": r.source_repo,
                "source_commit": r.source_commit,
                "dataset_id": r.dataset_id,
                "file_path": r.file_path,
                "doc_title": r.doc_title,
                "doc_tags": r.doc_tags,
                "domain": r.domain,
                "topic": r.topic,
                "heading_path": r.heading_path,
                "is_starred": r.is_starred,
                "text": r.text,
                "char_count": r.char_count,
                "content_hash": r.content_hash,
                "quality_tag": r.quality_tag,
            })
            for r in rows
        ]


def delete_knowledge_chunks_for_dataset(dataset_id: str) -> int:
    """smoke test 重跑用; 只删该 dataset 的, 不影响其他。返回删除行数。
    其他 dataset 同 chunk_id 的行不应存在 (chunk_id 已是 PK), 但隔离原则上
    永远按 dataset_id 过滤。"""
    with session_scope() as s:
        return (
            s.query(KnowledgeChunkORM)
            .filter(KnowledgeChunkORM.dataset_id == dataset_id)
            .delete()
        )


# ---------- QuestionDraft (Sprint B) ----------

def upsert_question_drafts(drafts: list[QuestionDraft]) -> int:
    """批量 upsert draft; 返回写入行数。
    幂等键 draft_id 含 prompt_version, 同 prompt 重跑命中已有 draft 不变。"""
    if not drafts:
        return 0
    payloads = [d.model_dump(mode="json") for d in drafts]
    with session_scope() as s:
        for p in payloads:
            s.merge(QuestionDraftORM(**p))
    return len(payloads)


def count_question_drafts(
    *,
    dataset_id: Optional[str] = None,
    review_status: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> int:
    with session_scope() as s:
        q = s.query(QuestionDraftORM)
        if dataset_id is not None:
            q = q.filter(QuestionDraftORM.dataset_id == dataset_id)
        if review_status is not None:
            q = q.filter(QuestionDraftORM.review_status == review_status)
        if prompt_version is not None:
            q = q.filter(QuestionDraftORM.prompt_version == prompt_version)
        return q.count()


def delete_question_drafts_for_dataset(
    dataset_id: str, *, prompt_version: Optional[str] = None,
) -> int:
    """smoke test 重跑用。可选按 prompt_version 只清旧 prompt 的 draft,
    保留 approved 的不动 (实际删除前过滤 review_status='pending')。"""
    with session_scope() as s:
        q = (
            s.query(QuestionDraftORM)
            .filter(QuestionDraftORM.dataset_id == dataset_id)
            .filter(QuestionDraftORM.review_status == "pending")
        )
        if prompt_version is not None:
            q = q.filter(QuestionDraftORM.prompt_version == prompt_version)
        return q.delete()


# ---------- SkillBacklog (Sprint B+D) ----------

import hashlib as _hashlib


def _skill_id(skill: str) -> str:
    return _hashlib.sha256(skill.strip().lower().encode("utf-8")).hexdigest()[:16]


def record_skill_backlog(
    skills: list[str], *,
    job_id: str = "", candidate_id: str = "",
) -> int:
    """记录候选人 resume 抽出但未匹配 topic 的 skill 集合。
    幂等键 sha256(skill_lower), 重复落同一行 count += 1。
    返回新插入 + 更新的行数 (跟入参 len 一致, 除非有重复)。"""
    if not skills:
        return 0
    seen: set[str] = set()
    touched = 0
    with session_scope() as s:
        for raw in skills:
            sk = raw.strip()
            if not sk or sk in seen:
                continue
            seen.add(sk)
            sid = _skill_id(sk)
            row = s.get(SkillBacklogORM, sid)
            if row is None:
                s.add(SkillBacklogORM(
                    skill_id=sid, skill=sk, count=1,
                    last_job_id=job_id, last_candidate_id=candidate_id,
                ))
            else:
                row.count = (row.count or 0) + 1
                row.last_job_id = job_id or row.last_job_id
                row.last_candidate_id = candidate_id or row.last_candidate_id
            touched += 1
    return touched


def list_skill_backlog(*, limit: int = 200) -> list[SkillBacklog]:
    """按 count 倒序, 让 HR 一眼看哪个 skill 被最多人提到 → 扩库优先级。"""
    with session_scope() as s:
        rows = (
            s.query(SkillBacklogORM)
            .order_by(SkillBacklogORM.count.desc())
            .limit(limit)
            .all()
        )
        return [
            SkillBacklog.model_validate({
                "skill_id": r.skill_id, "skill": r.skill, "count": r.count,
                "last_job_id": r.last_job_id, "last_candidate_id": r.last_candidate_id,
            })
            for r in rows
        ]


# ---------- Dataset 元数据 (Sprint D-lite) ----------

def upsert_dataset(d: Dataset) -> None:
    """按 dataset_id upsert; 重跑 ingest 会更新 topic / commit 等。"""
    payload = d.model_dump(mode="json")
    with session_scope() as s:
        s.merge(DatasetORM(**payload))


def _dataset_orm_to_pydantic(r: DatasetORM) -> Dataset:
    return Dataset.model_validate({
        "dataset_id": r.dataset_id,
        "topic": r.topic,
        "description": r.description,
        "source_repo": r.source_repo,
        "source_commit": r.source_commit,
        "category": r.category,
    })


def get_dataset(dataset_id: str) -> Optional[Dataset]:
    with session_scope() as s:
        r = s.get(DatasetORM, dataset_id)
        if r is None:
            return None
        return _dataset_orm_to_pydantic(r)


def list_datasets() -> list[Dataset]:
    with session_scope() as s:
        return [
            _dataset_orm_to_pydantic(r)
            for r in s.query(DatasetORM).order_by(DatasetORM.dataset_id).all()
        ]


# ---------- chunk 级 topic (Sprint E) ----------
#
# Milvus questions 行的 topic 从 dataset 级细化为「dataset/chunk 复合标签」
# (chunk topic = parse_path 从语料目录二级提取, 粒度 = 子主题), 让同一个大
# dataset 内部也能按子主题选题。
# 链路: seed.source_draft_id → question_drafts.chunk_id → knowledge_chunks.topic。
# 为什么复合而不是裸 chunk topic: 目录名是英文缩略语 ("basis"), 与中文
# query 的 embedding 距离过远 (实测 "Java"→"basis" 0.699, 阈值 0.45 全漏);
# 复合 "JAVA 基础/basis" 保住 dataset 语义 (0.438 ✓) 又带子主题区分度。
# chunk topic 空 (语料在根目录 / HR 上传平铺文件 / 无 draft 谱系的老题) 时
# 退化为纯 dataset.topic, 老数据行为不变。


def compose_question_topic(chunk_topic: str, dataset_topic: str) -> str:
    """Milvus 题目行 topic 值的唯一构造规则 —— 写入端 (approve / reseed) 与
    匹配候选端 (list_question_topics) 必须用同一条, 否则 expr 过滤对不上。"""
    ct = (chunk_topic or "").strip()
    dt = (dataset_topic or "").strip()
    if ct and dt:
        return f"{dt}/{ct}"
    return ct or dt


def get_chunk_topic_for_draft(draft_id: str) -> str:
    """单 draft 反查其 chunk 的 topic; 查不到 (draft/chunk 已删) 返空串。"""
    with session_scope() as s:
        row = (
            s.query(KnowledgeChunkORM.topic)
            .join(
                QuestionDraftORM,
                QuestionDraftORM.chunk_id == KnowledgeChunkORM.chunk_id,
            )
            .filter(QuestionDraftORM.draft_id == draft_id)
            .first()
        )
        return (row[0] or "") if row else ""


def map_draft_chunk_topics() -> dict[str, str]:
    """全量 draft_id → chunk topic 映射, reseed 批量重灌时用
    (一次 join 代替逐行 SQL)。"""
    with session_scope() as s:
        rows = (
            s.query(QuestionDraftORM.draft_id, KnowledgeChunkORM.topic)
            .join(
                KnowledgeChunkORM,
                KnowledgeChunkORM.chunk_id == QuestionDraftORM.chunk_id,
            )
            .all()
        )
        return {draft_id: (topic or "") for draft_id, topic in rows}


def list_question_topics() -> list[str]:
    """topic 匹配的候选列表: 题库 (seed_questions) 里实际会出现的 topic 值,
    用与 Milvus 写入端同一条 compose_question_topic 规则构造。
    只返回至少有一道题挂着的 topic (匹配上空 topic 白占 knowledge 槽位),
    去重 + 排序保证多次调用稳定。"""
    with session_scope() as s:
        rows = (
            s.query(KnowledgeChunkORM.topic, DatasetORM.topic)
            .select_from(SeedQuestionORM)
            .outerjoin(
                QuestionDraftORM,
                QuestionDraftORM.draft_id == SeedQuestionORM.source_draft_id,
            )
            .outerjoin(
                KnowledgeChunkORM,
                KnowledgeChunkORM.chunk_id == QuestionDraftORM.chunk_id,
            )
            .outerjoin(
                DatasetORM,
                DatasetORM.dataset_id == SeedQuestionORM.dataset_id,
            )
            .all()
        )
    topics: set[str] = set()
    for chunk_topic, dataset_topic in rows:
        t = compose_question_topic(chunk_topic or "", dataset_topic or "")
        if t:
            topics.add(t)
    return sorted(topics)


# ---------- 知识库审核工作流 (Sprint C) ----------

# competency_id → name 映射. 来源: src.agents.planner._build_competencies().
# 这里硬编码避免 repository ↔ planner 反向依赖; Sprint D 重构时把映射上提到
# schemas 作为单一 source of truth, planner / repository 都 import 它。
_COMPETENCY_ID_TO_NAME = {
    "comp:tech": "技术深度",
    "comp:comm": "沟通协作",
}


def _resolve_competency_name(competency_id: str) -> str:
    if competency_id not in _COMPETENCY_ID_TO_NAME:
        raise ValueError(
            f"unknown competency_id: {competency_id!r}; "
            f"expected one of {sorted(_COMPETENCY_ID_TO_NAME)}"
        )
    return _COMPETENCY_ID_TO_NAME[competency_id]


def _draft_orm_to_pydantic(d: QuestionDraftORM) -> QuestionDraft:
    return QuestionDraft.model_validate({
        "draft_id": d.draft_id,
        "chunk_id": d.chunk_id,
        "dataset_id": d.dataset_id,
        "question_text": d.question_text,
        "qtype": d.qtype,
        "difficulty": d.difficulty,
        "key_points": d.key_points,
        "prompt_version": d.prompt_version,
        "llm_model": d.llm_model,
        "review_status": d.review_status,
        "category": d.category,
    })


def _seed_question_id_for_draft(
    *, role_family: str, competency_name: str, question_text: str,
) -> str:
    """复用 seed_questions.py 的 _question_id 算法 = sha256(role|comp|text)[:16].
    同 (role, comp, text) → 同 question_id, 让"重复 approve 同题文"幂等。"""
    import hashlib
    raw = f"{role_family}|{competency_name}|{question_text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def list_datasets_summary() -> list[dict]:
    """每 dataset 一行: chunk / draft 各状态 / seed 数 + datasets 表元数据
    (topic, description, source_repo, source_commit)。

    没在 datasets 表 (老数据) 的 dataset_id, 元数据字段返空串。HR 在主页可一眼
    认出"哪些 dataset 缺元数据"。order 按 chunk 数倒序。"""
    from sqlalchemy import func as sa_func
    with session_scope() as s:
        chunk_rows = (
            s.query(
                KnowledgeChunkORM.dataset_id,
                sa_func.count().label("n_chunks"),
            )
            .group_by(KnowledgeChunkORM.dataset_id)
            .all()
        )
        draft_rows = (
            s.query(
                QuestionDraftORM.dataset_id,
                QuestionDraftORM.review_status,
                sa_func.count().label("n"),
            )
            .group_by(QuestionDraftORM.dataset_id, QuestionDraftORM.review_status)
            .all()
        )
        seed_rows = (
            s.query(
                SeedQuestionORM.dataset_id,
                sa_func.count().label("n_seed"),
            )
            .group_by(SeedQuestionORM.dataset_id)
            .all()
        )
        meta_rows = s.query(DatasetORM).all()

    def _empty(ds: str) -> dict:
        return {
            "dataset_id": ds, "n_chunks": 0,
            "n_pending": 0, "n_approved": 0, "n_rejected": 0, "n_seed": 0,
            "topic": "", "description": "",
            "source_repo": "", "source_commit": "",
            "category": "knowledge",
        }

    summary: dict[str, dict] = {}
    for ds, n in chunk_rows:
        summary.setdefault(ds, _empty(ds))["n_chunks"] = n
    for ds, status, n in draft_rows:
        b = summary.setdefault(ds, _empty(ds))
        if status == "pending":
            b["n_pending"] = n
        elif status == "approved":
            b["n_approved"] = n
        elif status == "rejected":
            b["n_rejected"] = n
    for ds, n in seed_rows:
        summary.setdefault(ds, _empty(ds))["n_seed"] = n
    for m in meta_rows:
        b = summary.setdefault(m.dataset_id, _empty(m.dataset_id))
        b["topic"] = m.topic
        b["description"] = m.description
        b["source_repo"] = m.source_repo
        b["source_commit"] = m.source_commit
        b["category"] = m.category

    return sorted(summary.values(), key=lambda r: -r["n_chunks"])


def list_chunks_with_draft_stats(dataset_id: str) -> list[dict]:
    """该 dataset 下所有 chunk + 各状态 draft 数。审核入口列表用。
    返回字段: chunk_id / heading_path / quality_tag / is_starred / char_count /
    n_pending / n_approved / n_rejected。按 (n_pending desc, char_count desc)
    排序: 待审最多的、最长的 chunk 优先, HR 先啃硬骨头。"""
    from sqlalchemy import func as sa_func
    with session_scope() as s:
        chunk_rows = (
            s.query(KnowledgeChunkORM)
            .filter(KnowledgeChunkORM.dataset_id == dataset_id)
            .all()
        )
        draft_rows = (
            s.query(
                QuestionDraftORM.chunk_id,
                QuestionDraftORM.review_status,
                sa_func.count().label("n"),
            )
            .filter(QuestionDraftORM.dataset_id == dataset_id)
            .group_by(QuestionDraftORM.chunk_id, QuestionDraftORM.review_status)
            .all()
        )

    stats: dict[str, dict] = {}
    for cid, status, n in draft_rows:
        bucket = stats.setdefault(cid, {"pending": 0, "approved": 0, "rejected": 0})
        bucket[status] = n

    out = []
    for c in chunk_rows:
        s_ = stats.get(c.chunk_id, {"pending": 0, "approved": 0, "rejected": 0})
        out.append({
            "chunk_id": c.chunk_id,
            "heading_path": c.heading_path,
            "quality_tag": c.quality_tag,
            "is_starred": c.is_starred,
            "char_count": c.char_count,
            "file_path": c.file_path,
            "n_pending": s_["pending"],
            "n_approved": s_["approved"],
            "n_rejected": s_["rejected"],
        })
    out.sort(key=lambda r: (-r["n_pending"], -r["char_count"]))
    return out


def get_chunk_with_drafts(chunk_id: str) -> Optional[dict]:
    """返回 { chunk: KnowledgeChunk, drafts: list[QuestionDraft] }; None 表示
    chunk 不存在。drafts 按 (review_status asc, difficulty, qtype) 排序: pending
    在前 (HR 先看待审), 同状态内按难度 + 类型稳定排序。"""
    with session_scope() as s:
        c = s.get(KnowledgeChunkORM, chunk_id)
        if c is None:
            return None
        drafts = (
            s.query(QuestionDraftORM)
            .filter(QuestionDraftORM.chunk_id == chunk_id)
            .all()
        )
    chunk = KnowledgeChunk.model_validate({
        "chunk_id": c.chunk_id, "source_repo": c.source_repo,
        "source_commit": c.source_commit, "dataset_id": c.dataset_id,
        "file_path": c.file_path, "doc_title": c.doc_title, "doc_tags": c.doc_tags,
        "domain": c.domain, "topic": c.topic, "heading_path": c.heading_path,
        "is_starred": c.is_starred, "text": c.text, "char_count": c.char_count,
        "content_hash": c.content_hash, "quality_tag": c.quality_tag,
    })
    status_order = {"pending": 0, "approved": 1, "rejected": 2}
    diff_order = {"easy": 0, "medium": 1, "hard": 2}
    drafts_sorted = sorted(
        (_draft_orm_to_pydantic(d) for d in drafts),
        key=lambda d: (
            status_order.get(d.review_status, 9),
            diff_order.get(d.difficulty, 9),
            d.qtype,
        ),
    )
    return {"chunk": chunk, "drafts": drafts_sorted}


def edit_question_draft(
    draft_id: str, *,
    question_text: Optional[str] = None,
    key_points: Optional[list[str]] = None,
) -> QuestionDraft:
    """改 draft 内容; review_status 保持 pending。
    None 字段不动 (允许只改题文不改 key_points 或反之)。"""
    with session_scope() as s:
        d = s.get(QuestionDraftORM, draft_id)
        if d is None:
            raise ValueError(f"draft not found: {draft_id}")
        if d.review_status != "pending":
            raise ValueError(
                f"draft {draft_id} status={d.review_status}, only pending editable"
            )
        if question_text is not None:
            d.question_text = question_text
        if key_points is not None:
            d.key_points = key_points
        s.flush()
        return _draft_orm_to_pydantic(d)


def approve_question_draft(
    draft_id: str, *, competency_id: str, role_family: str = "backend",
) -> SeedQuestion:
    """draft 状态置 approved + 写 SeedQuestion (PG 部分); 返回 SeedQuestion
    让 API 层去 embed + Milvus (本函数只管 PG 一致性)。

    幂等: 重复 approve 同 draft 不报错, 返回已写入的 SeedQuestion。
    重复 approve 不同 draft 但题文巧合相同 (同 role+comp+text) → 同 question_id,
    后写覆盖前写 (source_draft_id 会变成最后那个 draft, key_points / dataset_id
    同理)。这是 UI 应该提示用户避免的情况, 当前不做强检。
    """
    competency_name = _resolve_competency_name(competency_id)
    with session_scope() as s:
        d = s.get(QuestionDraftORM, draft_id)
        if d is None:
            raise ValueError(f"draft not found: {draft_id}")
        question_id = _seed_question_id_for_draft(
            role_family=role_family,
            competency_name=competency_name,
            question_text=d.question_text,
        )
        seed = SeedQuestion(
            question_id=question_id,
            role_family=role_family,
            competency=competency_name,
            text=d.question_text,
            source="reviewed_llm_derived",
            category=QuestionCategory(d.category),   # Sprint upload: 从 draft 继承
            dataset_id=d.dataset_id,
            source_draft_id=d.draft_id,
            key_points=d.key_points,
            difficulty=d.difficulty,
            qtype=d.qtype,
        )
        s.merge(SeedQuestionORM(**seed.model_dump(mode="json")))
        if d.review_status != "approved":
            d.review_status = "approved"
        return seed


def reject_question_draft(draft_id: str) -> QuestionDraft:
    """draft 状态置 rejected; 留底但不入 SeedQuestion. 幂等 (重复 reject 不报错)."""
    with session_scope() as s:
        d = s.get(QuestionDraftORM, draft_id)
        if d is None:
            raise ValueError(f"draft not found: {draft_id}")
        if d.review_status == "approved":
            raise ValueError(
                f"draft {draft_id} already approved; "
                f"reject 不允许撤销 approve, 需手动从 seed_questions 删"
            )
        d.review_status = "rejected"
        s.flush()
        return _draft_orm_to_pydantic(d)


def bulk_approve_chunk(
    chunk_id: str, *, competency_id: str, role_family: str = "backend",
) -> list[SeedQuestion]:
    """该 chunk 所有 pending draft 一次 approve; 已 approved/rejected 的不动。
    返回所有新写入的 SeedQuestion 让 API 层一次性 embed + Milvus。
    单事务: 任一道 approve 失败整批回滚 (校验 competency_id 等同步检查)."""
    competency_name = _resolve_competency_name(competency_id)
    written: list[SeedQuestion] = []
    with session_scope() as s:
        drafts = (
            s.query(QuestionDraftORM)
            .filter(QuestionDraftORM.chunk_id == chunk_id)
            .filter(QuestionDraftORM.review_status == "pending")
            .all()
        )
        for d in drafts:
            question_id = _seed_question_id_for_draft(
                role_family=role_family,
                competency_name=competency_name,
                question_text=d.question_text,
            )
            seed = SeedQuestion(
                question_id=question_id,
                role_family=role_family,
                competency=competency_name,
                text=d.question_text,
                source="reviewed_llm_derived",
                category=QuestionCategory(d.category),  # Sprint upload: 从 draft 继承
                dataset_id=d.dataset_id,
                source_draft_id=d.draft_id,
                key_points=d.key_points,
                difficulty=d.difficulty,
                qtype=d.qtype,
            )
            s.merge(SeedQuestionORM(**seed.model_dump(mode="json")))
            d.review_status = "approved"
            written.append(seed)
    return written


def load_report_by_session(session_id: str) -> Optional[EvaluationReport]:
    """便利方法: 按 session_id 反查最终报告(唯一约束保证至多一份)。"""
    with session_scope() as s:
        row = (
            s.query(EvaluationReportORM)
            .filter(EvaluationReportORM.session_id == session_id)
            .one_or_none()
        )
        if row is None:
            return None
        return EvaluationReport.model_validate(
            {
                "report_id": row.report_id,
                "session_id": row.session_id,
                "content_scores": row.content_scores,
                "performance_observations": row.performance_observations,
                "overall": row.overall,
                "summary": row.summary,
                "needs_human_review": row.needs_human_review,
                "rag_context_chunk_ids": row.rag_context_chunk_ids,
                "competency_coverage": row.competency_coverage,
            }
        )
