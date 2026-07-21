"""HR 端上传 md 文件 → ingest → derive → (auto_approve) → Milvus 入库 —— Sprint upload.

挂在 /admin/upload-knowledge 下, require_hr_user (跟 /admin/* 同权限)。

multipart 表单接 dataset_id / topic / description / category / role_family /
competency_id / auto_approve + 多 md 文件。FastAPI BackgroundTasks 串行跑
ingest → derive → bulk_approve (复用 lib + admin_drafts._embed_and_upsert_milvus)。

设计取舍:
- 不引外部任务队列 (arq/dramatiq): in-process BackgroundTasks 够 MVP, server 重启
  会丢任务, 但日志能看到中断点; 生产化再上 Redis 队列。
- 文件存 tempfile.mkdtemp(): 后台任务结束自动 shutil.rmtree, 不持久占盘。
- dataset_id 冲突直接 409 (HTTP); HR 改 id 重传。merge 模式留作未来扩展。
- 进度 = HR 在 /hr/admin/{ds} 轮询 chunks/drafts/seed 数字滚 (现有 listDatasetChunks).
- LLM 失败单 chunk 跳过不阻塞下一个 (跟 CLI 一致)。
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from api.routes.admin_drafts import _embed_and_upsert_milvus
from src import auth, db
from src.derivation import (
    derive_chunk,
    llm_model_name,
    make_draft_id,
    prompt_version,
)
from src.knowledge_pipeline.parser import build_chunks
from src.schemas import Dataset, KnowledgeChunk, QuestionDraft, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

HrUser = Annotated[User, Depends(auth.require_hr_user)]


_VALID_CATEGORIES = ("knowledge", "scenario")
_VALID_ROLE_FAMILIES = (
    "backend", "frontend", "data_science", "product", "hr",
)
_VALID_COMPETENCIES = ("comp:tech", "comp:comm")


@router.post("/upload-knowledge")
async def upload_knowledge(
    background: BackgroundTasks,
    _user: HrUser,
    dataset_id: str = Form(..., min_length=1),
    topic: str = Form(..., min_length=1),
    description: str = Form(""),
    category: str = Form("knowledge"),
    role_family: str = Form("backend"),
    competency_id: str = Form("comp:tech"),
    auto_approve: bool = Form(True),
    files: list[UploadFile] = File(...),
) -> dict:
    """HR 上传 md 文件 + dataset 元数据, 立即返 {scheduled, n_files} 并启动后台任务。

    - 409: dataset_id 冲突 (PG datasets 表已存在)
    - 400: category / role_family / competency_id 非法
    - 422: files 为空 / 非 .md / 解析失败 (FastAPI 自带校验)
    """
    # 字段校验
    if category not in _VALID_CATEGORIES:
        raise HTTPException(400, f"category 必须为 {_VALID_CATEGORIES}")
    if role_family not in _VALID_ROLE_FAMILIES:
        raise HTTPException(400, f"role_family 必须为 {_VALID_ROLE_FAMILIES}")
    if competency_id not in _VALID_COMPETENCIES:
        raise HTTPException(400, f"competency_id 必须为 {_VALID_COMPETENCIES}")
    if not files:
        raise HTTPException(422, "至少要上传一个 md 文件")

    # dataset_id 冲突直接拒
    if db.get_dataset(dataset_id) is not None:
        raise HTTPException(
            409, f"dataset_id={dataset_id!r} 已存在; 请换个名字"
        )

    # 文件存临时目录 (UploadFile.file 是 SpooledTemporaryFile, async 读后转 disk)
    tmpdir = Path(tempfile.mkdtemp(prefix="upload-knowledge-"))
    saved: list[Path] = []
    n_files = 0
    for f in files:
        if not f.filename:
            continue
        if not f.filename.lower().endswith(".md"):
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(
                422, f"文件 {f.filename!r} 不是 .md, 当前只支持 markdown",
            )
        target = tmpdir / f.filename
        content = await f.read()
        target.write_bytes(content)
        saved.append(target)
        n_files += 1

    if n_files == 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise HTTPException(422, "没有有效的 md 文件")

    # 先 upsert datasets 元数据 (sync), 让 list_datasets_summary 立刻能看到
    db.upsert_dataset(Dataset(
        dataset_id=dataset_id,
        topic=topic,
        description=description,
        source_repo="uploaded",
        source_commit="",
        category=category,
    ))

    background.add_task(
        _run_upload_pipeline,
        tmpdir=tmpdir,
        files=saved,
        dataset_id=dataset_id,
        category=category,
        role_family=role_family,
        competency_id=competency_id,
        auto_approve=auto_approve,
    )

    return {
        "scheduled": True,
        "dataset_id": dataset_id,
        "n_files": n_files,
        "auto_approve": auto_approve,
    }


# ---------- 后台流水线 ----------

def _run_upload_pipeline(
    *,
    tmpdir: Path,
    files: list[Path],
    dataset_id: str,
    category: str,
    role_family: str,
    competency_id: str,
    auto_approve: bool,
) -> None:
    """串行: parse+chunk → upsert_chunks → derive_chunks → (auto_approve?) approve + Milvus.
    完成 / 失败都清理 tmpdir。单文件失败跳过, 不阻塞下一个。"""
    try:
        log.info(
            "upload pipeline start: dataset=%s category=%s files=%d auto_approve=%s",
            dataset_id, category, len(files), auto_approve,
        )
        _ingest_uploaded_files(
            files=files, root=tmpdir, dataset_id=dataset_id,
        )
        chunks = db.list_knowledge_chunks(
            dataset_id=dataset_id,
            exclude_quality_tags=["low_value", "navigation"],
        )
        _derive_for_chunks(chunks=chunks, category=category)
        if auto_approve:
            _approve_and_embed_all(
                dataset_id=dataset_id,
                competency_id=competency_id,
                role_family=role_family,
            )
        log.info("upload pipeline DONE: dataset=%s", dataset_id)
    except Exception:
        log.exception("upload pipeline failed: dataset=%s", dataset_id)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _ingest_uploaded_files(
    *, files: list[Path], root: Path, dataset_id: str,
) -> None:
    """对上传的 md 文件跑 chunk + 入 PG knowledge_chunks。"""
    total = 0
    skipped = 0
    for md in files:
        try:
            rel = PurePosixPath(md.relative_to(root).as_posix())
        except ValueError:
            rel = PurePosixPath(md.name)
        try:
            raw = md.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            log.warning("upload ingest skip (encoding): %s", rel)
            skipped += 1
            continue
        chunks: list[KnowledgeChunk] = build_chunks(
            rel, raw,
            source_name="uploaded",
            commit="",                 # 上传没有 git commit
            dataset_id=dataset_id,
        )
        n = db.upsert_knowledge_chunks(chunks)
        total += n
        log.info("  ingest %s: %d chunks", rel, n)
    log.info("upload ingest done: %d files chunks=%d skipped=%d",
             len(files), total, skipped)


def _derive_for_chunks(*, chunks: list[KnowledgeChunk], category: str) -> None:
    """对 chunks 跑 LLM 反向出题, 落 question_drafts. 失败单 chunk 跳过."""
    pv = prompt_version(category)
    model = llm_model_name()
    log.info(
        "upload derive start: chunks=%d category=%s prompt_version=%s",
        len(chunks), category, pv,
    )
    total_drafts = 0
    zero_chunks = 0
    for i, chunk in enumerate(chunks, 1):
        try:
            derived = derive_chunk(chunk, category=category)
        except Exception:
            log.exception("derive chunk %s failed", chunk.chunk_id)
            zero_chunks += 1
            continue
        if not derived:
            zero_chunks += 1
            continue
        drafts = [
            QuestionDraft(
                draft_id=make_draft_id(
                    chunk_id=chunk.chunk_id,
                    question_text=dq.question_text,
                    category=category,
                ),
                chunk_id=chunk.chunk_id,
                dataset_id=chunk.dataset_id,
                question_text=dq.question_text,
                qtype=dq.qtype,
                difficulty=dq.difficulty,
                key_points=dq.key_points,
                prompt_version=pv,
                llm_model=model,
                category=category,
            )
            for dq in derived
        ]
        db.upsert_question_drafts(drafts)
        total_drafts += len(drafts)
        if i % 20 == 0 or i == len(chunks):
            log.info("  derive progress: %d/%d chunks +%d drafts (total %d)",
                     i, len(chunks), len(drafts), total_drafts)
    log.info("upload derive done: drafts=%d zero_chunks=%d",
             total_drafts, zero_chunks)


def _approve_and_embed_all(
    *, dataset_id: str, competency_id: str, role_family: str,
) -> None:
    """复用 admin_drafts 的 bulk_approve_chunk + _embed_and_upsert_milvus."""
    chunks_stats = db.list_chunks_with_draft_stats(dataset_id)
    pending_chunks = [c for c in chunks_stats if c["n_pending"] > 0]
    log.info("upload approve start: pending chunks=%d", len(pending_chunks))
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
            if i % 20 == 0 or i == len(pending_chunks):
                log.info("  approve progress: %d/%d chunks +%d seeds (total %d)",
                         i, len(pending_chunks), len(seeds), total_seeds)
        except Exception:
            log.exception("approve chunk %s failed", c["chunk_id"])
    log.info("upload approve done: seeds=%d", total_seeds)
