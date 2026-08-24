# PolyGate

PolyGate 是面向团队和多模型 AI 应用的云原生 **AI API 网关与策略控制系统**。
客户端通过统一的 OpenAI 兼容接口或 Web 界面提交请求，不需要在业务代码里硬编码
具体 Provider。系统根据质量、隐私、预算、延迟、能力和健康状态选择 Provider，
并返回可解释的决策卡片：选了谁、为什么、是否命中缓存、花费多少、耗时多久；
同时通过缓存、重试、熔断、故障切换和 Kubernetes 弹性扩缩容改善成本、可靠性
和容量。

项目使用 Python、FastAPI、Redis、React、Prometheus、Grafana 和 Kubernetes，
云端部署目标为 AWS EKS（`us-east-1`）。

| Web Console | Operations Dashboard |
|---|---|
| ![PolyGate Web Console](./assets/web-console.png) | ![PolyGate Grafana dashboard](./assets/observability-dashboard.png) |

下面的能力表使用四级状态，避免把代码、部署资产和路线图混为一谈：

- **代码已实现**：仓库中存在对应实现和测试；
- **已有部署清单/脚本**：具备本地或 EKS 部署与验证路径，**但不等于你阅读本文
  时云集群一定在线**——是否仍在运行取决于是否已手动销毁，请勿假设 EKS 端点
  可访问；
- **部分接通**：各组件分别存在，但端到端的用户链路尚未完全打通；
- **规划或后续方向**：明确不是已完成能力，不应被当作交付物。

## 当前状态

| 能力 | 状态 | 说明 |
|---|---|---|
| Gateway 核心路由 | 代码已实现 | OpenAI 兼容接口、真实/Mock Provider、质量·隐私·预算·延迟路由、精确缓存、Decision Card、Decision Record |
| 可靠性 | 代码已实现 | 重试、熔断、故障切换、请求级时间预算 |
| Automation 任务调度 | 代码已实现 | Intent/Preview/Job API、Redis 优先队列、Worker（租约、重试、公平性防饥饿） |
| Policy Control Plane | 代码已实现 | 版本化策略、发布前 validate/simulate、热加载、Last Known Good、回滚、私有 Policy Editor |
| 可观测性 | 代码已实现 | Prometheus 指标、Grafana 大屏（含 Policy 版本漂移面板）、Monitoring API |
| Kubernetes / EKS 部署 | 已有部署清单/脚本 | 完整 manifest、RBAC、Secret、ConfigMap 持久化、HPA、preflight 与 smoke 脚本均已验证通过，但当前是否仍在集群中运行未知 |
| Web ↔ Automation/Pi 端到端链路 | 部分接通 | Web Chat 直连 Gateway 已打通；Web 提交企业任务卡片、Pi Agent 调用 Automation 的完整用户链路尚未完全接通 |
| 语义缓存 / KEDA / 多租户计费 | 规划方向 | 明确不在本轮范围内 |

当前稳定链路：

```text
Browser / OpenAI Client -> Web or Gateway -> Router -> Redis cache -> Provider
                                      \-> Decision Card + Decision Record + Prometheus metrics
```

企业任务调度链路：

```text
Requirement Card -> Automation API -> Priority Queue + Worker -> Gateway -> Provider
                         \-> Preview（可注入 Policy 草稿模拟路由影响）
```

策略变更链路：

```text
Policy Editor -> Automation Policy API（validate/simulate/publish）-> ConfigMap
                         \-> Gateway / Worker 每 5 秒轮询热加载 -> Last Known Good 兜底
```

Automation 与 Policy Control Plane 是独立于 Gateway 的控制面，不侵入
`/v1/chat/completions` 既有契约。

## 本地启动

```bash
cp .env.example .env          # 可留空；填写 REAL_A_API_KEY 后启用真实 DeepSeek Provider
docker compose up --build -d
docker compose ps
```

