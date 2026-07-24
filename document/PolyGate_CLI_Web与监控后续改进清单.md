# PolyGate CLI、Web 与监控后续改进清单

> 更新时间：2026-07-23
> 用途：作为后续开发模型的直接任务入口，覆盖当前负责人维护的 Pi CLI、Web
> 客户端和监控后端。本文优先记录可落地缺口、依赖关系和验收标准，不重复完整项目
> 架构说明。

## 1. 范围与当前基线

本文涉及三个端口：

| 端口 | 主要目录 | 当前基线 |
|---|---|---|
| Pi CLI | `.pi/extensions/polygate-routing/` | 已支持 `/login polygate`、`polygate/auto`、`/route`、SSE、工具循环和会话级路由偏好 |
| Web | `web/` | 已支持多轮会话、本地持久化、路由偏好、取消/重试、决策卡和离线演示，但模型请求仍是非流式 JSON |
| 监控后端 | `monitoring/` | 已有 Prometheus、Grafana、Monitoring API、Kubernetes Pod/HPA 面板和本地冒烟测试 |

已验证的测试基线：

- Pi 扩展纯契约测试 11 项通过；Pi → Gateway → Mock → 本地 `read` 工具闭环通过。
- Web Vitest 31 项通过，已有多轮、取消、重试、本地存储和 Playwright 布局测试。
- Monitoring API 有固定 PromQL、稳定 JSON 契约、Prometheus/Grafana 冒烟测试。
- Gateway 的 Agent 可靠性分支 `codex/agent-reliability` 已增加请求预算、
  `provider_timeout`、`Retry-After`、504 和 `X-PolyGate-Request-ID`。后续工作应在该
  分支合并后对齐这些行为，避免重复实现。

## 2. 已确认的关键问题

### 2.1 Web 开启 Gateway 鉴权后无法聊天

当前 Web 请求 `POST /api/v1/chat/completions` 时只发送 `Content-Type`，Nginx 的
`/api/` 代理也没有注入内部凭证。当 Gateway 设置 `POLYGATE_API_KEYS` 后，实际运行栈
返回 `401 invalid or missing PolyGate API key`，但 `/api/health` 仍为 200，因此页面会
显示“网关在线”，发送消息后才失败。

相关文件：

- `web/src/api/gateway-client.ts`
- `web/nginx/default.conf`
- `web/Dockerfile`
- `docker-compose.yml`
- `deploy/web.yaml`

### 2.2 Web 尚未消费新的可靠性错误与追踪信息

当前错误分类只识别 403、422、429、502 和 503：

- 401 被显示为未知错误；
- 504 `provider_timeout` 被显示为未知错误；
- Gateway 校验错误使用 `error.message`，Web 只读取顶层 `detail`；
- 错误响应的 `X-PolyGate-Request-ID` 没有保存或展示，用户无法把故障交给管理员定位。

### 2.3 监控错误率口径会漏报并混淆责任

Monitoring API 和 Grafana 当前使用 `outcome=~".*_error"`：

- 会漏掉 `provider_timeout`；
- 会把 `client_error` 计入服务可用性错误率；
- `cancelled` 没有独立展示；
- Provider 成功率需要确认客户端取消是否被错误记成 Provider 失败。

服务可用性 SLI 应只统计服务端和上游失败；客户端非法请求与主动取消应单独展示。

### 2.4 Monitoring API 查询存在串行放大

一次 Overview 当前顺序执行约 11 个 Prometheus 即时查询，每个查询超时默认 3 秒。
Prometheus 局部变慢时，接口总等待时间可能远大于 3 秒，并且每次请求都新建 HTTP
Client。需要整体截止时间、连接复用和查询收敛。

### 2.5 CLI 只能看到路由偏好，看不到实际执行结果

CLI Widget 展示的是请求前的质量、隐私、成本和延迟偏好，不能展示本轮实际 Provider、
重试、failover、成本或 request ID。`/login` 的 `/v1/models` 验证也没有独立的连接超时，
网络黑洞时可能等待过久。

