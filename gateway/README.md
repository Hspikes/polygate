# gateway/  —— 成员 A（网关与策略）

## 你的验收物（来自项目书）
> 同一请求能解释为何选择某 Provider —— 决策卡片里的 `reason` 必须是人话。

## 本地开发（不依赖 B 的 Mock）
```bash
cd gateway
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PROVIDERS_FILE=../contracts/providers.yaml
FAKE_ADAPTER=1 uvicorn app.main:app --reload   # 用假 Adapter，返回罐头数据
```
访问 http://localhost:8000/providers 看注册表；POST /v1/chat/completions 测路由。

## Gateway 指标

Gateway 在 `GET /metrics` 暴露 Prometheus 文本格式的运行指标。它记录：

- 每个聊天请求的结果与端到端耗时，包括 400/403/422 客户端错误
- 缓存命中/未命中
- 各 Provider 的调用结果与耗时
- Provider 重试原因、自动 failover 和熔断状态
- 流式请求最终结果与请求预算耗尽阶段
- 成功调用消耗的输入/输出 token
- 成功调用的估算费用

本地启动 Gateway 后可直接检查：

```bash
curl http://localhost:8000/metrics
```

每个 `POST /v1/chat/completions` 只计数一次。请求结果包括
`success`、`cache_hit`、`client_error`、`routing_error`、
`provider_error`、`provider_timeout`、`cancelled`、`partial_error` 和未预期的
`server_error`。
Provider 调用额外区分 `success`、`error` 和客户端主动触发的 `cancelled`，
避免取消请求降低 Provider 成功率。`polygate_circuit_state{provider,state}`
以 `closed`、`open`、`half_open` one-hot Gauge 暴露熔断状态。重试原因使用
`timeout`、`429`、`5xx`、`transport`、`other` 固定枚举。指标标签只包含 Provider
和这些有限的结果类型；`request_id`、prompt、路由原因和原始错误文本
继续写入日志，不进入指标标签。

## 接入 B 的 Mock（第 3 天集成）
去掉 `FAKE_ADAPTER=1`，用 `docker compose up` 起全套即可。

## Pi Agent 可运行闭环

Gateway v0.3 支持 Pi 使用的 OpenAI Chat Completions 子集：content blocks、
`developer/tool` 消息、`tools/tool_choice`、增量 tool call、SSE usage 和
`data: [DONE]`。工具仍由 Pi 在本地执行，Gateway 不获得工作区权限。

本地启动 Mock、Gateway 后执行：

```bash
git submodule update --init --recursive
docker compose up --build redis mock-a mock-b gateway

PI_BIN="$(command -v pi)" \
POLYGATE_BASE_URL=http://127.0.0.1:8000/v1 \
POLYGATE_API_KEY=local-development \
./scripts/pi-gateway-smoke-test.sh
```

测试会完成两次模型调用：第一次从 Mock 流式收到 `read` tool call，Pi
读取扩展中的固定 fixture，第二次把 tool result 发回 Gateway 并收到
`mock ok`。测试不调用真实模型，也不会修改工作区文件。

设置 `POLYGATE_API_KEYS` 后，`/v1/models` 和 `/v1/chat/completions` 要求
Bearer Key；变量为空时保留原有本地 Web 开发兼容性：

```bash
POLYGATE_API_KEYS=local-development docker compose up --build gateway
```

所有 `stream=true`、tools、tool history、content-block、消息元数据、会话 ID
和显式生成参数请求默认绕过 P0 精确缓存，避免不同语义的请求复用旧答案。
流式请求只允许在首个下游 SSE event 之前重试或切换 Provider，输出开始后
发生错误会终止流，不拼接其他 Provider 的输出。Gateway 会为内部计费请求
usage，但只在客户端设置 `stream_options.include_usage=true` 时向下游透传
usage-only chunk。

### Agent 调用可靠性

- 自动路由请求共享一个总时间预算，重试和 Provider failover 都不能越过它；
  非流式默认 30 秒，流式默认在 30 秒内拿到首个有效 SSE event。
