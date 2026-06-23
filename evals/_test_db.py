"""Test DB 隔离工具 —— Sprint 5.9 patch.

历史背景: 早期 evals 直接读 POSTGRES_URL, 多个 eval 文件在 setUp 里跑
`TRUNCATE users, jobs, ...` 清表。结果 dev 跑 `python -m unittest discover -s evals`
会把真实 dev DB 的所有数据 wipe (实战遇到一次: 用户 HR UI 里手动建的 job /
session / report 被 eval 抹掉, 没法恢复)。

修复策略:
- 强制 eval 走 TEST_POSTGRES_URL, 不再共用 POSTGRES_URL
- 模块顶用 `swap_to_test_url()` 把 os.environ["POSTGRES_URL"] 改成
  TEST_POSTGRES_URL, 这样后面 `from src import db` 起的 engine 全指向 test DB
- 没设 TEST_POSTGRES_URL -> `swap_to_test_url()` 把 POSTGRES_URL 也 pop 掉,
  让所有 PG-依赖 eval 通过 `@skipUnless(os.environ.get("POSTGRES_URL"))` 自动跳过,
  不会再误碰 prod DB
- TEST_POSTGRES_URL == POSTGRES_URL 直接 RuntimeError 拒绝跑

用法:
    # evals/test_xxx.py 顶部, BEFORE `from src import db` / `from api ...`
    from evals._test_db import swap_to_test_url
    swap_to_test_url()

    # 然后正常 import + 写 testcase
    from src import db
    ...

setUp 里如果需要再次保证 (重复防御性), 调 `ensure_test_db()` 抛 SkipTest。

如何起 test DB:
    createdb interview_test
    # 然后 .env 里加 TEST_POSTGRES_URL=postgresql+psycopg://joy@localhost:5432/interview_test
"""
from __future__ import annotations

import os
import unittest

_SWAPPED = False


def swap_to_test_url() -> None:
    """模块顶调用: 把 POSTGRES_URL 切到 TEST_POSTGRES_URL, 不然 pop 掉。

    必须在 `from src import db` / `from api.main import create_app` 之前调,
    否则 src.db.base 的 engine 缓存已绑定到原 POSTGRES_URL, 改 env 太晚。
    重复调用安全 (二次调用直接 return)。
    """
    global _SWAPPED
    if _SWAPPED:
        return
    _SWAPPED = True
    # dotenv 可能还没被加载, 先尝试加载一次让 .env 里的 TEST_POSTGRES_URL 进环境
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    test_url = os.environ.get("TEST_POSTGRES_URL")
    prod_url = os.environ.get("POSTGRES_URL")

    if test_url and prod_url and test_url.strip() == prod_url.strip():
        # 完全一样, 拒绝跑 —— 跑了真的会把 dev 数据 TRUNCATE
        raise RuntimeError(
            "TEST_POSTGRES_URL 与 POSTGRES_URL 相同, eval 会 TRUNCATE 真实 DB. "
            "请在 .env 里把 TEST_POSTGRES_URL 设成不同的库 (例 interview_test)."
        )

    if test_url:
        os.environ["POSTGRES_URL"] = test_url
    else:
        # 没配 test URL: pop POSTGRES_URL, 让 PG-eval 的 @skipUnless 跳过
        os.environ.pop("POSTGRES_URL", None)


def ensure_test_db() -> None:
    """setUp 里再保险一次: TEST_POSTGRES_URL 未配置时 skip; 配了但当前
    POSTGRES_URL 没被 swap 也 skip (防 import 顺序错把 swap 跳过的 case)."""
    test_url = os.environ.get("TEST_POSTGRES_URL")
    if not test_url:
        raise unittest.SkipTest(
            "TEST_POSTGRES_URL 未配置, 跳过 PG eval. "
            "在 .env 设例 TEST_POSTGRES_URL=postgresql+psycopg://joy@localhost:5432/interview_test"
        )
    cur = os.environ.get("POSTGRES_URL")
    if cur != test_url:
        raise unittest.SkipTest(
            f"POSTGRES_URL ({cur!r}) 未被 swap 成 TEST_POSTGRES_URL; "
            "请确认 eval 文件顶部调了 swap_to_test_url()"
        )