## 3. 推荐任务与验收标准

### P0-A：打通 Web 服务端鉴权

目标：Gateway 开启客户端 Key 后，Web 和 CLI 均能正常使用，同时不把 Web Key 暴露给
浏览器。

推荐方案：

1. 为 Web 代理创建独立的 `WEB_GATEWAY_API_KEY`，不要复用个人 CLI Key。
2. 由 Nginx `/api/` 在服务端固定注入 `Authorization: Bearer ...`。
3. Key 通过 Compose 环境变量和 Kubernetes Secret 注入，不能写入 Vite 构建产物、
   JavaScript、localStorage、仓库或日志。
4. `/v1/` 继续原样转发客户端 Authorization，供 Pi 和其他 OpenAI 客户端使用。
5. 明确覆盖浏览器传入的 `/api/` Authorization，避免用户伪造内部身份。

验收：

- `POLYGATE_API_KEYS` 非空时，Web 能完成两轮聊天。
- 未带 Key 的公网 `/v1/models` 和 `/v1/chat/completions` 返回 401。
- 正确 CLI Key 可以访问 `/v1/models` 并完成 Pi 工具循环。
- 浏览器网络面板、静态 JS 和 localStorage 中都找不到 `WEB_GATEWAY_API_KEY`。
- Web 健康状态不能出现“在线但必然 401”的假阳性；至少增加一次鉴权就绪检查。

### P0-B：统一 Web 错误与 request ID 契约

目标：用户能区分认证、校验、预算、路由、Provider 和超时故障，并能提供 request ID。

建议行为：

| HTTP/场景 | Web 类型 | 用户提示 |
|---|---|---|
| 401 | `auth` | Web 凭证未配置或已失效 |
| 403/422 | `validation` | 显示 `detail` 或 `error.message` |
| 429 | `budget` 或 `rate_limit` | 请求受预算或限流约束 |
| 502 | `provider` | 上游模型调用失败 |
| 503 | `routing` | 没有满足约束的可用路由 |
| 504 | `timeout` | Provider 调用或流式启动超过时间预算 |
| 网络异常 | `network` | 无法连接 Gateway |

实现要求：

- 错误解析兼容 `{detail: string}` 和 `{error: {message, code, details}}`。
- 从所有响应读取 `X-PolyGate-Request-ID`，保存到 `MessageError` 或决策卡。
- 错误卡展示可复制的 request ID，但不要把 request ID 放入 Prometheus 标签。
- 为 401、422 嵌套错误、504、网络错误和 request ID 增加 Vitest。

验收：每种错误都有稳定中文提示、正确的重试入口和测试覆盖。

### P0-C：修正监控 SLI 与 Dashboard

目标：错误率真实反映服务可靠性，不漏掉超时，也不把用户输入错误当成服务故障。

建议拆分：

- 服务错误率：`routing_error|provider_error|provider_timeout|server_error|partial_error`。
- 客户端拒绝率：`client_error`。
- 客户端取消率：`cancelled`。
- 缓存命中继续作为成功结果单独展示。

同时修改：

- `monitoring/api/app/service.py`
- `monitoring/grafana/dashboards/polygate-overview.json`
- Monitoring API 测试与 `contracts/monitoring-overview.*`
- `monitoring/README.md` 中的指标定义

验收：注入一次 504 后，服务错误率和 outcome 面板能看到 `provider_timeout`；注入 422
只增加客户端拒绝率，不降低服务可用性；取消流只增加取消率。

### P1-A：增加可靠性指标和告警

推荐增加低基数指标：

```text
polygate_provider_retries_total{provider,reason}
polygate_failovers_total{from_provider,to_provider}
polygate_circuit_state{provider,state}
polygate_streams_total{outcome}
polygate_request_budget_exhausted_total{phase}
```

`reason` 必须使用有限枚举，如 `timeout`、`429`、`5xx`、`transport`，禁止放原始错误、
prompt、request ID 或 URL。

最小告警集：

