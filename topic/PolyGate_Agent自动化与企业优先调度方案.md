# PolyGate Agent 自动化与企业优先调度方案

## 1. 方案定位

新增功能名称：

> **Intent-to-API Automation and Priority-Aware AI Scheduling**  
> 业务意图到 API 的自动化，以及面向企业高峰期的优先级智能调度。

该功能在现有 PolyGate 上增加两种能力：

1. 用户通过 Chat 界面和需求卡片表达业务偏好，系统自动生成 OpenAI/PolyGate JSON、curl 和 Python 请求，不要求用户手写 API。
2. 多名公司员工在高峰期同时申请 AI 资源时，系统根据业务场景、紧急度和等待时间自动确定执行顺序。

系统形成两个彼此独立的决策层：

```text
第一层：Automation Service 决定“哪个员工任务先执行”
第二层：PolyGate Gateway 决定“这个任务交给哪个模型 Provider”
```

Pi 只负责 Chat 对话和插件调用，不承载公司政策、调度算法或 Provider 路由。未来更换其他 Agent 时，只需要替换薄插件。

## 2. 总体架构

```text
Internet
   │
LoadBalancer → Web/Nginx
                 ├── /             → Chat UI
                 ├── /agent/*      → Pi Agent Service
                 ├── /v1/*         → Gateway（保留开发者 API）
                 └── /providers    → Gateway

Pi Agent Service
   └── PolyGate Extension
         ├── preview_polygate_request
         ├── submit_polygate_job
         └── get_polygate_job
                    │
                    ▼
Automation Service
   ├── Template Compiler
   ├── Priority Policy
   ├── Job API
   └── Redis Priority Queue
                    │
            Scheduler Worker
                    │
                    ▼
Existing Gateway → Redis Cache / Providers

Prometheus → Gateway + Automation + Agent metrics → Private Grafana
```

### 2.1 低耦合边界

- Gateway 保持现有 `/v1/chat/completions`、缓存、Provider 路由和 decision card，不依赖 Pi。
- Pi 使用独立的 OpenAI-compatible 模型连接；只有用户确认后的业务任务进入 PolyGate。
- Pi Extension 只转换参数并调用 Automation HTTP API，不包含模板、优先级和路由算法。
- Automation Service 不直接调用 Provider，只负责编译请求、排队和调用现有 Gateway。
- Automation API 与 Worker 使用同一代码库、不同进程；API 可扩展，Worker 演示期保持单副本。
- Browser 不直接访问 Automation、Redis、Provider、Prometheus 或 Grafana。
- Chat 流式响应采用 SSE，Nginx 对 `/agent/` 关闭响应缓冲。

## 3. 用户交互

用户界面由真正的 Chat 窗口和右侧需求卡片组成。需求卡片由用户手动填写，避免 Agent 猜测隐私、预算或紧急程度。

需求卡片包含：

```json
{
  "employee": "Alice",
  "department": "engineering",
  "scenario": "production_incident",
  "urgency": "critical",
  "prompt": "Analyse the production incident log",
  "preferences": {
    "quality": "high",
    "privacy": "high",
    "max_cost_usd": 0.01,
    "latency_target_ms": 1000
  }
}
```

用户流程：

```text
填写需求卡片
  → Preview 自动生成 JSON/curl/Python
  → 用户确认
  → Pi Extension 提交 Job
  → 页面展示 queued/running/completed/failed
  → 完成后展示 Automation 调度理由和 Gateway decision card
```

Pi Extension 注册三个工具：

- `preview_polygate_request`
- `submit_polygate_job`
- `get_polygate_job`

## 4. Automation API

```text
GET  /v1/templates
POST /v1/requests/preview
POST /v1/jobs
GET  /v1/jobs/{job_id}
GET  /v1/jobs?status=queued,running&limit=50
GET  /health
GET  /metrics
```

`GET /v1/jobs` 仅供集群内部 Agent 或管理员使用，不通过公网 Nginx 暴露。

### 4.1 Preview

`POST /v1/requests/preview` 不执行模型调用，也不产生 Provider 费用。返回：

```json
{
  "preview_id": "preview_uuid",
  "expires_in_seconds": 600,
  "normalized_intent": {},
  "priority": {
    "class": "critical",
    "initial_score": 140,
    "reason": "Critical urgency + production incident"
  },
  "gateway_request": {},
  "snippets": {
    "json": "...",
    "curl": "...",
    "python": "..."
  },
  "policy_adjustments": []
}
```

Preview 保存 10 分钟。用户修改需求卡片后必须重新 Preview。

### 4.2 提交 Job

```http
POST /v1/jobs
Idempotency-Key: <client-generated UUID>
```

```json
{
  "preview_id": "preview_uuid",
  "confirmed": true
}
```

成功返回 `202 Accepted`：

```json
{
  "job_id": "job_uuid",
  "status": "queued",
  "priority": {},
  "queue_position": 2,
  "created_at": "..."
}
```

Job 状态固定为：

```text
queued → running → completed
                  ↘ failed
```

