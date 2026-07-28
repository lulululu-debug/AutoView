"""golden trace 场景定义 —— Sprint 8.2 task 3。

scripts/record_golden_traces.py (录制) 与 evals/test_trace_replay.py (回放
回归) 共用本模块, 保证两边跑的是**同一场景**。没有 test_ 前缀, discover
不会当测试跑。

场景取典型分布: campus 中等回答多追问 / lateral 长回答少追问 /
campus 回答耗尽提前 finalize。全部 stub 路径 (录制脚本负责 pop key),
所有输入是固定字符串 -> 决策序列确定。
"""
from __future__ import annotations

_GOOD = (
    "比如订单服务我们用了两级令牌桶, 当时结果 P99 大促稳定在 80ms, "
    "我们选择了 lazy refill 减少 Redis 调用, 用了滑动窗口避免突刺。"
)
_MEDIUM = "加了缓存, 效果还行, 具体数字记不清了。"
_INTRO = "我是张三, 做了四年后端, 主要负责订单和对账系统。"

SCENARIOS: list[dict] = [
    {
        "name": "campus-full",
        "track": "campus",
        "title": "后端工程师(校招)",
        "jd": "高并发服务开发, 熟悉 MySQL/Redis, 有项目经验优先。",
        "resume": "张三, 计算机本科。课程项目: 秒杀系统, 用了 Redis 限流。" * 3,
        "answers": [_INTRO] + [_MEDIUM] * 24,
    },
    {
        "name": "lateral-deep",
        "track": "lateral",
        "title": "资深后端工程师",
        "jd": "负责核心交易链路, 分布式一致性, 性能优化经验。",
        "resume": "张三, 4 年后端。订单系统 P99 优化 800ms->350ms; 对账中台 0 到 1。" * 3,
        "answers": [_INTRO] + [_GOOD] * 24,
    },
    {
        "name": "campus-early-finalize",
        "track": "campus",
        "title": "后端工程师(校招)",
        "jd": "高并发服务开发, 熟悉 MySQL/Redis。",
        "resume": "李四, 计算机本科, 实习做过报表系统。" * 3,
        "answers": [_INTRO, _MEDIUM, _MEDIUM],  # 答案耗尽 -> 提前 finalize
    },
]


def isolate_environment() -> dict:
    """golden 场景的确定性隔离, 返回被改动的旧 env 值供恢复。

    - 强制 stub (pop key; 需在 orchestrator/pymilvus import 之后调, F9 坑)
    - ASSESSOR_ENABLED=true (决策 span 完整)
    - **关 Milvus** (pop MILVUS_LITE_URI + reset 单例): 否则召回结果耦合
      本机 corpus 内容, golden 换台机器就废; 关掉后 planner/evaluator 走
      MilvusNotConfigured 的 fallback 分支, 处处确定。
    """
    import os as _os

    # 先把 orchestrator (连带 pymilvus) import 完 —— pymilvus.settings 在
    # import 时 load_dotenv 会把 .env 塞回 os.environ, pop 必须发生在其后
    from src import orchestrator as _orchestrator  # noqa: F401

    saved = {
        k: _os.environ.get(k)
        for k in ("OPENAI_API_KEY", "ASSESSOR_ENABLED", "MILVUS_LITE_URI")
    }
    _os.environ.pop("OPENAI_API_KEY", None)
    _os.environ["ASSESSOR_ENABLED"] = "true"
    _os.environ.pop("MILVUS_LITE_URI", None)
    from src.vector_store.base import reset_client_for_testing
    reset_client_for_testing()
    return saved


def restore_environment(saved: dict) -> None:
    import os as _os
    for k, v in saved.items():
        if v is None:
            _os.environ.pop(k, None)
        else:
            _os.environ[k] = v
    from src.vector_store.base import reset_client_for_testing
    reset_client_for_testing()


def run_scenario(sc: dict) -> str:
    """跑一个场景到 finalize, 返回 session_id (trace 已归档 PG)。
    调用方负责: TEST_POSTGRES_URL swap + isolate_environment()。"""
    from src import db, orchestrator
    from src.schemas import CandidateProfile, JobContext

    job = JobContext(title=sc["title"], jd=sc["jd"], track=sc["track"])
    db.save_job(job)  # lazy resolve 会从 PG 反查 job, 不存则占位路径不同
    candidate = CandidateProfile(resume=sc["resume"])

    answers = list(sc["answers"])
    result = orchestrator.start_session(job, candidate)
    session_id = result.session_id
    while not result.done and answers:
        result = orchestrator.submit_answer(session_id, answers.pop(0))
    orchestrator.finalize(session_id)
    return session_id
