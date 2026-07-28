"""Milvus 客户端单例 + 惰性连接 (镜像 src.db.base / src.cache.base 套路)。

两种模式 (Sprint 9), server 优先:
- **MILVUS_SERVER_URI** (如 http://localhost:19530): 独立 Milvus standalone,
  多进程/多 worker 安全 —— 高并发部署的前提, 也解除「跑批必须停 uvicorn」
  的单进程限制。可选 MILVUS_TOKEN 鉴权。
- **MILVUS_LITE_URI** (如 ./milvus_lite.db): in-process 单文件, 零 docker
  依赖, dev/eval/CI 默认。**单进程限制**: 多 worker 共享同一文件会触发
  released-collection 降级。
两者 MilvusClient 接口完全一致, 业务代码零感知。

惰性连接:
- import 本模块不读 env, 不建连接
- 调用 get_client() / init_collections() / upsert / search 时才连接
- 两个 env 都缺时抛 MilvusNotConfigured, 不污染骨架 / eval / 离线开发
"""
from __future__ import annotations

import os
from typing import Optional

from pymilvus import MilvusClient


class MilvusNotConfigured(RuntimeError):
    """MILVUS_SERVER_URI / MILVUS_LITE_URI 都未设置时,
    任何需要 Milvus 的调用都抛出本异常。"""


_client: Optional[MilvusClient] = None


def _build_client() -> MilvusClient:
    # 注意: 环境变量刻意不叫 MILVUS_URI —— pymilvus 的 ORM 子模块在 import
    # 时直接读 MILVUS_URI 当 http URL 解析, 撞上文件路径会 import 期 crash。
    # Sprint 9: server 模式优先 (多进程安全), lite 兜底 (dev/eval 零依赖)。
    server_uri = os.environ.get("MILVUS_SERVER_URI")
    if server_uri:
        token = os.environ.get("MILVUS_TOKEN") or ""
        return MilvusClient(uri=server_uri, token=token)
    uri = os.environ.get("MILVUS_LITE_URI")
    if not uri:
        raise MilvusNotConfigured(
            "MILVUS_SERVER_URI / MILVUS_LITE_URI 均未配置, 无法连接 Milvus。"
            "参考 .env.example: dev 期用 MILVUS_LITE_URI=./milvus_lite.db;"
            "多 worker 部署用 MILVUS_SERVER_URI=http://localhost:19530。"
        )
    return MilvusClient(uri=uri)


def get_client() -> MilvusClient:
    """返回单例 MilvusClient, 首次调用时按 MILVUS_URI 建立。"""
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def reset_client_for_testing() -> None:
    """测试用: 关掉 client 单例, 让下一次 get_client() 重新按 MILVUS_URI 建立。
    业务代码不要调用本函数。"""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    _client = None
