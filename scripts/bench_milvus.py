"""Milvus 检索层专项压测 —— lite vs server 对比。

EVALUATION.md 2026-07-30 勘误的记账清偿: load_test 的 stub 口径下 Milvus
完全不被触达, lite/server 的检索层差异需要专项压测才能单独度量。

    python -m scripts.bench_milvus --mode lite                # scratch lite 文件
    python -m scripts.bench_milvus --mode lite --coholder     # + 第二进程共持同一 lite 文件 (复现 uvicorn 共存)
    python -m scripts.bench_milvus --mode server              # 需 docker compose up -d milvus

设计要点:
- 自建 bench_vectors collection (dim/metric 与生产 collections 对齐:
  1536 / COSINE), **不碰 questions/documents**, 跑完 drop 零残留;
  lite 走独立 scratch 文件 (默认 ./milvus_lite_bench.db, 命中 .gitignore
  的 milvus_lite* 规则), 不碰 dev 数据。
- 语料 3626 条合成单位向量 (对齐真实题库规模, 固定 seed 可复现)。
- 查询向量优先走**真 embedding** (OPENAI_API_KEY 存在时 ~10 次
  text-embedding-3-small 调用, embed 时延单独报告, 不混进 search 计时);
  缺 key 退合成向量, 只影响语义不影响时延度量。
- LLM chat 全程不涉及 —— search 计时只包含 Milvus 调用本身。
- 度量: 单线程 search p50/p95 / 10 线程并发 p50/p95+吞吐+错误数 /
  read-own-write 探针 (insert 后立即检索, Strong vs Bounded 各 10 次,
  验证 Sprint 9 的一致性修复依据)。

F9 纪律: pymilvus import 时 load_dotenv 会把 .env 的 MILVUS_*/OPENAI_* 塞回
环境, 所以 env 的 set/pop 必须发生在 import 之后 (本脚本 main 内已保证)。
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import threading
import time

COLLECTION = "bench_vectors"
DIM = 1536
CORPUS = 3626
SEED = 42
QUERY_TEXTS = [
    "Redis 的持久化机制 RDB 和 AOF 有什么区别",
    "HTTP 长连接与短连接的取舍",
    "数据库索引为什么用 B+ 树",
    "进程与线程的区别以及各自的适用场景",
    "RAG 检索增强生成的基本流程",
    "消息队列如何保证消息不丢失",
    "TCP 三次握手为什么不能是两次",
    "缓存穿透 缓存击穿 缓存雪崩的区别与对策",
    "微服务拆分的边界怎么划",
    "向量数据库的近似最近邻检索原理",
]


def _unit_vector(rng: random.Random) -> list[float]:
    v = [rng.gauss(0.0, 1.0) for _ in range(DIM)]
    norm = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / norm for x in v]


def _pct(sorted_vals: list[float], p: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


def _build_queries() -> tuple[list[list[float]], str, float | None]:
    """真 embedding 优先; 缺 key 退合成向量。返回 (向量, 来源, embed_p50_ms)。"""
    import os

    if os.environ.get("OPENAI_API_KEY"):
        from src import embeddings

        vecs, lats = [], []
        for text in QUERY_TEXTS:
            t0 = time.monotonic()
            v = embeddings.embed(text)
            lats.append((time.monotonic() - t0) * 1000.0)
            if not embeddings.is_stub_vector(v):
                vecs.append(v)
        if vecs:
            lats.sort()
            return vecs, "real_embedding", _pct(lats, 0.5)
    rng = random.Random(SEED + 1)
    return [_unit_vector(rng) for _ in QUERY_TEXTS], "synthetic", None


def _setup(client, corpus: int) -> float:
    if client.has_collection(COLLECTION):
        client.drop_collection(COLLECTION)
    client.create_collection(COLLECTION, dimension=DIM, metric_type="COSINE")
    rng = random.Random(SEED)
    t0 = time.monotonic()
    batch: list[dict] = []
    for i in range(corpus):
        batch.append({"id": i, "vector": _unit_vector(rng)})
        if len(batch) == 500:
            client.insert(COLLECTION, batch)
            batch = []
    if batch:
        client.insert(COLLECTION, batch)
    return time.monotonic() - t0


def _search_once(client, vec: list[float]) -> float:
    t0 = time.monotonic()
    client.search(COLLECTION, data=[vec], limit=5, consistency_level="Strong")
    return (time.monotonic() - t0) * 1000.0


def _phase_single(client, queries: list[list[float]], n: int = 100) -> dict:
    lats = [_search_once(client, queries[i % len(queries)]) for i in range(n)]
    lats.sort()
    return {"n": n, "p50_ms": round(_pct(lats, 0.5), 2), "p95_ms": round(_pct(lats, 0.95), 2)}


def _phase_concurrent(client, queries: list[list[float]], threads: int, per_thread: int) -> dict:
    lats: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(tid: int) -> None:
        for i in range(per_thread):
            try:
                ms = _search_once(client, queries[(tid + i) % len(queries)])
                with lock:
                    lats.append(ms)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}"[:200])

    t0 = time.monotonic()
    ts = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    wall = time.monotonic() - t0
    lats.sort()
    return {
        "threads": threads,
        "n_ok": len(lats),
        "n_err": len(errors),
        "err_samples": errors[:3],
        "p50_ms": round(_pct(lats, 0.5), 2) if lats else None,
        "p95_ms": round(_pct(lats, 0.95), 2) if lats else None,
        "throughput_qps": round(len(lats) / wall, 1) if wall > 0 else None,
    }


def _phase_read_own_write(client, iterations: int = 10) -> dict:
    """insert 后立即按向量检索刚写入的 id —— Sprint 9 一致性修复的依据复现。"""
    rng = random.Random(SEED + 2)
    out: dict = {}
    for level in ("Strong", "Bounded"):
        hits = 0
        lats: list[float] = []
        for i in range(iterations):
            new_id = 10_000_000 + i + (0 if level == "Strong" else iterations)
            vec = _unit_vector(rng)
            client.insert(COLLECTION, [{"id": new_id, "vector": vec}])
            t0 = time.monotonic()
            res = client.search(COLLECTION, data=[vec], limit=1, consistency_level=level)
            lats.append((time.monotonic() - t0) * 1000.0)
            if res and res[0] and res[0][0]["id"] == new_id:
                hits += 1
        lats.sort()
        out[level] = {"visible": f"{hits}/{iterations}", "p50_ms": round(_pct(lats, 0.5), 2)}
    return out


def _child_loop(uri: str) -> None:
    """--coholder 的子进程体: 共持同一 lite 文件并持续检索, 复现 uvicorn 共存。"""
    from pymilvus import MilvusClient

    try:
        client = MilvusClient(uri=uri)
        rng = random.Random(SEED + 3)
        vec = _unit_vector(rng)
        while True:
            try:
                client.search(COLLECTION, data=[vec], limit=5)
            except Exception as exc:  # noqa: BLE001
                print(f"[child] search error: {type(exc).__name__}: {exc}"[:200], flush=True)
            time.sleep(0.05)
    except Exception as exc:  # noqa: BLE001
        print(f"[child] open failed: {type(exc).__name__}: {exc}"[:300], flush=True)
        sys.exit(2)


def main() -> None:
    ap = argparse.ArgumentParser(description="Milvus 检索层专项压测 (lite vs server)")
    ap.add_argument("--mode", choices=["lite", "server"], required="--child" not in sys.argv)
    ap.add_argument("--lite-uri", default="./milvus_lite_bench.db")
    ap.add_argument("--corpus", type=int, default=CORPUS)
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--per-thread", type=int, default=50)
    ap.add_argument("--coholder", action="store_true", help="lite: 第二进程共持同一文件")
    ap.add_argument("--child", metavar="URI", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.child:
        _child_loop(args.child)
        return

    import os

    from pymilvus import MilvusClient  # 先 import: 触发 load_dotenv 回填 (F9)

    if args.mode == "server":
        uri = os.environ.get("MILVUS_SERVER_URI")
        if not uri:
            raise SystemExit("server 模式需要 MILVUS_SERVER_URI (docker compose up -d milvus)")
    else:
        uri = args.lite_uri

    client = MilvusClient(uri=uri)
    queries, query_source, embed_p50 = _build_queries()

    print(f"==== bench_milvus mode={args.mode} uri={uri} ====")
    print(f"语料 {args.corpus} 条合成向量; 查询来源: {query_source}"
          + (f" (embed p50 {embed_p50:.0f}ms, 独立于 search 计时)" if embed_p50 else ""))
    ingest_s = _setup(client, args.corpus)
    print(f"建库+插入: {ingest_s:.1f}s")

    single = _phase_single(client, queries)
    print(f"单线程 search (n={single['n']}): p50={single['p50_ms']}ms p95={single['p95_ms']}ms")

    child = None
    if args.coholder:
        if args.mode != "lite":
            raise SystemExit("--coholder 仅对 lite 模式有意义")
        child = subprocess.Popen(
            [sys.executable, "-m", "scripts.bench_milvus", "--child", uri],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        time.sleep(3.0)
        print(f"coholder 子进程已共持 lite 文件 (pid={child.pid})")

    conc = _phase_concurrent(client, queries, args.threads, args.per_thread)
    print(f"{conc['threads']} 线程并发 (ok={conc['n_ok']} err={conc['n_err']}): "
          f"p50={conc['p50_ms']}ms p95={conc['p95_ms']}ms 吞吐={conc['throughput_qps']} qps")
    for e in conc["err_samples"]:
        print(f"  ✗ {e}")

    row = _phase_read_own_write(client)
    print(f"read-own-write: Strong visible={row['Strong']['visible']} "
          f"p50={row['Strong']['p50_ms']}ms | Bounded visible={row['Bounded']['visible']} "
          f"p50={row['Bounded']['p50_ms']}ms")

    child_report = None
    if child is not None:
        child.terminate()
        try:
            out, _ = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            out, _ = child.communicate()
        child_lines = [ln for ln in (out or "").splitlines() if ln.strip()]
        child_report = {"exit": child.returncode, "output_head": child_lines[:5]}
        print(f"coholder 子进程: exit={child.returncode}"
              + (f"; 输出: {child_lines[:3]}" if child_lines else "; 无异常输出"))

    client.drop_collection(COLLECTION)
    if args.mode == "lite":
        # scratch 文件清理尽力而为 (占不住就留着, 命中 .gitignore 的 milvus_lite* 规则)
        import contextlib
        import shutil
        from pathlib import Path

        with contextlib.suppress(Exception):
            client.close()
        with contextlib.suppress(Exception):
            Path(args.lite_uri).unlink(missing_ok=True)
        shutil.rmtree(args.lite_uri + ".lock", ignore_errors=True)

    print("SUMMARY " + json.dumps({
        "mode": args.mode, "coholder": bool(args.coholder), "query_source": query_source,
        "single": single, "concurrent": conc, "read_own_write": row,
        "child": child_report,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