完成状态包含原始 Gateway response 和 decision card。

### 4.3 Agent API

Agent Service 暴露 `POST /agent/chat`。请求包含 `session_id`、聊天消息和可选需求卡片；响应使用以下 SSE 事件：

```text
message.delta
tool.call
tool.result
job.status
done
error
```

## 5. 请求模板

| 模板 | Gateway 默认值 | 强制政策 |
|---|---|---|
| Production Incident | high / high privacy / 1000 ms / $0.01 | 日志默认不出公司 |
| Customer Escalation | balanced / standard / 1500 ms / $0.01 | 无 |
| Finance Document Summary | balanced / high privacy / 3000 ms / $0.005 | privacy 不可降级 |
| Marketing Batch Content | cheap / standard / 5000 ms / $0.002 | 无 |

用户偏好可以覆盖模板默认值，但不能覆盖安全政策。Urgency 只影响公司任务调度，不直接修改 Gateway 的质量、预算和 Provider 选择，从而保持两层决策独立。

## 6. 优先调度算法

基础评分：

```text
effective_priority = urgency_score + scenario_weight + waiting_bonus
```

紧急度分数：

| Urgency | Score |
|---|---:|
| Critical | 100 |
| High | 60 |
| Normal | 30 |
| Low | 10 |

场景分数：

| Scenario | Weight |
|---|---:|
| Production Incident | +40 |
| Customer Escalation | +25 |
| Finance Summary | +15 |
| Marketing Batch | +0 |

等待奖励为每等待 5 秒加 1 分，最多加 30 分。

选择规则：

1. Worker 每 1 秒形成一次 admission window。
2. 正常情况下选择 effective priority 最大的任务。
3. 同分任务按创建时间 FIFO。
4. 已经 Running 的任务不抢占。
5. 连续执行 3 个 Critical/High 后，如果 Normal/Low 已等待至少 20 秒，则执行其中等待时间最长的任务。
6. 执行低优先级任务后，高优先 streak 清零。

该公平护栏保证高峰期优先处理紧急任务，同时避免普通任务永久饥饿。

Redis 使用独立数据库或 `polygate:automation:` key 前缀，与 Gateway Cache 隔离。

## 7. 可靠性与安全

- `Idempotency-Key` 在 1 小时内返回同一个 Job，防止双击和 Agent 重试重复提交。
- Preview 保存 10 分钟；Job、Prompt 和演示 Session 保存 1 小时。
- Prompt、employee 和 job_id 不进入 Prometheus label 或普通日志。
- Redis 不可用时返回 503，不绕过队列直接调用 Gateway。
- Gateway 暂时不可用时，Worker 最多重试两次，退避时间为 1 秒和 2 秒。
- Worker 为 Running Job 设置租约；进程重启后恢复超时任务一次，再次失败则结束任务。
- 员工和部门下拉框只代表演示身份，不宣称真实认证或授权。
- Agent API Key、DeepSeek Key 和 Grafana 密码全部使用 Kubernetes Secret。
- Redis 在 Learner Lab 中继续使用 `emptyDir`；Redis Pod 重建会丢失临时 Job，这是演示环境限制。

## 8. 可观测性

Automation 指标：

```text
polygate_automation_queue_depth{priority}
polygate_automation_jobs_total{status,priority,scenario}
polygate_automation_job_wait_duration_seconds{priority}
polygate_automation_job_execution_duration_seconds{priority}
polygate_automation_worker_busy
polygate_automation_template_previews_total{template}
```

Agent 指标：

```text
polygate_agent_tool_calls_total{tool,outcome}
```

Grafana 新增 “Enterprise Scheduling” 区域：

- Queue Depth by Priority
- P95 Queue Wait
- Running/Completed/Failed Jobs
- Worker Utilization
- Jobs by Scenario
- Agent Tool Calls

现有 Gateway 请求率、错误率、P95、缓存、Token、成本、CPU、内存和 HPA 面板全部保留。所有指标禁止使用 employee、prompt 或 job_id 作为 label。

## 9. EKS 部署

- Web/Nginx 是唯一 `LoadBalancer`。
- Agent、Automation API、Worker、Gateway、Redis、Prometheus 和 Grafana 均使用 `ClusterIP` 或无 Service 的内部工作负载。
- Prometheus/Grafana 继续通过 `kubectl port-forward` 由管理员访问，不加入用户公网入口。
- Gateway 保持两个基础副本和现有 HPA。
- Worker 固定单副本、并发 1，确保课堂演示中的队列顺序清晰。
- Apple Silicon 上构建所有 EKS 镜像时继续指定 `linux/amd64`。

## 10. 四人分工

### Member A：Automation API 与模板编译

- 冻结需求卡片、Preview、Job 和结果契约。
- 实现四个模板、覆盖规则和 Finance 隐私锁定。
- 生成 Gateway JSON、curl 和 Python。
- 实现 Job API、状态查询和 Gateway 调用边界。
- 保证现有 Gateway 契约与测试不被破坏。

