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
  | `automation:queue:pending` | List | 待处理任务队列（FIFO） |
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
uvicorn automation.app.main:app --host 0.0.0.0 --port 8000

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
- **单个 Worker 内的并发数（`WORKER_CONCURRENCY`）：先给 5**。任务本质是
  "调用网关、等 HTTP 返回"，属于 IO 密集型，线程池并发即可，不需要多进程

单副本情况下，如果 Worker 这个 Pod 被杀（比如节点重启），队列会短暂无人
处理，直到 Pod 重新调度起来（不会丢任务，靠租约机制保证，只是会有一段
处理延迟）——已和组长确认这个影响可以接受。

后续如果要做"排队积压自动扩容"，P3 阶段可以上 KEDA（按 Redis 队列长度触发
扩容），目前先用固定单副本，且默认不部署到 EKS（跟契约一致，先在本地
docker-compose 里验证）。

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

Worker 本身不接收业务流量，但仍然起了一个很小的 HTTP server（默认端口
`9000`，`WORKER_HEALTH_PORT`）专门给探针和监控用：

- `GET /health` —— 根据"最近一次循环心跳时间"判断是否存活，超过
  一定时间没心跳就返回 503（供 liveness probe 用）
- `GET /metrics` —— 简单的 Prometheus 文本格式指标：
  `automation_worker_jobs_processed_total`、
  `automation_worker_jobs_failed_total`、
  `automation_worker_jobs_retried_total`

（API 那边的 `/health` 已经存在，不需要动；如果 API 也要接入 Prometheus
`/metrics`，可以参考 Gateway 那边 `prometheus_client` 的现成用法。）

## 需要 C 在 `deploy/` 里补充的内容（未直接改动，供参考；暂不部署 EKS）

- 新增 `automation-worker` Deployment：同镜像、`command` 换成
  `python -m automation.app.worker`，`replicas: 1`（已和组长确认，
  Worker 和 API 都是单副本）
- `terminationGracePeriodSeconds: 60`
- livenessProbe / readinessProbe 指向 `9000` 端口的 `/health`
- Prometheus scrape 配置里加上 `9000` 端口的 `/metrics`
- 环境变量：`AUTOMATION_REDIS_URL`、`GATEWAY_URL`（指向 Gateway 的 Service 地址）
- Automation API 本身：监听 `8020`，健康检查 `GET /health`（已有，不用改）

## 待确认事项（需要和 A 对一下）

`worker.py` 里 `execute_job()` 目前是按"直接把 `JobRecord` 序列化后 POST
给 Gateway"这个假设写的骨架，具体 `JobRecord` 怎么关联到当初 `preview`
阶段编译出来的 `gateway_request`（是 Worker 自己再查一次
`get_preview()`，还是 `JobRecord` 应该直接带上完整的 `gateway_request`
字段），这一点需要和 A 确认后再补完整。
