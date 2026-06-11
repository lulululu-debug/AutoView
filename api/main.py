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

from fastapi import FastAPI

API_TITLE = "AI Interview Platform API"
API_VERSION = "0.0.1"


def create_app() -> FastAPI:
    app = FastAPI(title=API_TITLE, version=API_VERSION)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """进程存活检查; 不验证上游(PG/Redis)。"""
        return {"status": "ok", "service": API_TITLE, "version": API_VERSION}

    return app


app = create_app()
