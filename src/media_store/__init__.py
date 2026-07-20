"""录制归档存储 —— Sprint 6-5。

候选人面试录像 (MediaRecorder webm 分片) 的唯一落盘点。

- 惰性配置: import 不读 env; 未配置 MEDIA_STORAGE_DIR 时 is_configured()=False,
  API 层直接 409, 前端 /media/config 探测后根本不会启动 MediaRecorder。
- 本地目录起步; 换 S3/MinIO = 重新实现本模块同名函数 (append_chunk 换
  分段上传), 调用方一行不改 —— 私有化部署继续用本地盘。
- **只录不判**: 本模块只做字节落盘。任何"拿录像做分析/打分"的调用都属于
  Sprint 7 Analyzer 且受 ARCHITECTURE.md §7 约束, 不许从这里长出来。

留存策略 (PIPL): purge_older_than() 按 mtime 删过期文件,
scripts/cleanup_recordings.py 是 cron 薄包装。默认 90 天 ——
与前端 consent 文案 (web session/media.tsx 的 RETENTION_DAYS) 保持同步。
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

# 与 web/src/app/interview/.../session/media.tsx RETENTION_DAYS 同步
DEFAULT_RETENTION_DAYS = 90

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class MediaStoreNotConfigured(RuntimeError):
    """MEDIA_STORAGE_DIR 未设置时, 任何落盘调用都抛出本异常。"""


class InvalidSessionId(ValueError):
    """session_id 含路径字符等非法内容 (防目录穿越)。"""


def is_configured() -> bool:
    return bool(os.environ.get("MEDIA_STORAGE_DIR"))


def _root() -> Path:
    raw = os.environ.get("MEDIA_STORAGE_DIR")
    if not raw:
        raise MediaStoreNotConfigured(
            "MEDIA_STORAGE_DIR 未配置, 无法归档录像。参考 .env.example"
        )
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path_for(session_id: str) -> Path:
    if not _SAFE_ID.match(session_id):
        raise InvalidSessionId(f"非法 session_id: {session_id!r}")
    return _root() / f"{session_id}.webm"


def append_chunk(session_id: str, chunk: bytes) -> None:
    """顺序追加一个 MediaRecorder 分片。

    同一 MediaRecorder 流的分片按序拼接就是合法 webm (首片含容器头,
    后续为延续段)。**顺序由前端串行上传链保证** (recorder.ts), 本函数只管追加。
    """
    if not chunk:
        return
    with open(_path_for(session_id), "ab") as f:
        f.write(chunk)


def media_ref(session_id: str) -> str | None:
    """归档引用 (本地后端 = 绝对路径); 无录像 / 未配置返回 None。
    orchestrator.finalize 时挂到 session.media_ref 随会话落 PG。"""
    try:
        p = _path_for(session_id)
    except (MediaStoreNotConfigured, InvalidSessionId):
        return None
    return str(p) if p.exists() else None


def purge_older_than(days: int = DEFAULT_RETENTION_DAYS) -> list[str]:
    """删除 mtime 超过 days 天的录像, 返回删掉的文件名列表 (打审计日志用)。
    未配置时返回空列表 (cron 环境缺 env 不炸)。"""
    if not is_configured():
        return []
    cutoff = time.time() - days * 86400
    removed: list[str] = []
    for p in _root().glob("*.webm"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed.append(p.name)
        except OSError:
            continue  # 单个文件失败不挡整体清理
    return removed
