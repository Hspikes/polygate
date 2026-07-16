# PolyGate：面向多模型 AI 应用的云原生智能网关

## 1. 项目定位

PolyGate 不是单纯转发大模型 API 的“AI 中转站”，而是一个面向实际 AI 应用的云原生模型访问与治理平台。

用户仍然通过统一接口调用 AI，但平台会根据任务类型、服务状态、延迟、价格、隐私等级和预算，在多个模型提供方之间自动选择、切换和扩缩容，并将全过程可视化。

一句话概括：

> 普通中转站解决“如何统一访问多个模型”，PolyGate 进一步解决“在复杂云环境中，如何可靠、经济、可观测地使用多个模型”。

## 2. 普通 AI 中转站的局限

常见 AI 中转站通常提供以下能力：

- 统一 API 地址和鉴权；
- 转发 OpenAI 兼容请求；
- 管理多个 API Key；
- 统计 token 和余额；
- 在某个服务不可用时手动更换渠道。

这些功能很实用，但系统通常并不真正理解请求和运行状态：所有请求可能固定转发到同一渠道，故障后才被动切换，重复请求仍会重复付费，也缺少完整的云端监控和弹性机制。

## 3. PolyGate 相比普通中转站的优势

| 能力 | 普通 AI 中转站 | PolyGate |
|---|---|---|
| 模型选择 | 用户手动指定或固定映射 | 根据任务、质量、延迟、价格和预算自动选择 |
| 故障处理 | 请求失败后返回错误或简单重试 | 健康检查、熔断、自动降级和跨 Provider 切换 |
| 成本控制 | 事后统计消费 | 请求前预算判断、低成本路由、精确缓存复用和每日上限 |
| 性能优化 | 基本转发 | 并发控制、负载均衡与精确/规范化缓存；语义缓存仅作冲刺项 |
| 弹性伸缩 | 网关固定副本 | 对无状态网关按请求/CPU 使用 HPA 扩缩容；KEDA 队列版仅作冲刺项 |
| 可观测性 | 简单调用日志 | Prometheus/Grafana 指标、结构化日志、路由原因和费用估算 |
| 治理能力 | 主要管理 API Key | 每用户一个 Key、简单限速、每日费用上限和最小审计记录 |
| 用户体验 | “把请求转出去” | “在约束条件下选择当前最合适的 AI 服务” |

PolyGate 的价值不在于比中转站多堆几个功能，而在于把一次 AI 请求视为一个需要被云平台管理的工作负载。

## 4. 典型使用场景

用户通过一个 AI 工作台提交任务，例如：

- 普通聊天；
- 长文档摘要；
- 代码解释；
- 高隐私文本处理；
- 低成本批量分类。

用户可以声明约束：

```yaml
task: document-summary
priority: normal
latency_target: 3s
max_cost: 0.002
privacy: standard
quality: balanced
```

平台随后比较候选服务：

- Provider A：质量高、价格高、当前负载正常；
- Provider B：价格低，但近期延迟较高；
- Local / Mock Provider：免费或低成本、隐私好，但能力有限；
- Cached Result：已有完全相同或规范化后的请求结果。

最终界面不仅显示答案，还显示：

- 选择了哪个模型；
- 为什么选择它；
- 是否发生精确缓存命中；
- 请求延迟和估算费用；
- 是否发生重试或故障切换。

这样，应用本身负责提供直观体验，云计算能力在后台真实改善可靠性、成本和性能。

## 5. 系统架构

```text
AI Application UI
        │
Gateway API / Envoy Gateway
        │
PolyGate Router
 ┌──────┼──────────────┐
 │      │              │
Policy  Exact Cache    Provider Health
成本/预算 Redis        探测/熔断/恢复
 │
Provider Adapters
Real API A · Mock Provider A · Mock Provider B

Metrics / Structured Logs → Prometheus + Grafana
Gateway Load             → HPA → Autoscaling
```

建议只接入一个真实 API，其余提供方使用可控制延迟、价格和故障的 Mock Provider。这样既能演示真实调用，也不依赖昂贵 GPU；压测、缓存和故障演示只访问 Mock Provider，避免额度失控。