- `GatewayTargetDown`
- `HighProviderErrorOrTimeoutRate`
- `GatewayP95LatencyAboveSLO`
- `ProviderCircuitOpenTooLong`
- `GatewayPodRestarting`
- `GatewayHPAAtMaxReplicas`（云端可选）

课程演示至少要能通过 Mock 故障注入触发并恢复一条告警。

### P1-B：加固 Monitoring API 查询路径

实施顺序：

1. 复用 `httpx.Client` 或改为生命周期管理的 `AsyncClient`。
2. 给整次 Overview 设置总截止时间，不允许 11 个查询各自串行消耗完整超时。
3. 并发执行仍然需要的固定查询，并限制最大并发数。
4. 将常用聚合迁移为 Prometheus recording rules，减少 API 查询数量。
5. 保持“Prometheus 不可查询时返回 502；无流量时返回 null + warning”的现有契约。

验收：单个 Prometheus 查询卡住时，Overview 在整体预算内失败；连接被复用；无流量、
NaN、目标 DOWN 和部分 Provider 无数据的现有测试继续通过。

### P1-C：Web 流式回答与 Decision Record

这是三个端口最值得共用的一项中期能力。

推荐架构：

```text
Web / Pi -- stream=true --> Gateway -- SSE --> Provider
    |                         |
    |                         +--> Redis Decision Record（短 TTL）
    +-- GET /v1/decisions/{request_id} --> 最终成本、tokens、重试、failover
```

不要向标准 OpenAI SSE 中插入自定义 `polygate` event。Gateway 已通过响应 Header 返回
request ID 和初始 Provider；最终 Decision Record 应使用独立查询接口获取。

Web 实现要点：

- 增量解析标准 SSE content delta 和 `[DONE]`；
- 收到每个 delta 时更新当前 assistant message；
- 用户取消必须真正关闭 fetch，并让 Gateway 取消上游流；
- 流结束后按 request ID 获取最终 Decision Record；
- localStorage 写入需要节流或只在稳定状态持久化，不能每个 token 都同步写一次；
- 首字节前 504 可以重试，首字节后断流必须显示“回答不完整”，不能伪装成成功；
- 补 SSE 分帧、UTF-8 跨 chunk、tool-call chunk（若 Web 后续支持工具）、取消和截断流测试。

### P1-D：CLI 可观测性和兼容性加固

建议：

1. `/login polygate` 验证增加约 5 秒超时，并与交互取消信号组合。
2. 增加 `/route-last` 或临时 Widget，展示实际 Provider、成本、重试、failover 和
   request ID；数据来自 Decision Record，不进入模型 prompt。
3. Pi 包的 peer dependency 不再永久使用 `*`。定义受支持范围，或建立当前版本与下一
   个 Pi 版本的 CI 矩阵。
4. 保留当前安全边界：工具只在 Pi 本地执行，Gateway 不获取工作区权限。
5. 静态虚拟模型成本当前为 0；若 Pi 无法接收动态成本，应在 `/route-last` 明确展示
   Gateway 实际估算，避免用户误以为请求免费。

验收：Gateway 地址不可达时 `/login` 在预算内给出可理解错误；完整工具循环后用户可以
查看本轮实际决策；升级 Pi 依赖后契约测试能及时发现破坏性变化。

## 4. P2 候选项

以下任务在 P0/P1 稳定后再考虑：

- CLI 和 Web 注入稳定 `session_id`，Gateway 实现 Provider 黏性与安全迁移。
- 从 `contracts/` 生成 TypeScript 类型/Zod 校验，减少 Web、CLI 与 JSON Schema 漂移。
- 使用 OpenTelemetry 串联 Web/Nginx → Gateway → Provider，接入 Tempo/Jaeger。
- 为 Monitoring API 实现 Kubernetes `resources` 查询；如果它继续只做本地 JSON 消费，
  则明确保持 `resources.available=false`，不要维护半完成的云端接口。
- 增加预算日累计、Provider 限流和按策略版本拆分的管理视图。

