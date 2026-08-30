# 压测结果（Java 后端）

## 口径（诚实交代，避免被追问穿）
- **被测接口**：`POST /candidate/upload`（同步写路径：插入候选人 + stub 计划生成 + JSONB 写入）。
- **不含 LLM**：`AI_ENABLED=false`（stub 出题），`JOBS_QUEUE_ENABLED=false`（ingest 同步跑）。
- **关限流**：`RATE_LIMIT_ENABLED=false`，避免 429 污染延迟。
- **环境**：本机 macOS（Apple Silicon）+ 本地 Postgres/Redis；工具 ApacheBench。
- **负载**：N=3000，并发 C=50，前置 200 次预热。
- ⚠️ 这是「同一台机器上应用层 + DB 写入」的相对延迟，**不是生产绝对性能**。

## 结果（2026-08，示例一次）
| 指标 | 值 |
|---|---|
| 吞吐 | **1964 req/s** |
| p50 | **24 ms** |
| p90 / p95 | 31 / 35 ms |
| p99 | **64 ms** |
| 失败 | **0 / 3000** |

## 复现
```bash
# 起应用（关限流、stub AI、同步 ingest）
RATE_LIMIT_ENABLED=false JOBS_QUEUE_ENABLED=false AI_ENABLED=false \
DB_USER=joy DB_PASSWORD= DB_NAME=interview_java mvn spring-boot:run
# 另一个终端
./scripts/loadtest.sh http://localhost:8080 3000 50
```

## 结论（简历可用）
同步写路径（不含 LLM）在本机 50 并发下 **p50≈24ms、p99≈64ms、~1960 req/s、零失败**，
支撑「上传接口毫秒级返回」的说法;LLM 相关的长耗时已被 RocketMQ 异步搬离该请求路径。