## 6. 结合的云计算前沿话题

### 6.1 AI Gateway 与模型感知路由

传统 API Gateway 主要根据 URL、Header 和权重路由；AI Gateway 开始进一步考虑模型、请求优先级、服务负载和推理特征。Kubernetes 社区已成立 AI Gateway Working Group，Gateway API Inference Extension 也在推进模型感知路由和 AI 流量标准化。

PolyGate 将这一方向简化为课程项目可实现的版本：根据价格、健康状态、任务类别和用户约束动态路由。

### 6.2 多云与供应商容错

模型 API 天然具有多供应商特征。PolyGate 把不同模型服务视为多个云后端，通过健康检查、熔断、重试和故障切换降低单一供应商故障、限流或价格变化带来的影响。

### 6.3 弹性伸缩

本项目以同步网关为主，因此使用负载生成器压测 PolyGate 网关本体，并由 HPA 扩缩无状态网关副本。这能直接说明突发请求下的弹性，不引入队列和 Worker 的第二套架构。

KEDA、Knative 的事件驱动扩缩容与缩容到零作为后续扩展方向；只有 P0/P1 稳定后才尝试队列版。

### 6.4 AI 可观测性

OpenTelemetry 正在标准化 GenAI 调用中的模型名称、token 数量、延迟和 trace 等信息。PolyGate 在本次项目中先统一采集：

- 每个 Provider 的成功率和 p95 延迟；
- 输入、输出 token；
- 每次请求费用估算；
- 缓存命中率；
- 路由和故障切换原因。

实现上采用 Prometheus + Grafana 基础大屏；“请求旅程”由带 request_id 的结构化日志拼接。完整 OpenTelemetry Collector 和追踪后端只作为后续扩展，不作为十天承诺。

### 6.5 FinOps 与成本感知计算

传统云平台关注 CPU、存储和网络成本；AI 应用又增加了按 token 计费。PolyGate 可以将预算作为一等约束，在满足基本质量和延迟的情况下优先选择更便宜的服务，并通过缓存、单请求上限和每日费用上限避免重复支出与演示失控。

### 6.6 云原生治理与多租户

本项目将治理范围缩小为：每个演示用户一个 Key、简单每分钟限速、每日费用上限，以及敏感文本不得发送至外部 Provider 的规则。角色权限、复杂配额报表和完整平台级审计不纳入本次实现承诺。

## 7. 演示设计

一次完整演示可以控制在五分钟内：

1. 用户提交一个带预算和延迟要求的摘要任务；
2. Dashboard 展示多个 Provider 的价格、延迟和健康状态；
3. PolyGate 自动选择合适模型并说明原因；
4. 再次提交相同请求，展示精确缓存命中和费用节省；
5. 人为让当前 Provider 返回错误或增加延迟；
6. 系统触发熔断并自动切换 Provider；
7. 使用负载生成器制造请求突发，展示网关 HPA 自动扩容；
8. 最后展示费用、延迟、错误率，以及由 request_id 串起的简化请求旅程。

评委看到的不是若干孤立 Kubernetes 功能，而是一条统一故事：请求增加时系统自动扩容，服务异常时自动切换，重复请求通过缓存节约费用，所有决策都可以被观察和解释。

## 8. 推荐实现范围与优先级

### P0（第 1-4 天，必须完成）

- 一个极简 AI Web 应用；
- 一个统一 OpenAI-compatible Gateway；
- 一个真实 Provider + 两个 Mock Provider；
- 基于价格、健康状态和预算的规则路由；
- Redis 精确/规范化缓存；
- 显示“答案 + 决策卡片（选了谁、为什么、是否命中、多少钱）”的界面；
- 按课程规范部署到 Kubernetes。

### P1（第 5-6 天，可靠性故事）

- 健康检查、重试、熔断和 fallback；
- Prometheus + Grafana 基础大屏；
- 每用户一个 Key、简单限速与每日费用护栏。

### P2（第 7 天，二选一）

- Provider CRD：`kind: Provider` 中描述 endpoint、价格和隐私级别，网关动态感知 Provider 增删；或
- HPA 压测强化：明确展示请求突发下网关副本数增加和恢复。

