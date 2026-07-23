# Automation Queue & Worker — 部署要求

> 本次改动新增两个文件：`automation/app/redis_store.py`（Redis 版
> `AutomationStore` 实现）、`automation/app/worker.py`（Worker 主程序）。
> `automation/app/store.py` 里的 `AutomationStore` 协议和 `main.py` 的 API
> 路由**没有改动**，只需要在应用启动时把 `InMemoryAutomationStore()` 换成
> `RedisAutomationStore(redis_client)` 即可无缝切换。
> 未直接改动 `deploy/`，具体 manifests 请 C 按下面的要求补充。

## 1. Redis 使用的 URL/DB 或 key prefix

复用 Provider 层已经在用的同一个 Redis 实例（`docker-compose.yml` 里的
`redis` 服务 / K8s 里对应的 Redis 部署），**不单独起一个 Redis**。

- 连接方式：环境变量 **`AUTOMATION_REDIS_URL`**（对齐 C1 已冻结的契约，
  和 Gateway 那边用的 `REDIS_URL` 是两个独立的变量名，不要混用），
  默认 `redis://redis:6379/0`
- 隔离方式：**用 key 前缀而不是单独的 DB 号**（`automation:` 前缀），
  原因是很多托管 Redis（比如 Redis Cluster）不支持多 DB，前缀方式更方便
  以后迁移
- 具体 key 设计：

  | Key | 类型 | 用途 |
  |---|---|---|
  | `automation:preview:{preview_id}` | String (JSON) | 预览结果，TTL = `expires_in_seconds` |
  | `automation:job:{job_id}` | String (JSON) | 任务当前状态 |
  | `automation:idempotency:{key}` | String | 幂等键 → job_id，TTL 24 小时 |
  | `automation:queue:pending` | Set | 待处理任务（按 effective_priority 挑选，不是 FIFO List） |
  | `automation:queue:leases` | Sorted Set | 租约，score = 到期时间戳 |
  | `automation:jobs:index` | Sorted Set | 按创建时间排序的任务索引，供 `list_jobs()` 使用 |

## 2. Worker 是独立进程还是和 Automation API 同一进程

**独立进程。** 和 API 完全分开跑。原因：

- API 是"请求-响应"式的短连接，Worker 是长期占用 Redis 连接、持续轮询的
  常驻循环，两者的资源特征（长连接 vs 短连接、CPU/IO 模式）不一样，混在
  一个进程里不好独立扩缩容，也不方便分别做健康检查
- 用**同一个 Docker 镜像，不同启动命令**区分（这也是组长提的建议，已确认采用）

## 3. Worker 的 Docker 启动命令

```
# API（原有，不变）
uvicorn automation.app.main:app --host 0.0.0.0 --port 8020

# Worker（新增）
python -m automation.app.worker
```

Dockerfile 本身不需要区分两个版本，`CMD` 在 K8s manifest 里通过
`command`/`args` 覆盖即可。

## 4. Worker 是否需要独立 Deployment

**需要，单独一个 Deployment**（比如叫 `automation-worker`），不需要配
Service（Worker 不接收入站的业务流量），但需要暴露一个端口给 K8s 探针和
Prometheus 抓取（见第 8 点）。

按 C1 契约，**当前阶段默认不部署到 EKS**，先在本地 docker-compose 里把
API + Worker + Redis 跑通验证，K8s manifest 先准备着，等确认没问题、
组长决定要上 EKS 时再由 C 接入。

## 5. 推荐副本数和并发数

**已按 C1 冻结契约确定为单副本，Worker 和 API 都保持一致（已和组长确认）。**

- **副本数（replicas）：1**（Worker 和 API 都是单副本）
- **单个 Worker 内的并发数（`WORKER_CONCURRENCY`）：先给 1**（团队 review
  后调整）。之前建议过 5，但 review 时发现 `claim_next_job()` 里
  "挑选任务再移出队列"这两步不是完全原子的（已修复 `srem` 返回值检查这
  一处竞态，见下方"已修复的问题"），在多副本/高并发场景下理论上还可能有
  极端 race。在进一步验证并发安全性之前，先收紧到 1，确保正确性优先于
  吞吐量；后续验证充分后可以调大。

单副本 + concurrency=1 情况下，如果 Worker 这个 Pod 被杀（比如节点重启），
队列会短暂无人处理，直到 Pod 重新调度起来（不会丢任务，靠租约机制保证，
只是会有一段处理延迟）——已和组长确认这个影响可以接受。

