# interview-backend（Java 后端主干）

AI 面试平台的 Java 后端主干,与仓库根目录的 Python 服务对照实现——
**Java 承载业务流程/并发/事务/MQ/检索,Python 保留 LLM Agent + 校准/评测**。

设计文档见根目录:`Spring-Boot-落地方案.md`(架构)+ `Spring-Boot-类设计.md`(类设计)。

## 已实现(S1–S7,全部端到端验证通过)
| 模块 | 内容 | 验证 |
|---|---|---|
| S1 | Spring Boot + MyBatis-Plus + Postgres + **JSONB 往返** | `/smoke/session` history 完整还原 |
| S2 | **Redisson 分布式锁**(看门狗续期)+ Redis 会话热态 | 6 并发提交 → 5×409 + 1×200 |
| S3 | **会话状态机** start/submit/resume/finalize + 追问规则 + 终止策略 + 懒题现场生成 + Evaluator | 单场面试 self_intro→knowledge→project→scenario 11 轮收敛,出报告 |
| S4 | **RocketMQ 异步 ingest**(未配 broker 时同步兜底 + 幂等键) | 上传 50ms 返回,同步建计划 |
| S5 | **Milvus** 检索封装(未配 uri 时降级为空) | 连接/健康检查 wired |
| S6 | **JWT + RBAC + 所有权链路(越权 404)+ 候选人一次性 token** | owner 200 / 非 owner 404 / 无 token 403 |
| S7 | **双路径容错**(LLM 失败退启发式)+ 统一异常映射(409/404/400) | assess 走本地启发式 |

> 外部依赖全部「可选 + 优雅降级」:RocketMQ/Milvus/Python AI 未配置时走本地 stub/同步兜底,
> 只需 **Postgres + Redis** 即可端到端跑完整场面试(对应 Python 侧 `[stub]` 哲学)。

## 依赖
- JDK 21、Maven 3.9+
- Postgres(`brew services start postgresql@16`)、Redis(`brew services start redis`)

## 跑起来
```bash
# 建独立库(勿与 Python 项目共用 interview 库)
createdb interview_java

cd java-backend
export JAVA_HOME=$(/usr/libexec/java_home -v 21)   # 项目锁 JDK 21
export DB_USER=joy DB_PASSWORD=""                  # brew Postgres 默认用户=当前用户,无密码
mvn spring-boot:run
```
连接可用环境变量覆盖:`DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`、`REDIS_HOST/REDIS_PORT`。

## 端到端 demo(curl)
```bash
# 1) HR 注册登录
curl -s -X POST localhost:8080/auth/register -H 'Content-Type: application/json' -d '{"username":"alice","password":"pw123456"}'
HR=$(curl -s -X POST localhost:8080/auth/login -H 'Content-Type: application/json' -d '{"username":"alice","password":"pw123456"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")

# 2) 候选人上传 → 得 candidateId(接口立即返回,ingest 异步/同步兜底)
CID=$(curl -s -X POST localhost:8080/candidate/upload -H 'Content-Type: application/json' -d '{"jobId":"job-x","name":"Bob","resumeText":"后端工程师..."}' | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['candidateId'])")

# 3) 候选人一次性 token
CT=$(curl -s -X POST "localhost:8080/auth/candidate-token?candidateId=$CID" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")

# 4) 面试:start → answer*N → finalize(带 Authorization: Bearer $CT)
curl -s -X POST localhost:8080/interview/start -H "Authorization: Bearer $CT"
#   用返回的 sessionId 反复 POST /interview/{sid}/answer,直到 done=true,再 POST /interview/{sid}/finalize

# 5) HR 查报告(需 job 归属 alice;越权返回 404)
curl -s localhost:8080/hr/reports/{sessionId} -H "Authorization: Bearer $HR"
```

## 测试(JUnit 5 + Testcontainers)
- **23 单元测**(纯规则/JWT/启发式,毫秒级,无容器)+ **12 集成测**(真 PG/Redis 容器):
  分布式锁并发、JSONB 泛型往返、报告唯一约束、内容 hash 消费幂等、所有权链路、端到端面试全流程 + 安全(403/404)+ 并发 409。
- 方案见根目录 `Spring-Boot-测试方案.md`。

```bash
# 只跑单元测(不需 Docker)
mvn test
# 跑单元 + 集成测(需 Docker 起容器)
mvn verify
```

**本机 Docker Desktop 的坑(CI/标准 Docker 不需要)**:Docker Desktop 的默认 socket 是个代理,
且引擎要求 Docker API ≥1.40,而 docker-java 默认协商 1.32。本项目已在 pom 的 failsafe 里 pin
`api.version=1.44`;运行 `mvn verify` 前还需指向真正的引擎 socket:
```bash
export DOCKER_HOST="unix://$HOME/Library/Containers/com.docker.docker/Data/docker.raw.sock"
export TESTCONTAINERS_RYUK_DISABLED=true   # 避开 Desktop 的 socket 挂载限制
mvn verify
```
CI(GitHub Actions,见 `.github/workflows/ci.yml`)用标准 `/var/run/docker.sock`,无需上述设置。

> 测试踩坑记录(体现排障):① `*IT` 属 failsafe(`verify`)不属 surefire(`test`);
> ② `@Component` 的过滤器会被 Spring Boot 同时注册成 servlet 级过滤器(已改为 SecurityConfig 手动 new);
> ③ 共享 TestRestTemplate 会带着 `/auth/login` 下发的 HR cookie 串味到候选人请求(filter cookie 优先 → 403),
> 已用无 cookie store 的 JDK 客户端隔离;④ JDK 客户端默认 Accept 会协商出 XML,测试显式要 JSON。

## 启用真实外部服务(可选)
```bash
# RocketMQ(需先起 broker):
export ROCKETMQ_EXCLUDE= ROCKETMQ_NAMESERVER=localhost:9876 JOBS_QUEUE_ENABLED=true
# Milvus(需先起 standalone):
export MILVUS_URI=http://localhost:19530
# 接 Python AI 服务(真 LLM 出题/打分):
export AI_ENABLED=true AI_BASE_URL=http://localhost:8000
```

## 目录
```
domain/        实体 + json/ 内嵌 JSONB 结构
mapper/        MyBatis-Plus BaseMapper
service/lock/  Redisson 分布式锁          service/cache/ Redis 会话热态
service/orchestrator/ 状态机(SessionService/FollowUpDecider/CompletionPolicy/PlanService/SessionEngine/ArchiveService)
service/ai/    AiClient(Python 出口 + 本地兜底)   service/mq/ RocketMQ ingest   service/vector/ Milvus
security/      JWT/过滤器/所有权校验        controller/ HTTP 入口   exception/ 统一映射
```
