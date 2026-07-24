# PolyGate

PolyGate 是面向多模型 AI 应用的云原生智能网关。用户通过统一的 OpenAI
兼容接口调用 AI，Gateway 根据价格、健康、预算、质量和隐私约束选择 Provider，
并返回可解释的决策卡片：选了谁、为什么、是否命中缓存、花费多少以及耗时多久。

项目使用 Python、FastAPI、Redis、React、Prometheus、Grafana 和 Kubernetes，
云端部署目标为 AWS EKS（`us-east-1`）。

## 当前进度

| 阶段 | 状态 | 已实现能力 |
|---|---|---|
| P0：核心网关 | ✅ 完成 | OpenAI 兼容接口、真实/Mock Provider、规则路由、预算与隐私约束、精确缓存、决策卡片、Web Chat、EKS 部署与冒烟测试 |
| P1：云原生与可观测性 | ✅ 主体完成 | metrics-server、Gateway HPA、压力扩缩容验证、Prometheus、Grafana、Kubernetes 资源大屏 |
| Agent 自动化扩展 | 🟡 并行开发中 | Automation 契约、需求模板、Preview、API 请求代码生成、Job API 骨架；Redis 优先队列、Worker、Pi Agent 和最终云端接线待完成 |
| 单一公网入口 | ⏳ 集成阶段处理 | 计划使用一个 LoadBalancer 暴露 Web/API；Prometheus 与 Grafana 保持 ClusterIP，仅管理员通过 port-forward 访问 |

当前稳定链路：

```text
Browser / OpenAI Client -> Web or Gateway -> Router -> Redis cache -> Provider
                                      \-> Decision Card + Prometheus metrics
```

正在开发的自动化链路：

```text
Chat / Pi Agent -> Automation API -> Priority Queue + Worker -> Existing Gateway
                         \-> Preview + JSON/curl/Python request templates
```

Automation 是独立控制面，不把 Agent、队列或企业调度逻辑塞进 Gateway，已有
`/v1/chat/completions` 契约保持不变。

## 本地启动

```bash
cp .env.example .env          # 可留空；填写 REAL_A_API_KEY 后启用真实 Provider
docker compose up --build -d
docker compose ps
```

常用入口：

| 服务 | 地址 | 用途 |
|---|---|---|
| Web | <http://localhost:8080> | 多轮 Chat 与决策卡片 |
| Gateway | <http://localhost:8000> | OpenAI 兼容 API |
| Automation | <http://localhost:8020/docs> | 需求模板、Preview 和 Job API |
| Monitoring API | <http://localhost:8010/api/monitoring/overview> | 固定 Prometheus 查询的 JSON 总览 |
| Prometheus | <http://localhost:9090/targets> | 指标采集状态与查询 |
| Grafana | <http://localhost:3000/d/polygate-overview/polygate-overview> | 业务与 Kubernetes 资源大屏 |

Pi Agent 可把 PolyGate 注册为 `polygate/auto` 模型 Provider，通过 SSE 完成
本地工具循环。`/login polygate` 的网关验证受五秒预算约束；工具循环结束后可用
`/route-last` 查看最终 Provider、Gateway 实际成本估算、tokens、重试、failover 和
request ID，且这些决策数据不会进入模型 Prompt。无真实模型、无费用的 Mock 验证
方式见 [`gateway/README.md`](./gateway/README.md#pi-agent-可运行闭环)，扩展安装与使用
说明见 [`.pi/extensions/polygate-routing/README.md`](./.pi/extensions/polygate-routing/README.md)。

推荐验证顺序：

```bash
./scripts/smoke-test.sh
./scripts/web-smoke-test.sh
./scripts/automation-skeleton-smoke-test.sh
./scripts/prometheus-smoke-test.sh
./scripts/monitoring-api-smoke-test.sh
./scripts/grafana-smoke-test.sh
```

## 四人分工与并行边界

| 成员 | 主责范围 | 当前任务 | 不应直接修改 |
|---|---|---|---|
| A | `gateway/`、Automation 编译/API | 需求模板规则、Preview 编译、Gateway Client | Redis Worker、Pi UI、Kubernetes 清单 |
| B | `providers/`、Automation 调度执行 | Redis Store、优先队列、Scheduler Worker、租约与重试 | Gateway 路由规则、Pi Extension、Grafana Dashboard |
| C | `deploy/`、`monitoring/`、部署脚本 | Automation/Agent 的 Compose 与 EKS 接线、指标与大屏、最终 LoadBalancer | Agent 对话逻辑、Gateway 路由算法 |
| D | `web/`、`agent/`、`.pi/extensions/` | Chat UI、需求卡片、Pi Extension、Agent Service | Automation 企业策略、Redis 调度、Kubernetes 清单 |
| 全体 | `contracts/` | 评审和冻结跨服务接口 | 未沟通时不得单独修改契约 |

共享组合文件（例如 `docker-compose.yml`、Prometheus 配置和最终入口配置）由 C
统一接线。其他成员通过 PR 说明所需端口、环境变量和健康检查，减少同一文件冲突。

## Agent 自动化并行计划

1. **契约与骨架（已完成）**：冻结 Intent、Preview、Job Schema，提供可运行的
   Automation FastAPI 服务和 Compose 入口。
2. **并行实现**：A 完成编译与 Gateway Client；B 完成 Redis 队列和 Worker；D
   完成 Pi 工具与 Chat 交互；C 准备监控指标和部署清单。
3. **本地集成**：验证“填写需求卡片 → Preview → 用户确认 → 排队 → Gateway
   执行 → 决策卡片”的完整流程，并演示不同部门和紧急程度的调度顺序。
4. **云端集成**：构建 `linux/amd64` 不可变镜像，部署到 EKS，保持监控私有，
   通过单一 LoadBalancer 暴露用户端。
5. **最终演示**：同时提交 Production Incident、Customer Escalation、Finance
   Summary 和 Marketing Batch 请求，展示优先调度、隐私锁定、成本路由、HPA
   扩缩容及 Grafana 指标变化。

当前 Automation Job 使用进程内存保存，只用于冻结接口和并行联调；它还不会真正
消费队列或调用 Provider。Redis 持久化和 Worker 合并之前，不应将其描述为完整的
企业调度系统。

## 契约与协作规则

- `contracts/` 是跨服务接口的唯一真相来源，修改必须获得所有受影响成员确认。
- 不提交 `.env`、AWS 凭证、Provider API Key、Grafana 密码或真实员工 Prompt。
- A、B、D 日常使用 Docker Compose；EKS 和 Learner Lab 操作由 C 统一执行。
- Apple Silicon 本地开发可以使用原生镜像，但推送到当前 x86_64 EKS 节点的镜像
  必须使用 `--platform linux/amd64`。
- 监控页面是管理员内部工具，不通过公网 NodePort 或 LoadBalancer 直接暴露。

## 详细文档

- [Automation Service](./automation/README.md)
- [Agent / Pi 接口边界](./agent/README.md)
- [Agent 自动化与企业优先调度方案](./docs/PolyGate_Agent自动化与企业优先调度方案.md)
- [四人并行开发对接清单](./docs/PolyGate_Agent并行开发对接清单.md)
- [本地监控说明](./monitoring/README.md)
- [Kubernetes 监控部署说明](./deploy/monitoring/README.md)

## 范围边界

课程项目优先保证可解释、可演示和可复现。当前不实现语义缓存、KEDA、Provider
CRD、复杂多租户计费或生产级身份系统；这些属于后续扩展，而不是本轮五天集成的
验收前置条件。