后续如果要做"排队积压自动扩容"，P3 阶段可以上 KEDA（按 Redis 队列长度触发
扩容），目前先用固定单副本，且默认不部署到 EKS（跟契约一致，先在本地
docker-compose 里验证）。

K8s 部署策略建议：`replicas: 1` + `strategy: Recreate`（而不是
RollingUpdate），避免滚动更新期间短暂出现两个 Worker 同时跑（这一点由 C
在写 Deployment manifest 时落实）。

## 6. Job 状态转换：queued → running → completed/failed

状态定义已经在 `models.py` 里写好了（`JobState` 枚举），不需要新增，
只是之前没有任何代码真正驱动这个转换。现在的驱动方是 Worker：

```
queued  --[Worker claim_next_job()]-->  running  --[执行成功]--> completed
                                            |
                                            +------[执行失败，重试次数用完]--> failed
                                            |
                                            +------[执行失败，还有重试次数]--> 重新回到 queued
```

对应字段：`started_at`（claim 时写入）、`completed_at`（成功或最终失败时
写入）、`result`（成功时写入）、`error`（失败时写入）——这些字段
`JobRecord` 里已经预留好了。

## 7. 幂等、租约、超时和重试规则

- **幂等**：入队阶段，同一个 `Idempotency-Key` 只会真正入队一次（用
  Redis `SET ... NX` 保证并发请求下也不会重复），重复提交直接返回原来那个
  `JobRecord`
- **租约（lease）**：Worker 领取任务时设置 60 秒（`WORKER_LEASE_SECONDS`）
  的租约。如果 Worker 在这段时间内崩溃/被杀，没人 renew 或 complete，
  租约到期后会被别的 Worker 的 `reap_expired_leases()` 发现并重新入队
- **超时**：单次任务执行硬性超时 30 秒（`WORKER_JOB_TIMEOUT_SECONDS`），
  超时按失败处理，走重试规则
- **重试**：最多重试 3 次（`WORKER_MAX_RETRIES`），用完次数后标记为最终
  `failed` 并写入 `error` 字段。当前重试计数存在 Worker 进程内存里，
  **有一个已知限制**：如果 Worker 本身重启，重试计数会归零——如果这一点
  重要，可以后续把重试计数也挪进 Redis（`automation:job:{id}:retries`），
  这个可以放到下一轮迭代

## 8. Worker 如何优雅停止，避免 Pod 终止时丢失正在执行的 Job

监听 `SIGTERM`（K8s 终止 Pod 时会先发这个信号，再等
`terminationGracePeriodSeconds` 后才强制 kill）：

1. 收到 `SIGTERM`，设置一个停止标志，**不再领取新任务**
2. 等待当前正在执行的任务，在宽限期（`WORKER_SHUTDOWN_GRACE_SECONDS`，
   默认 45 秒）内做完
3. 宽限期内没做完的任务，不强行等待——它的租约会在正常时间后自然过期，
   被别的 Worker 的 `reap_expired_leases()` 接手重试，不会丢失
4. 建议 K8s manifest 里把 `terminationGracePeriodSeconds` 设置成比
   `WORKER_SHUTDOWN_GRACE_SECONDS` 稍大一点（比如 60 秒），确保 K8s 不会
   在 Worker 自己走完优雅关闭流程之前就强制 kill 掉

## 9. 健康检查和 Metrics 路径

**Automation API**（监听 `8020`）：
- `GET /health` —— liveness，只确认进程存活，不检查 Redis
- `GET /ready` —— readiness（新增），真正 ping 一下 Redis，连不上返回
  503，K8s 会暂时把这个 Pod 从 Service 后端摘掉，不再转发流量过去

**Worker**（固定端口 `9000`）：

- `GET /health` —— 根据"最近一次循环心跳时间"判断是否存活，超过
  一定时间没心跳就返回 503（供 liveness probe 用）