- 单次非流式调用默认超时 10 秒；流式连接建立后默认允许 90 秒的上游空闲间隔。
- 暂时性网络错误、408/409/429 和 5xx 默认最多重试四次，使用指数退避和 jitter；
  `Retry-After` 会优先于本地退避，但不能突破总时间预算。
- 只有 `model=auto` 会在首字节前 failover。显式指定 Provider 时会坚持该
  Provider，避免 Agent 在不知情的情况下改变模型或执行位置。
- 每个 Chat 响应（包括鉴权、校验、Provider 错误和 504）都返回
  `X-PolyGate-Request-ID`。预算耗尽返回稳定的 `provider_timeout` 错误码。

服务端可通过以下环境变量调整策略；非法或非有限值会让进程启动失败：

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| `PROVIDER_TIMEOUT_SECONDS` | `10` | 单次非流式 Provider 调用上限 |
| `PROVIDER_STREAM_IDLE_TIMEOUT_SECONDS` | `90` | 流式上游空闲超时 |
| `GATEWAY_NON_STREAM_BUDGET_SECONDS` | `30` | 非流式重试与 failover 总预算 |
| `GATEWAY_STREAM_START_BUDGET_SECONDS` | `30` | 流式首个有效 event 总预算 |
| `PROVIDER_MAX_RETRIES` | `4` | 每个 Provider 的最大重试次数，范围 0–10 |
| `PROVIDER_RETRY_BASE_DELAY_SECONDS` | `0.5` | 指数退避基准时间 |
| `PROVIDER_RETRY_MAX_BACKOFF_SECONDS` | `5` | 本地退避上限，不截短 `Retry-After` |
| `DECISION_RECORD_TTL_SECONDS` | `3600` | Redis 中脱敏 Decision Record 的保留秒数（60–86400） |

### Decision Record

每个成功的非流式请求、缓存命中和已经开始下游输出的流式请求，都会尽力把最终结果写入
Redis。客户端从 Chat 响应的 `X-PolyGate-Request-ID` 读取主键，再查询：

```bash
curl -H "Authorization: Bearer $POLYGATE_API_KEY" \
  http://localhost:8000/v1/decisions/req_0123456789abcdef0123456789abcdef
```

`GET /v1/decisions/{request_id}` 与 `/v1/chat/completions` 使用同一套客户端 Bearer
鉴权。返回契约见 `contracts/decision-record.schema.json`。记录仅包含 Provider、路由
原因、outcome、成本、tokens、重试、failover 和时间戳；不会保存 prompt、消息、工具
参数、凭证、上游 URL 或原始错误。默认 TTL 为 1 小时，过期返回 404，Redis 当前不可用
返回 503。记录写入是 best-effort，Redis 故障不会把已经完成的聊天降级为 5xx。

远程 Pi 通过 Web/Nginx 公网入口使用 `https://<host>/v1`；该位置关闭
响应/请求缓冲并保留 Authorization。生产环境必须配置非默认客户端 Key。

## 你负责的文件
- `app/main.py`     入口、缓存查、组装决策卡片、request_id（**已种下，别删**）
- `app/router.py`   ⭐ 路由核心，P0 的规则都在这，扩展策略只改这里
- `app/adapters.py` Adapter 归一化（真实 API 的鉴权细节和 B 一起弄）
- `app/cache.py`    精确/规范化缓存（normalize 规则是和 B 的共享契约）
- `app/cost.py`     成本估算
- `app/registry.py` 读 providers.yaml

## P0 完成判据
- `/v1/chat/completions` 端到端返回「答案 + 决策卡片」
- 卡片包含 chosen_provider / reason / cache_hit / cost / latency / tokens / request_id
- 改约束（quality/privacy/max_cost）能看到路由结果和 reason 相应变化

## P1 可靠性状态

实时健康探测、重试、熔断、首字节前 failover、请求预算和 Redis 重连均已接入
主链路。跨副本共享熔断状态仍未实现；当前每个 Gateway Pod 独立维护熔断器。