预期 `redis`、`mock-a`、`mock-b`、`gateway`、`automation`、`automation-worker`、
`web`、`prometheus`、`grafana` 全部健康。

常用入口：

| 服务 | 地址 | 用途 |
|---|---|---|
| Web | <http://localhost:8080> | 多轮 Chat、决策卡片、企业需求卡片 |
| Gateway | <http://localhost:8000> | OpenAI 兼容 API |
| Automation | <http://localhost:8020/docs> | 需求模板、Preview、Job API |
| Policy Editor | <http://localhost:8020/admin/policies> | 策略编辑、校验、模拟、发布、回滚（私有，不经 Web Nginx 暴露） |
| Monitoring API | <http://localhost:8010/api/monitoring/overview> | 固定 Prometheus 查询的 JSON 总览 |
| Prometheus | <http://localhost:9090/targets> | 指标采集状态与查询 |
| Grafana | <http://localhost:3000/d/polygate-overview/polygate-overview> | 业务、Kubernetes 资源与 Policy 版本大屏 |

Pi Agent 可把 PolyGate 注册为 `polygate/auto` 模型 Provider，通过 SSE 完成
本地工具循环。`/login polygate` 的网关验证受五秒预算约束；工具循环结束后可用
`/route-last` 查看最终 Provider、Gateway 实际成本估算、tokens、重试、failover 和
request ID，且这些决策数据不会进入模型 Prompt。无真实模型、无费用的 Mock 验证
方式见 [`gateway/README.md`](./gateway/README.md#pi-agent-可运行闭环)，扩展安装与使用
说明见 [`.pi/extensions/polygate-routing/README.md`](./.pi/extensions/polygate-routing/README.md)。

## EKS 部署

部署清单和自动化脚本位于 `deploy/`，已通过完整 preflight 与 smoke test 验证
（见下方"本地集成闸门"第 3、4 步）。云端公开入口是 Web 的 Kubernetes
NodePort（`http://<节点公网IP>:30080`），而不是固定域名的 LoadBalancer；
Gateway、Automation、Worker、Redis、Prometheus、Grafana 和 Policy Editor 均
保持集群内部访问，仅管理员通过 `kubectl port-forward` 触达。

本文档不假设存在持续在线的共享集群。部署或排障前请按 `deploy/README.md`
检查目标环境、凭证和资源状态。

## 本地集成闸门（部署前必须全绿）

**0. 起完整栈**

```bash
docker compose up --build -d && docker compose ps
```

**1. 后端测试**（需在 Python 3.12 容器/环境下跑）

Worker 测试需要真实 Redis（`AUTOMATION_TEST_REDIS_URL`，用 db 15）；Gateway 套件
也需要 Redis，否则若干缓存用例会**静默跳过**而不是失败——只看 `0 failed` 会
误判，要同时确认 skip 数为 0。

```bash
AUTOMATION_TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
  python -m pytest automation/tests -q
cd gateway && python -m pytest tests -q && cd ..
```

**2. Web 测试**

```bash
cd web && npm test && npm run lint && npm run build && cd ..
```

当前 Web 测试环境使用 Node 22。Node 25 的实验性 `localStorage` 会与 jsdom
冲突，因此不属于受支持的测试运行时。

**3. 契约与部署回归**

```bash
python3 scripts/tests/test-automation-contracts.py
python3 scripts/tests/test-policy-contracts.py      # 需要 pip install jsonschema
bash scripts/tests/test-deployment-automation.sh
bash scripts/tests/test-deployment-policy.sh
./scripts/kubernetes-monitoring-preflight.sh        # 12 项
```

**4. 行为冒烟**

```bash
./scripts/web-smoke-test.sh
./scripts/kubernetes-automation-smoke-test.sh
POLICY_ADMIN_KEY=local-policy-admin-development \
  PROMETHEUS_URL=http://localhost:9090 \
  ./scripts/kubernetes-policy-smoke-test.sh
./scripts/automation-peak-test.sh
```

`automation-peak-test.sh` 提交顺序刻意与优先级相反（low 先提交），所以看到
critical 最先被执行才说明调度按 `effective_priority` 生效，而不是碰巧。

配置了真实 DeepSeek Key 时，再跑 Flash/Pro 质量路由闸门。它会产生少量真实
API 费用，并验证强制 Flash/Pro、高质量选 Pro、紧预算回落 Flash、Web SSE、
Decision Record 以及 Policy Editor Preview：

```bash
set -a; source .env; set +a
POLYGATE_API_KEY="${POLYGATE_API_KEYS%%,*}" \
POLICY_ADMIN_KEY=local-policy-admin-development \
  ./scripts/deepseek-v4-routing-smoke-test.sh
```

**5. 安全不变量**

| 不变量 | 怎么验 |
|---|---|
| Policy Editor 不经 Web Nginx 暴露 | `curl :8080/admin/policies` 返回的必须是 Chat 的 SPA 兜底页，不是编辑器——**只看状态码会得到假阳性**，200 是 SPA 兜底 |
| admin key 不进日志 | `docker compose logs \| grep -F "<key>"` 无命中 |
| `privacy=high` 不落到 DeepSeek | 发一个 `privacy=high` 请求，看 `polygate.provider` |
| finance privacy 不可降级 | 改 `finance_summary.defaults.privacy` 后 validate/publish 均应 422 |
| `/internal/routing/simulate` 不外露 | 不在 Gateway OpenAPI 中；**经 `:8080/api/` 也必须不可达** |
| 已存在的 ConfigMap 不被覆盖 | `test-deployment-policy.sh` 已用 fake deploy 断言 |

**本地 Compose 的一个预期差异**：Automation API 用的是内存仓库
（`POLICY_ALLOW_ENV_ADMIN_KEY=true`），所以 publish/rollback **不写回**
`policy-store.json`，重启容器即回到初始版本。本地冒烟验的是 API 语义与热
加载，ConfigMap 持久化只能在 EKS 上验证。

## Repository map

| 路径 | 职责 |
|---|---|
| `gateway/` | OpenAI 兼容 API、路由、缓存、可靠性与 Decision Record |
| `providers/` | Provider 适配器和可控故障注入 Mock |
| `automation/` | Intent/Preview/Job、Worker 调度与 Policy 生命周期 |
| `web/` | Chat、路由偏好和决策卡片界面 |
| `agent/`、`.pi/extensions/` | Agent 接口边界与 Pi 集成 |
| `contracts/` | 跨组件 JSON Schema、示例和 Provider 注册表 |
| `deploy/`、`monitoring/` | Compose/Kubernetes/EKS、Prometheus 与 Grafana |
| `scripts/` | 契约回归、部署校验、冒烟和压力测试 |

组件级说明：

- [Gateway](./gateway/README.md) · [Providers](./providers/README.md)
- [Automation Service](./automation/README.md) · [Contract Registry](./contracts/README.md)
- [Web Console](./web/README.md) · [Agent / Pi integration](./agent/README.md)
- [Kubernetes deployment](./deploy/README.md)
- [Local monitoring](./monitoring/README.md) · [Kubernetes monitoring](./deploy/monitoring/README.md)

## Compatibility and security rules

- `contracts/` 是跨服务接口的唯一真相来源；不兼容变更必须同步更新实现、示例和契约测试。
- 不提交 `.env`、AWS 凭证、Provider API Key、Grafana 密码或真实员工 Prompt。
- Apple Silicon 本地开发可使用原生镜像，但推送到 x86_64 EKS 节点的镜像必须
  使用 `--platform linux/amd64`。
- 监控页面与 Policy Editor 是管理员内部工具，不通过公网 NodePort 或
  LoadBalancer 直接暴露。

## 范围边界

当前版本优先保证路由决策可解释、运行行为可复现、控制面变更可回滚。语义缓存、
KEDA、Provider CRD、复杂多租户计费和生产级身份系统尚未实现，不应被视为现有能力。
