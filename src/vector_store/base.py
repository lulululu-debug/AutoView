"""Milvus Lite 客户端单例 + 惰性连接 (镜像 src.db.base / src.cache.base 套路)。

为什么 Milvus Lite (而不是完整 Milvus):
- darwin arm64 支持 in-process, 单文件存储, 零 docker 依赖
- 接口 100% 兼容 pymilvus, 等数据上量后切完整 Milvus 只改 MILVUS_URI
- Sprint 3 dev 期 1 万级题库 / 切片足够; 真到 1M+ 再切

惰性连接:
- import 本模块不读 MILVUS_URI, 不建连接
- 调用 get_client() / init_collections() / upsert / search 时才连接
- 缺 MILVUS_URI 时抛 MilvusNotConfigured, 不污染骨架 / eval / 离线开发
"""
from __future__ import annotations

import os
from typing import Optional

from pymilvus import MilvusClient


class MilvusNotConfigured(RuntimeError):
    """MILVUS_LITE_URI 未设置时, 任何需要 Milvus 的调用都抛出本异常。"""


_client: Optional[MilvusClient] = None


def _build_client() -> MilvusClient:
    # 注意: 不用 MILVUS_URI —— pymilvus 的 ORM 子模块在 import 时
    # 直接读 MILVUS_URI 当 http URL 解析, 撞上我们的文件路径会 import 期 crash。
    # 用专用名字 MILVUS_LITE_URI, 彻底避开。
    uri = os.environ.get("MILVUS_LITE_URI")
    if not uri:
        raise MilvusNotConfigured(
            "MILVUS_LITE_URI 未配置, 无法连接 Milvus。"
            "参考 .env.example, dev 期用 ./milvus_lite.db 即可。"
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