### P3（纯冲刺，不承诺）

- 语义缓存；
- KEDA 队列与 Worker；
- 租户配额报表。

不必自行训练模型，也不必实现复杂机器学习路由算法。项目的竞争力主要来自完整性、真实运行效果和清楚的云端故事。

## 9. 四人分工与十日推进

| 成员 | 独立主责 | 协作接口 | 本人验收物 |
|---|---|---|---|
| 成员 A：网关与策略 | 统一 API、请求规范化、路由、费用估算、决策卡片接口 | 接收 B 的 Provider 状态；向 D 提供决策 JSON | 同一请求能解释为何选择某 Provider |
| 成员 B：Provider 与可靠性 | 真实/Mock Adapter、Redis 缓存、健康检查、重试、熔断、fallback | 向 A 输出可复现的价格/延迟/故障状态 | 人为使 A 故障后，下一请求可切换到 B |
| 成员 C：Kubernetes 与可观测性 | 镜像、Secret、Deployment、Redis StatefulSet/PVC、Gateway API、HPA、Prometheus/Grafana | 提供集群入口、指标端点和压测环境 | 从清单部署；探针、资源限制和 HPA 有可见证据 |
| 成员 D：前端、测试与叙事 | 极简 UI、决策卡片、演示控制、负载/故障发生器、截图与答辩材料 | 调用 A 的 API；展示 C 的 Dashboard | 五分钟演示每一步都有清楚画面与备用录屏 |

| 天数 | 当日主目标 | 主责 | 完成判据 |
|---|---|---|---|
| 第 1 天 | 验证集群出网、Envoy Gateway、Redis 存储类和 Prometheus 资源余量 | C | 最小 Pod、Redis PVC 与指标服务均可用 |
| 第 2 天 | 完成一个真实 Adapter 与两个 Mock Provider，配置费用上限 | B | 三个 Provider 都能返回统一格式 |
| 第 3 天 | 打通统一网关、规则路由和决策 JSON | A | 至少一条端到端请求可解释 |
| 第 4 天 | 加精确缓存和极简 UI，完成 P0 并冻结范围 | A / B / C | K8s 中 P0 可演示 |
| 第 5 天 | 完成健康、熔断、fallback 与确定性故障开关 | B | Provider A 故障后切到 B |
| 第 6 天 | 完成 Grafana、限速与每日费用上限，冻结 P1 | C | 录制一次 P1 全流程 |
| 第 7 天 | 选择 Provider CRD 或 HPA 压测强化 | C | P2 二选一有证据；若不稳立即停止 |
| 第 8 天 | 锁定环境，补齐 README、架构图、海报与答辩问答 | D | 演示材料完整 |
| 第 9 天 | 连续彩排至少三次，只修复问题，不扩范围 | D | 五分钟内稳定走完 |
| 第 10 天 | 备份镜像、清单和录屏，完成演示与答辩 | D | 有可恢复的备用方案 |

每日站会只检查三件事：昨天的可运行证据、今天的单一交付物、是否威胁 P0/P1。第 4 天 P0 不稳定时，立即砍掉 P2/P3；第 8-10 天只做排练、材料与缓冲。

## 10. 总结

PolyGate 的本质仍然属于 AI 中转与网关，但它不应以“卖 API 额度”或“统一转发”为故事中心，而应定位为：帮助上层 AI 应用在多个不稳定、价格不同、能力不同的模型服务之间，实现可靠、弹性、低成本和可治理访问的云原生平台。

它所结合的热点不是昂贵的大模型训练，而是 AI Gateway、多云容错、HPA 弹性、Prometheus/Grafana 可观测性、FinOps 和最小治理。这些都能使用普通 CPU 云资源和少量 API 额度完成。

## 参考方向

- Kubernetes: Announcing the AI Gateway Working Group
- Kubernetes: Introducing Gateway API Inference Extension
- OpenTelemetry: GenAI Observability
- KEDA: Kubernetes Event-driven Autoscaling（后续扩展）
- Knative: Configuring Scale to Zero（后续扩展）