- `GET /metrics` —— 用和 Gateway 那边一致的 `prometheus_client` 库输出
  标准格式，指标如下：
  - `automation_worker_jobs_processed_total`（Counter）—— 成功完成的任务数
  - `automation_worker_jobs_failed_total`（Counter）—— 最终失败（重试用完）的任务数
  - `automation_worker_jobs_retried_total`（Counter）—— 触发重试的次数
  - `automation_worker_job_duration_seconds`（Histogram）—— 单次任务执行耗时
  - `automation_worker_queue_depth`（Gauge，新增）—— 当前排队中（还没被
    任何 Worker 领取）的任务数
  - `automation_worker_in_flight`（Gauge，新增）—— 当前正在被执行（持有
    租约）的任务数
  - `automation_worker_queue_wait_seconds`（Histogram，新增）—— 任务从
    创建到被领取，实际排队等待了多久

## 10. 本地测试命令

```bash
# 1. 重新构建（requirements.txt 加了 redis 包）
docker compose build automation --no-cache
docker compose up -d

# 2. 确认 automation API 正常
curl http://localhost:8020/health

# 3. 跑一次四任务高峰场景（见 scripts/automation-peak-test.sh）
./scripts/automation-peak-test.sh
```

`scripts/automation-peak-test.sh` 会连续提交 4 个任务（模拟高峰涌入），
然后轮询每个任务的状态，打印出从 `queued -> running -> completed/failed`
的变化，方便本地肉眼确认 Worker 真的在处理排队的任务。

## 11. 本次 follow-up 修复的问题（针对 commit 14afb30 之后的 review 反馈）

1. **`enqueue()` 多步 Redis 写入不原子**：之前分四次独立请求写
   job/job-payload/队列/索引，中途崩溃可能导致数据不一致。现在用
   `redis.pipeline()` 把这几步打包成一次 MULTI/EXEC 原子操作。
2. **旧 idempotency key 可能指向不存在的 Job**：如果上一次入队写到一半
   崩溃，幂等键会一直指向一个丢失的 job_id。现在的自愈逻辑是：发现指向
   的 job 不存在时，重新生成 job_id 并**覆盖**（不用 NX）idempotency
   key，让后续重复提交收敛到新建的 job。
3. **`claim_next_job()` 的竞态**：选中任务后，现在会检查 `srem` 的返回值，
   确认自己真的独占了这次移除操作，返回 0（被别人先抢走）就放弃这一轮，
   避免未来 replicas > 1 时同一个任务被两个 Worker 同时执行。
4. **Redis 不可用时不再静默退回内存版本**：`main.py` 的
   `_build_default_store()` 现在在 `AUTOMATION_REDIS_URL` 未配置时直接
   抛错，而不是悄悄用 `InMemoryAutomationStore`；`InMemoryAutomationStore`
   只应该在单元测试里被显式传入使用。
5. **新增 `GET /ready`**：真正 ping Redis，连不上返回 503，配合 K8s
   readiness probe 使用；`GET /health` 保持只做存活检查（liveness），
   不检查 Redis，避免 Redis 抖动时把 Pod 误杀。

## 12. 自动化测试

新增 `automation/app/tests/test_redis_store_and_worker.py`，覆盖：

- 幂等（同 key 重复提交只入队一次；不同 key 各自入队）
- 优先级排序（`effective_priority` 更高的先被 claim）
- 防饥饿（连续 3 个 critical/high 之后，强制插队等待够久的 low 任务，
  执行完 streak 清零）
- 内部执行快照（`job-payload`）正确保存，任务完成后被清理
- Worker 成功执行、暂时性失败重试直到最终 failed
- Bearer Header 只在 `POLYGATE_API_KEY` 配置时才携带

运行前提：本地/CI 有可访问的 Redis（默认用 DB 15，测试前后自动
`FLUSHDB`，不影响 DB 0 的真实数据）。

```bash
AUTOMATION_TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
  python -m pytest automation/app/tests/test_redis_store_and_worker.py -v
```

## 队列优先级 / aging / fairness（已按 A 确认的方案实现）

`JobRecord.priority`（`PriorityDecision`）同时有档位（`class_`：
critical/high/normal/low）和数值分数（`initial_score`）。排队规则：

```
effective_priority = initial_score + waiting_bonus
waiting_bonus：每等待 5 秒 +1 分，最多 +30 分（Worker 侧计算，不是 A 那边给的）
```

- Worker 每次挑选任务（大约每 1 秒一次"准入窗口"）时，从当前所有排队中的
  任务里，选 `effective_priority` 最大的执行