### Member B：队列与 Scheduler

- 实现 Redis Job Repository、优先队列和 Worker。
- 实现评分、FIFO、公平护栏、幂等、租约和失败重试。
- 提供四员工高峰 fixture。
- 配置 Mock Provider 延迟，确保演示可观察且不产生真实 API 费用。

### Member D：Pi Agent 与 Chat UI

- 使用 Node/TypeScript 包装 Pi SDK 和 SSE Agent API。
- 实现三个 PolyGate Extension 工具。
- 将当前页面改成 Chat 主窗口和右侧需求卡片。
- 实现 Preview、代码示例、确认提交、Job 时间线和 Peak Scenario Demo。
- UI 明确区分 Automation 调度理由和 Gateway decision card。

### Member C：Kubernetes 与可观测性

- 为 Web/Nginx、Agent、Automation API 和 Worker构建 `linux/amd64` 镜像。
- 增加 EKS Deployment、ClusterIP Service、ConfigMap、Secret 和健康探针。
- 创建单 LoadBalancer 入口和 Nginx 同源路由。
- 扩展 Prometheus discovery、Grafana Dashboard 和云端 smoke test。
- 编写高峰演示脚本、故障恢复验证和运行手册。

## 11. 五天实施安排

- **Day 1：** 合并并冻结新契约和模板；四人再从最新 main 创建分支。
- **Day 2：** A/B 完成核心单测；D 完成 Pi 工具调用和 Chat 骨架；C 完成本地部署骨架。
- **Day 3：** Docker Compose 端到端联调，完成四任务顺序、Preview 和 Job 状态。
- **Day 4：** 构建 amd64 镜像、部署 EKS、接入 Prometheus/Grafana，完成失败和重启测试。
- **Day 5：** 单 URL 联调、截图、讲稿和两次完整彩排；中午后冻结功能。

Pull Request 顺序：

```text
contracts → Automation/Worker → Agent/Web → deploy/monitoring
```

不得使用 `git add .`，不得提交 Secret、凭证、Token 或真实 Prompt。

## 12. 测试与验收

### 自动测试

- 四个模板生成合法且稳定的 Gateway Request 和代码片段。
- Finance privacy 无法从 high 降为 standard。
- Preview 过期、非法字段和未确认提交会被拒绝。
- 相同 Idempotency-Key 只创建一个 Job。
- 同批任务按照 Critical → High → Normal → Low 执行。
- 同分任务遵循 FIFO。
- 连续三个高优先任务后，等待超时的低优先任务获得执行。
- Worker 重启后恢复租约任务，且最多恢复一次。
- Gateway 两次重试失败后 Job 进入 failed。
- Pi 三个工具在 Mock Automation API 下通过。
- 现有 Gateway、Cache、Metrics、HPA 和 Monitoring 测试全部回归通过。

### 云端验收

- 用户只需要一个 LoadBalancer URL。
- Chat、需求卡片、Preview、Pi 工具、队列、Gateway 和 decision card 完成端到端。
- 除 Web/Nginx 外，所有服务均不暴露公网。
- Prometheus 中 Gateway、Automation、Agent、kube-state-metrics 和 cAdvisor targets 全部 UP。
- Grafana 同时展示企业队列指标和原有模型网关指标。

## 13. 课堂演示流程

演示前通过内部 Mock 管理接口增加 2–4 秒延迟，让队列状态可观察，不公开 Mock 管理端。

Peak Scenario 同时提交：

```text
Engineering · Production Incident · Critical
Support     · Customer Escalation  · High
Finance     · Document Summary     · Normal
Marketing   · Batch Content        · Low
```

演示步骤：

1. 打开一个 LoadBalancer URL，展示 Chat 和需求卡片。
2. 填写部门、场景、紧急度、隐私和预算。
3. Preview 自动生成 JSON、curl 和 Python。
4. 用户确认后，由 Pi Extension 提交 Job。
5. Peak Scenario 将四个任务放入队列。
6. 页面展示 Critical → High → Normal → Low 的状态变化。
7. 每个完成任务展示 Automation 调度理由和 Gateway decision card。
8. 尝试降低 Finance 隐私等级，展示服务器端政策拒绝。
9. 通过私有 Grafana 展示队列、等待时间、工具调用、成本、缓存和 HPA。

最终讲解：

> P0 解决“请求交给哪个模型”；P1 解决“系统如何观察和自动扩容”；新增功能进一步解决“企业高峰期哪个业务请求应该先获得 AI 资源”。Agent 负责交互，Automation 负责确定性公司政策，Gateway 负责模型路由，三层彼此独立。

## 14. 本期明确不做

- Pi 的内部模型请求全部经过 PolyGate。
- Agent 自动猜测隐私、预算和紧急程度。
- 真实用户登录与部门权限系统。
- 运行中任务抢占。
- 部门月度配额和真实计费。
- SQS、Celery、多 Worker 分布式锁和生产级持久化。
- 公网 Prometheus、Grafana 或 Mock 管理接口。

