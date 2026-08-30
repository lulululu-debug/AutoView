#!/usr/bin/env bash
# 压测 Java 后端「同步写路径」/candidate/upload(不含 LLM,stub 计划生成)。
# 用 ApacheBench(ab)。测的是应用层 + DB 写入的延迟分布,不是生产绝对性能。
#
# 前置:应用已启动且关闭限流(否则会打到 429 污染延迟):
#   RATE_LIMIT_ENABLED=false JOBS_QUEUE_ENABLED=false AI_ENABLED=false \
#   DB_USER=joy DB_PASSWORD= DB_NAME=interview_java mvn spring-boot:run
#
# 用法: ./scripts/loadtest.sh [host] [total] [concurrency]
set -euo pipefail

HOST="${1:-http://localhost:8080}"
N="${2:-3000}"
C="${3:-50}"

BODY="$(mktemp)"
printf '{"jobId":"loadtest","name":"bench","resumeText":"后端工程师,做过分布式锁/MQ/幂等/限流,P99从900ms优化到400ms。"}' > "$BODY"

echo "== warmup 200 =="
ab -q -n 200 -c 20 -p "$BODY" -T application/json "$HOST/candidate/upload" >/dev/null 2>&1 || true

echo "== load: N=$N C=$C  POST /candidate/upload =="
ab -n "$N" -c "$C" -p "$BODY" -T application/json "$HOST/candidate/upload" 2>/dev/null \
  | grep -E "Requests per second|Time per request|Failed requests|Percentage|50%|66%|75%|80%|90%|95%|98%|99%|100%"

rm -f "$BODY"
