"""sim 环境引导 —— .env 加载 + TEST DB 切换 + 真 key 硬校验。

所有 sim CLI 入口第一行调 bootstrap(); 之后才 import src.* 业务模块
(src.db 等是惰性连接, import 无副作用, 但保持这个顺序最不容易踩坑)。
"""
from __future__ import annotations

import os


def bootstrap() -> None:
    try:  # .env 便利加载; CI/cron 直接注入环境变量也行
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # 效果评估必须真 LLM: stub 跑出来的"效果"是自欺, 直接拒绝启动
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "sim 需要真 OPENAI_API_KEY (.env 配置), "
            "拒绝在 stub 模式下假装评估效果"
        )

    # PG 默认切 TEST 库: sim 数据可弃, sim/runs/ 的 JSONL 才是真相源
    if os.environ.get("SIM_USE_DEV_DB", "").lower() in ("1", "true"):
        print(f"[sim] 使用 dev PG (SIM_USE_DEV_DB=1): 仿真数据会出现在 HR Dashboard")
    else:
        test_url = os.environ.get("TEST_POSTGRES_URL")
        if test_url:
            os.environ["POSTGRES_URL"] = test_url
            print("[sim] PG 已切 TEST_POSTGRES_URL (仿真数据不进 dev 库)")
        else:
            print("[sim] 警告: 未配 TEST_POSTGRES_URL, 仿真数据将写进 dev 库")
