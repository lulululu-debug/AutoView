"""把领域异常映射成 HTTP 状态码。

Sprint 2 期间逐步累积:
- DatabaseNotConfigured / RedisNotConfigured -> 503 (上游不可用)
- 后续: SessionNotFound -> 404, SessionInvalidState -> 409,
        sqlalchemy.exc.IntegrityError -> 409 (FK / unique 冲突)

约定: 业务异常的"映射规则"放本文件; 业务异常本身放各自模块
(src/db, src/cache, src/orchestrator)。把映射集中起来, 一眼能看出每个 API 响应
能返回哪些状态码 —— 后续给 HR / 候选人端写 SDK 时尤其有用。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.cache import RedisNotConfigured
from src.db import DatabaseNotConfigured


def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(DatabaseNotConfigured)
    async def _db_not_configured(
        _req: Request, exc: DatabaseNotConfigured,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "数据库未配置, 服务暂不可用", "error": str(exc)},
        )

    @app.exception_handler(RedisNotConfigured)
    async def _redis_not_configured(
        _req: Request, exc: RedisNotConfigured,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Redis 未配置, 服务暂不可用", "error": str(exc)},
        )
