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

from src.auth import AuthenticationError, AuthorizationError, JwtNotConfigured
from src.cache import RedisNotConfigured
from src.db import DatabaseNotConfigured
from src.orchestrator import SessionInvalidState, SessionNotFound


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

    @app.exception_handler(SessionNotFound)
    async def _session_not_found(
        _req: Request, exc: SessionNotFound,
    ) -> JSONResponse:
        # session 在 Redis 中找不到: 过期 / 已 finalize / 无效 id; 一律 404
        return JSONResponse(
            status_code=404,
            content={"detail": "面试会话不存在或已结束", "error": str(exc)},
        )

    @app.exception_handler(SessionInvalidState)
    async def _session_invalid_state(
        _req: Request, exc: SessionInvalidState,
    ) -> JSONResponse:
        # 状态机不允许的操作: 比如对已 COMPLETED 会话 submit_answer; 409 Conflict
        return JSONResponse(
            status_code=409,
            content={"detail": "面试会话当前状态不允许此操作", "error": str(exc)},
        )

    @app.exception_handler(AuthenticationError)
    async def _auth_required(
        _req: Request, exc: AuthenticationError,
    ) -> JSONResponse:
        # 缺 token / token 坏 / 过期 -> 401, 提示重新登录
        return JSONResponse(
            status_code=401,
            content={"detail": "请先登录", "error": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AuthorizationError)
    async def _forbidden(
        _req: Request, exc: AuthorizationError,
    ) -> JSONResponse:
        # token 合法但 role 不够 -> 403
        return JSONResponse(
            status_code=403,
            content={"detail": "权限不足", "error": str(exc)},
        )

    @app.exception_handler(JwtNotConfigured)
    async def _jwt_not_configured(
        _req: Request, exc: JwtNotConfigured,
    ) -> JSONResponse:
        # 没配 JWT_SECRET, server 状态不对, 503
        return JSONResponse(
            status_code=503,
            content={"detail": "认证未配置", "error": str(exc)},
        )