- 同分按创建时间 FIFO（更早创建的优先）
- 正在 Running 的任务不会被抢占
- **防饥饿机制**：连续选中 3 个 critical/high 之后，如果有 normal/low
  任务已经等了至少 20 秒，强制执行其中等待最久的那个（忽略
  effective_priority 排名）；执行完这个低优先级任务后，streak 清零

已在 `redis_store.py` 的 `claim_next_job()` 里实现，待处理队列从 Redis
List 改成了 Set（因为现在是按优先级挑选，不是单纯先进先出，顺序不重要）。

字段名已对照 `automation/app/models.py` 核实无误：
`PriorityDecision.class_`（类型 `Urgency`，序列化别名 `class`）和
`PriorityDecision.initial_score`（`int`），和代码里的假设完全一致，不需要
再改。

## Worker 调用 Gateway 的认证（环境变量名已最终确认：`POLYGATE_API_KEY`）

Gateway 那边的认证是 D 在 streaming 改动里加的（`gateway/app/auth.py`），
opt-in 设计：`POLYGATE_API_KEYS`（复数，逗号分隔的一组 key）留空则不校验，
配置了才强制校验，格式是标准 `Authorization: Bearer <key>`。

Worker 这边同样做成 opt-in：环境变量 `POLYGATE_API_KEY`（单数，Worker 只需要
提供其中一个合法 key）有值就带上 `Authorization: Bearer` 头，没配就跟以前
一样裸调——这样不管部署环境最终要不要开启 Gateway 那层认证，Worker 都能
直接适配。

命名说明：这个变量名中间经过几轮反复讨论（`GATEWAY_API_KEY` ⇄
`POLYGATE_API_KEY` 来回改过），**最终团队确认的版本是 `POLYGATE_API_KEY`**，
和 Gateway 的 `POLYGATE_API_KEYS` 命名对齐。代码和文档已同步保持这个
最终版本，后续不再变更。

安全要求：这个 key 只用来构造请求头，**不会被写入 JobRecord、不会存进
Redis、也不会出现在任何日志输出里**——`execute_job()` 里日志只打印
`job_id`，不打印 headers 或 payload 内容。

错误处理：如果 Gateway 返回 401/403（说明 `POLYGATE_API_KEY` 不对，或者
根本没配但 Gateway 那边要求校验），Worker **直接判定为最终失败，不会
浪费重试次数**——因为同一个错误的 key，重试多少次结果都一样，属于
"永久性"错误，跟网络超时、5xx 这类"重试一下可能就好了"的暂时性错误
是两码事，需要区别对待。

## 需要 C 在 `deploy/` 里补充的内容（未直接改动，供参考；暂不部署 EKS）

- 新增 `automation-worker` Deployment：同镜像、`command` 换成
  `python -m automation.app.worker`，`replicas: 1`（已和组长确认，
  Worker 和 API 都是单副本）
- `terminationGracePeriodSeconds: 60`
- livenessProbe / readinessProbe 指向 `9000` 端口的 `/health`
- Prometheus scrape 配置里加上 `9000` 端口的 `/metrics`
- 环境变量：`AUTOMATION_REDIS_URL`、`GATEWAY_URL`（指向 Gateway 的 Service 地址）
- Automation API 本身：监听 `8020`，健康检查 `GET /health`（已有，不用改）

## Worker 怎么拿到要执行的完整请求内容（已确认，采用 C 的方案）

之前草案里"要不要给 `JobRecord` 新增 `gateway_request` 字段"这个契约变更，
最终**没有采用**，改成了 C 提出的方案：

- 入队时（`enqueue()`），把完整的 `gateway_request`（含用户 prompt）
  单独存进 Redis 一个 Worker 专用的 key：`automation:job-payload:{job_id}`，
  这份内容是不可变的快照，写一次就不再改
- Worker 执行任务时，直接读这个 key 拿到要发的请求内容，不经过
  `PreviewResponse`（不会有过期问题），也不经过公开的 `JobRecord`
- 任务成功或最终失败后，主动删除这个 key，减少 prompt 在 Redis 里停留的
  时间
- `GET /v1/jobs`、`GET /v1/jobs/{id}` 这些对外接口读的是 `JobRecord`，
  天然不会包含这份内容，不会泄露 prompt

**好处**：不需要改动公开的 `JobRecord` 结构，D 那边不用跟着改任何解析
代码，这个方案已经在代码里实现（`redis_store.py` 的
`get_execution_payload()` / `_clear_execution_payload()`，
`worker.py` 的 `execute_job()`）。