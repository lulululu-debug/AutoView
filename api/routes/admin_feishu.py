"""飞书导入 admin API —— Sprint 6.7 task 3。

流程: HR 连接飞书 (OAuth) -> 贴 wiki/docx 链接预览 -> 发起导入 ->
BG 任务 (拉 blocks -> blocks_to_md 清洗 -> 复用 admin_upload 的
_run_upload_pipeline 入库+派生) -> 轮询进度 -> 审核队列。

设计:
- OAuth 回调不挂 HR 鉴权 (飞书浏览器跳转带不了我们的语义), 用一次性
  state nonce (Redis TTL 10min) 防 CSRF; 完成后 302 回 HR admin 页。
- 导入进度存 Redis (feishu:import:{job_id}, TTL 1h), 前端轮询。
- dataset_id 冲突语义与 md 上传端点一致 (409, 追加已有 dataset 留后续)。
- 同批内容去重: 上次实战发现子文档"同名不同 token 整篇重复", 按 md
  内容哈希去重。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.routes.admin_upload import (
    _VALID_CATEGORIES,
    _VALID_COMPETENCIES,
    _VALID_ROLE_FAMILIES,
    _run_upload_pipeline,
)
from src import auth, db
from src.cache.base import RedisNotConfigured, get_redis
from src.connectors import feishu
from src.knowledge_pipeline.feishu_clean import blocks_to_md
from src.schemas import Dataset, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/feishu", tags=["admin-feishu"])
HrUser = Annotated[User, Depends(auth.require_hr_user)]

_STATE_KEY = "feishu:oauth_state"
_PROGRESS_KEY = "feishu:import:{job_id}"
_PROGRESS_TTL = 3600


# ---------- 状态与 OAuth ----------

@router.get("/status")
def feishu_status(_user: HrUser) -> dict:
    """前端据此决定入口显隐与"连接飞书"按钮。
    Sprint 6.7 task 5 扩展: source ("env"=部署钉死只读 / "db"=前端可改) +
    app_id 掩码展示; secret 永不回传。"""
    configured = feishu.is_configured()
    return {
        "configured": configured,
        "authorized": feishu.is_authorized() if configured else False,
        "source": feishu.credential_source(),
        "app_id_masked": feishu.app_id_masked(),
    }


class ConfigRequest(BaseModel):
    app_id: str
    app_secret: str


@router.put("/config")
def feishu_put_config(body: ConfigRequest, _user: HrUser) -> dict:
    """前端配置飞书凭证 (Sprint 6.7 task 5)。
    - env 已配置时 409 (部署钉死, UI 不许覆盖)
    - 保存前先连通性测试 (换一次 tenant token), 失败 422 带飞书错误信息
    - app_secret 用 JWT_SECRET 派生密钥加密后入 PG, 永不回传"""
    if (os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET")):
        raise HTTPException(409, "飞书凭证由部署环境 (env) 固定, 不可在界面修改")
    app_id, app_secret = body.app_id.strip(), body.app_secret.strip()
    if not app_id or not app_secret:
        raise HTTPException(422, "app_id / app_secret 不能为空")
    try:
        feishu.test_credentials(app_id, app_secret)
    except feishu.FeishuApiError as e:
        raise HTTPException(422, f"凭证无效: {e.msg} (code={e.code})")
    db.set_app_setting("feishu_app_id", app_id)
    db.set_app_setting("feishu_app_secret", feishu.encrypt_secret(app_secret))
    feishu.invalidate_credentials_cache()
    return {"ok": True, "app_id_masked": feishu.app_id_masked()}


@router.delete("/config")
def feishu_delete_config(_user: HrUser) -> dict:
    if (os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET")):
        raise HTTPException(409, "飞书凭证由部署环境 (env) 固定, 不可在界面清除")
    db.delete_app_setting("feishu_app_id")
    db.delete_app_setting("feishu_app_secret")
    feishu.invalidate_credentials_cache()
    return {"ok": True}


@router.get("/authorize-url")
def feishu_authorize_url(_user: HrUser) -> dict:
    state = uuid.uuid4().hex
    try:
        get_redis().set(_STATE_KEY, state, ex=600)
    except RedisNotConfigured:
        raise HTTPException(503, "Redis 未配置, 无法发起飞书授权")
    return {"url": feishu.authorize_url(state=state)}


@router.get("/oauth/callback")
def feishu_oauth_callback(code: str = "", state: str = "") -> RedirectResponse:
    """飞书授权回调 (无 HR 鉴权, state nonce 防 CSRF)。完成后跳回 HR admin。"""
    done = os.environ.get(
        "FEISHU_OAUTH_DONE_REDIRECT", "http://localhost:3000/hr/admin",
    )
    try:
        expected = get_redis().get(_STATE_KEY)
    except RedisNotConfigured:
        expected = None
    if not code or not state or state != expected:
        raise HTTPException(400, "无效的授权回调 (state 校验失败或缺 code)")
    get_redis().delete(_STATE_KEY)
    feishu.exchange_code(code)
    return RedirectResponse(url=f"{done}?feishu=connected", status_code=302)


# ---------- 预览 ----------

class PreviewRequest(BaseModel):
    url: str


@router.post("/preview")
def feishu_preview(body: PreviewRequest, _user: HrUser) -> dict:
    parsed = feishu.parse_url(body.url)
    if parsed is None:
        raise HTTPException(422, "无法识别的飞书链接 (支持 /wiki/ 与 /docx/)")
    kind, token = parsed

    if kind == "docx":
        return {"kind": "docx", "token": token, "title": None,
                "has_child": False, "children": None,
                "needs_authorization": False}

    node = feishu.get_wiki_node(token)
    resp: dict = {
        "kind": "wiki",
        "token": token,
        "title": node.get("title"),
        "obj_type": node.get("obj_type"),
        "has_child": bool(node.get("has_child")),
        "children": None,
        "needs_authorization": False,
    }
    if resp["has_child"]:
        try:
            children = feishu.list_wiki_children(
                node["space_id"], node["node_token"],
            )
            resp["children"] = [
                {
                    "node_token": c.get("node_token"),
                    "title": c.get("title"),
                    "obj_type": c.get("obj_type"),
                    "has_child": bool(c.get("has_child")),
                }
                for c in children
            ]
        except feishu.FeishuNotAuthorized:
            resp["needs_authorization"] = True
    return resp


# ---------- 导入 ----------

class ImportRequest(BaseModel):
    url: str
    dataset_id: str
    topic: str
    description: str = ""
    category: str = "knowledge"
    role_family: str = "backend"
    competency_id: str = "comp:tech"
    auto_approve: bool = True
    include_children: bool = True


def _prog(job_id: str, **fields) -> None:
    """进度合并写 Redis; Redis 不可用时静默 (导入照跑, 只是看不到进度)。"""
    try:
        r = get_redis()
        key = _PROGRESS_KEY.format(job_id=job_id)
        cur = json.loads(r.get(key) or "{}")
        cur.update(fields)
        r.set(key, json.dumps(cur, ensure_ascii=False), ex=_PROGRESS_TTL)
    except Exception:
        pass


@router.post("/import")
def feishu_import(
    body: ImportRequest, background: BackgroundTasks, _user: HrUser,
) -> dict:
    if body.category not in _VALID_CATEGORIES:
        raise HTTPException(400, f"category 必须为 {_VALID_CATEGORIES}")
    if body.role_family not in _VALID_ROLE_FAMILIES:
        raise HTTPException(400, f"role_family 必须为 {_VALID_ROLE_FAMILIES}")
    if body.competency_id not in _VALID_COMPETENCIES:
        raise HTTPException(400, f"competency_id 必须为 {_VALID_COMPETENCIES}")
    parsed = feishu.parse_url(body.url)
    if parsed is None:
        raise HTTPException(422, "无法识别的飞书链接")
    if db.get_dataset(body.dataset_id) is not None:
        raise HTTPException(
            409, f"dataset_id={body.dataset_id!r} 已存在; 追加导入留后续版本",
        )
    if not feishu.is_configured():
        raise HTTPException(409, "飞书未配置 (FEISHU_APP_ID/SECRET)")

    job_id = uuid.uuid4().hex[:12]
    _prog(job_id, status="queued", dataset_id=body.dataset_id)
    background.add_task(_run_feishu_import, job_id=job_id, req=body,
                        kind=parsed[0], token=parsed[1])
    return {"scheduled": True, "job_id": job_id}


@router.get("/import/{job_id}")
def feishu_import_progress(job_id: str, _user: HrUser) -> dict:
    try:
        raw = get_redis().get(_PROGRESS_KEY.format(job_id=job_id))
    except RedisNotConfigured:
        raise HTTPException(503, "Redis 未配置")
    if not raw:
        raise HTTPException(404, "导入任务不存在或进度已过期")
    return json.loads(raw)


# ---------- 后台任务 ----------

def _safe_filename(title: str, idx: int) -> str:
    name = re.sub(r"[^\w一-鿿-]+", "-", title or f"doc-{idx}").strip("-")
    return f"{(name or f'doc-{idx}')[:60]}.md"


def _collect_documents(kind: str, token: str, include_children: bool) -> list[tuple[str, str]]:
    """返回 [(标题, document_id)]。wiki 封面节点本身是 docx 也纳入。"""
    if kind == "docx":
        return [("", token)]
    node = feishu.get_wiki_node(token)
    docs: list[tuple[str, str]] = []
    if node.get("obj_type") == "docx":
        docs.append((node.get("title") or "", node["obj_token"]))
    if include_children and node.get("has_child"):
        for c in feishu.list_wiki_children(node["space_id"], node["node_token"]):
            if c.get("obj_type") == "docx":
                docs.append((c.get("title") or "", c["obj_token"]))
    return docs


def _run_feishu_import(*, job_id: str, req: ImportRequest, kind: str, token: str) -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="feishu-import-"))
    try:
        _prog(job_id, status="fetching")
        docs = _collect_documents(kind, token, req.include_children)
        if not docs:
            _prog(job_id, status="failed", error="该节点下没有可导入的 docx 文档")
            return
        _prog(job_id, total=len(docs), done=0)

        saved: list[Path] = []
        seen_hash: set[str] = set()
        skipped_dup = 0
        for i, (title, doc_id) in enumerate(docs):
            _prog(job_id, done=i, current=title or doc_id)
            blocks = feishu.fetch_document_blocks(doc_id)
            doc_title, md = blocks_to_md(blocks)
            # 同批去重: 同名不同 token 的整篇重复 (实战坑)
            digest = hashlib.sha256(md.encode("utf-8")).hexdigest()
            if digest in seen_hash:
                skipped_dup += 1
                continue
            seen_hash.add(digest)
            path = tmpdir / _safe_filename(doc_title or title, i)
            path.write_text(md, encoding="utf-8")
            saved.append(path)
        _prog(job_id, done=len(docs), skipped_dup=skipped_dup)

        if not saved:
            _prog(job_id, status="failed", error="全部文档为空或重复, 无内容可导入")
            return

        # source_commit 记 <节点token>@<日期> (corpus-md-conventions 溯源约定)
        db.upsert_dataset(Dataset(
            dataset_id=req.dataset_id,
            topic=req.topic,
            description=req.description,
            source_repo="feishu",
            source_commit=f"{token}@{time.strftime('%Y-%m-%d')}",
            category=req.category,
        ))

        _prog(job_id, status="pipeline_running", n_files=len(saved))
        # 复用 md 上传的同一条流水线: 入库 -> 派生 -> (可选) 批准+embed。
        # 该函数自己负责清理 tmpdir。
        _run_upload_pipeline(
            tmpdir=tmpdir,
            files=saved,
            dataset_id=req.dataset_id,
            category=req.category,
            role_family=req.role_family,
            competency_id=req.competency_id,
            auto_approve=req.auto_approve,
        )
        _prog(
            job_id, status="done",
            chunks=db.count_knowledge_chunks(dataset_id=req.dataset_id),
            drafts=db.count_question_drafts(dataset_id=req.dataset_id),
        )
    except feishu.FeishuNotAuthorized as e:
        _prog(job_id, status="failed", error=f"需要飞书授权: {e}")
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        log.exception("feishu import 失败 job=%s", job_id)
        _prog(job_id, status="failed", error=str(e)[:200])
        shutil.rmtree(tmpdir, ignore_errors=True)
