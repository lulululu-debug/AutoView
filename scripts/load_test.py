"""并发压测 —— Sprint 9 task 3。stub 模式, 零 token。

    python -m scripts.load_test --interviews 30 --workers 10

N 路并发跑完整面试 (start -> submit×M -> finalize), 直调 orchestrator
(测的是 Redis/PG/Milvus/锁的真实竞争, 不掺 HTTP 序列化)。每场答案带
唯一 marker, 结束后校验归档 session 只含自己的 marker —— 抓跨会话串扰。

PG 走 TEST_POSTGRES_URL (不污染 dev); 强制 stub (不烧 token)。
Milvus: lite 模式下并发会触发 reloadable 降级 (fallback 路径, 预期);
起 docker milvus + MILVUS_SERVER_URI 后无此噪声。
"""
from __future__ import annotations

import argparse
import statistics
import threading
import time

from evals._test_db import swap_to_test_url

swap_to_test_url()


def run_one(idx: int, results: list, errors: list) -> None:
    import os
    from src import db, orchestrator
    from src.schemas import CandidateProfile, JobContext

    assert not os.environ.get("OPENAI_API_KEY"), (
        "stub 失效: OPENAI_API_KEY 在 turn 执行期存在, 压测会烧真 token "
        "(F9: pymilvus load_dotenv 回填, 见 main 里的 pop 顺序)"
    )
    marker = f"MARKER-{idx:03d}"
    try:
        job = JobContext(title=f"压测岗-{idx}", jd="高并发服务", track="campus")
        db.save_job(job)
        cand = CandidateProfile(job_id=job.job_id, resume=f"张三 {marker}, 后端。" * 5)
        db.save_candidate(cand)

        latencies: list[float] = []
        turn = orchestrator.start_session(job, cand)
        sid = turn.session_id
        n = 0
        while not turn.done and n < 30:
            t0 = time.time()
            turn = orchestrator.submit_answer(
                sid, f"{marker} 我做过订单系统优化, 用了缓存, 结果还行。第 {n} 答。",
            )
            latencies.append(time.time() - t0)
            n += 1
        orchestrator.finalize(sid)

        # 串扰校验: 归档 session 的所有候选人回答只含自己的 marker
        archived = db.load_session(sid)
        for a in archived.answers:
            assert marker in a.text, f"会话 {sid} 混入他人回答: {a.text[:50]}"
            assert a.text.count("MARKER-") == 1
        results.append((idx, n, latencies))
    except Exception:  # noqa: BLE001
        import traceback
        errors.append((idx, traceback.format_exc()))


def main() -> None:
    ap = argparse.ArgumentParser(description="并发压测 (stub, 零 token)")
    ap.add_argument("--interviews", type=int, default=30)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    import os
    # F9 完整版: pymilvus 首次 import 时 load_dotenv() 会把 .env 的 key 回填。
    # 旧版在这里 pop 后, 线程内才首次 import orchestrator -> pymilvus, 回填发生在
    # pop 之后 —— 2026-07-30 实测坐实: 压测 turn 实际带着真 key 跑 (烧 token +
    # 污染 lite/server 对比)。先在主线程触发完整 import 链, 再 pop 才真正生效。
    import src.orchestrator  # noqa: F401  # 触发 pymilvus load_dotenv
    os.environ.pop("OPENAI_API_KEY", None)  # 强制 stub (pymilvus import 后 pop)
    os.environ["ASSESSOR_ENABLED"] = "true"
    from src.db import init_db
    init_db()

    results: list = []
    errors: list = []
    sem = threading.Semaphore(args.workers)

    def _guarded(i: int) -> None:
        with sem:
            run_one(i, results, errors)

    t0 = time.time()
    threads = [
        threading.Thread(target=_guarded, args=(i,))
        for i in range(args.interviews)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0

    all_lat = [x for _, _, ls in results for x in ls]
    print(f"\n==== 压测结果 ====")
    print(f"完成 {len(results)}/{args.interviews} 场, 失败 {len(errors)}, "
          f"并发 {args.workers}, 总耗时 {wall:.1f}s")
    if all_lat:
        all_lat.sort()
        p50 = all_lat[len(all_lat) // 2]
        p95 = all_lat[int(len(all_lat) * 0.95)]
        print(f"turn 延迟 p50={p50 * 1000:.0f}ms p95={p95 * 1000:.0f}ms "
              f"(n={len(all_lat)})")
        print(f"吞吐: {len(all_lat) / wall:.1f} turns/s")
    for idx, err in errors[:5]:
        print(f"  ✗ #{idx}:\n{err[-1200:]}")
    if errors:
        raise SystemExit(1)
    print("串扰校验: 全部会话只含自己的 marker ✅")


if __name__ == "__main__":
    main()
