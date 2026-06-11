"""FastAPI 应用工厂 + 健康检查端点。

设计:
- 用 create_app() 工厂返回 app, 而不是直接在模块顶部建实例。
  原因: TestClient / 多 worker / 嵌入子应用 都受益于"按需构造", 且能避免
  import 时副作用(比如 init_db) 在不需要 app 的场景被意外触发。
- 模块顶部仍然导出 `app = create_app()`, 这样 `uvicorn api.main:app` 这种
  最朴素的启动方式照样工作。
- /health 故意做成纯静态返回, 不查 PG/Redis。原因: 健康检查的语义是
  "进程在跑能接请求", 不是"上游一切就绪"; 后者放到 /readyz(待定) 里去做。

后续会接入异常映射(SessionNotFound -> 404 等), 留给加第一个业务端点时一起做,
本骨架不预先布线。
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.exceptions import register_handlers
from api.routes import candidates as candidates_routes
from api.routes import interviews as interviews_routes
from api.routes import jobs as jobs_routes

API_TITLE = "AI Interview Platform API"
API_VERSION = "0.0.1"

# Sprint 4 起加 CORS, 允许候选人端 Next.js dev server 调用。
# 生产期通过 CORS_ALLOWED_ORIGINS 环境变量配置具体源。
_DEFAULT_DEV_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return _DEFAULT_DEV_ORIGINS
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title=API_TITLE, version=API_VERSION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_handlers(app)
    app.include_router(jobs_routes.router)
    app.include_router(candidates_routes.router)
    app.include_router(interviews_routes.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """进程存活检查; 不验证上游(PG/Redis)。"""
        return {"status": "ok", "service": API_TITLE, "version": API_VERSION}

    return app


app = create_app()
