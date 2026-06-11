"""FastAPI 接口层 —— Sprint 2 起。

定位:
- 只做 HTTP 入口 + 校验 + 路由分发 + 把领域异常映射成状态码。
- 业务逻辑全部下沉到 src/orchestrator / src/agents, 本层不直接 import anthropic
  或操作 ORM 行 —— 那是 src/db / src/llm 的事。

启动:
    uvicorn api.main:app --reload

后续 Sprint 2 会陆续加: /jobs /jobs/{id}/candidates /interviews /reports。
"""