## 5. 推荐 PR 顺序与边界

### PR 1：Web Auth + Error Contract

主要文件：`web/`、`docker-compose.yml`、`deploy/web.yaml`、相关 Secret 文档。
不要修改 Provider 路由算法。必须先协调部署文件所有者。

### PR 2：Monitoring SLI + Alerts

主要文件：`monitoring/`、`contracts/monitoring-overview.*`，以及少量 Gateway metrics。
先冻结 outcome 分类，再修改 Dashboard，避免 API 与 Grafana 口径不同。

### PR 3：Decision Record Contract

主要文件：`contracts/`、`gateway/`、Redis 存储和查询 API。先冻结 Schema、TTL、鉴权和
数据脱敏规则；不要在同一 PR 同时重写 Web UI。

### PR 4：Web Streaming

主要文件：`web/src/api/`、conversation reducer、消息 UI 和流式测试。依赖 PR 3 提供
最终决策查询。

### PR 5：CLI Route Result

主要文件：`.pi/extensions/polygate-routing/`。注意该目录是独立 Git 子模块：先在子模块
提交和测试，再更新父仓库的 submodule pointer。

## 6. 回归验证命令

### Web

```bash
cd web
npm test -- --run
npm run build
npx playwright test
```

### Pi CLI

```bash
cd .pi/extensions/polygate-routing
npm run check

cd ../../..
PI_BIN="$(command -v pi)" \
POLYGATE_BASE_URL=http://127.0.0.1:8000/v1 \
POLYGATE_API_KEY=local-development \
./scripts/pi-gateway-smoke-test.sh
```

### Monitoring

```bash
docker compose run --rm monitoring-api \
  python -m unittest discover -s tests -v

./scripts/prometheus-smoke-test.sh
./scripts/monitoring-api-smoke-test.sh
./scripts/grafana-smoke-test.sh
```

### 必做跨端矩阵

| 场景 | Web | CLI | 监控 |
|---|---|---|---|
| Key 正确 | 请求成功 | 请求成功 | 记录 success |
| Key 缺失/错误 | Web 代理仍可用且不泄露内部 Key | 401 + 可理解提示 | client rejection 不计服务错误 |
| Provider 首字节前失败 | 自动路由可 failover | 工具循环继续 | retry/failover 指标增加 |
| 请求预算耗尽 | 显示 timeout + request ID | 显示 504 | provider_timeout 计入服务错误 |
| 用户取消 | 消息可重试 | Pi 正常终止 | cancelled 单独统计 |
| 流输出后断开 | 显示不完整 | 不拼接其他 Provider | partial_error 增加 |

## 7. 明确禁止事项

- 不把 Web Gateway Key 打包进前端、返回给浏览器或存入 localStorage。
- 不把 request ID、prompt、原始错误或 URL 放入 Prometheus 标签。
- 不在标准 OpenAI SSE 中插入 Pi/Web 无法识别的自定义事件。
- 不把客户端 4xx、主动取消直接当作服务端可用性失败。
- 不在 Provider 已输出 token 后透明拼接另一个 Provider 的内容。
- 不让 Web 每收到一个 token 就同步写 localStorage。
- 不在未冻结 Decision Record 契约前同时修改 Gateway、Web、CLI 三端。
- 不直接在父仓库内把 Pi 子模块文件和父仓库改动混成一个不可复现提交。

## 8. 后续模型开始工作前的检查清单

1. 读取根目录 `README.md`、本文件和目标目录 README。
2. 执行 `git status --short --branch`，保留用户已有改动。
3. 确认 `codex/agent-reliability` 是否已合并，尤其检查 504、
   `provider_timeout` 和 `X-PolyGate-Request-ID` 是否存在。
4. 一次只领取上述一个 PR 范围，先写失败测试，再改实现。
5. 修改 `contracts/`、Compose、Kubernetes 清单或 Pi 子模块前确认跨成员边界。
6. 完成后运行本节对应的单元、契约和冒烟测试，并报告未运行的测试及原因。
