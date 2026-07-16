# PolyGate：面向多模型 AI 应用的云原生智能网关

## 1. 项目定位

PolyGate 不是单纯转发大模型 API 的“AI 中转站”，而是一个面向实际 AI 应用的**云原生模型访问与治理平台**。

用户仍然通过统一接口调用 AI，但平台会根据任务类型、服务状态、延迟、价格、隐私等级和预算，在多个模型提供方之间自动选择、切换和扩缩容，并将全过程可视化。

一句话概括：

> 普通中转站解决“如何统一访问多个模型”，PolyGate 进一步解决“在复杂云环境中，如何可靠、经济、可观测地使用多个模型”。

---

## 2. 普通 AI 中转站的局限

常见 AI 中转站通常提供以下能力：

- 统一 API 地址和鉴权；
- 转发 OpenAI 兼容请求；
- 管理多个 API Key；
- 统计 token 和余额；
- 在某个服务不可用时手动更换渠道。

这些功能很实用，但系统通常并不真正理解请求和运行状态：所有请求可能固定转发到同一渠道，故障后才被动切换，重复请求仍会重复付费，也缺少完整的云端监控和弹性机制。

---

## 3. PolyGate 相比普通中转站的优势

| 能力 | 普通 AI 中转站 | PolyGate |
|---|---|---|
| 模型选择 | 用户手动指定或固定映射 | 根据任务、质量、延迟、价格和预算自动选择 |
| 故障处理 | 请求失败后返回错误或简单重试 | 健康检查、熔断、自动降级和跨提供方切换 |
| 成本控制 | 事后统计消费 | 请求前预算判断、低成本路由、缓存复用和配额限制 |
| 性能优化 | 基本转发 | 并发控制、负载均衡、语义缓存和请求队列 |
| 弹性伸缩 | 网关固定副本 | 根据请求量或队列长度自动扩缩容，闲置组件可缩容到零 |
| 可观测性 | 简单调用日志 | 端到端 trace、模型延迟、token、错误率、费用和路由原因 |
| 治理能力 | 主要管理 API Key | 租户隔离、权限、隐私策略、速率限制和审计 |
| 用户体验 | “把请求转出去” | “在约束条件下选择当前最合适的 AI 服务” |

PolyGate 的价值不在于比中转站多堆几个功能，而在于把一次 AI 请求视为一个需要被云平台管理的工作负载。

---

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
- Local Small Model：免费、隐私好，但能力有限；
- Cached Result：已有相似问题的结果。

最终界面不仅显示答案，还显示：

- 选择了哪个模型；
- 为什么选择它；
- 是否发生缓存命中；
- 请求延迟和费用；
- 是否发生重试或故障切换。

这样，应用本身负责提供直观体验，云计算能力在后台真实改善可靠性、成本和性能。

---

## 5. 系统架构

```text
                    ┌──────────────────────┐
                    │   AI Application UI  │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │ Gateway API / Router │
                    └──────────┬───────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
 ┌────────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
 │ Policy Engine   │  │ Semantic Cache  │  │ Provider Health │
 │ 成本/隐私/配额   │  │ 相似请求复用     │  │ 探测/熔断/恢复   │
 └────────┬────────┘  └─────────────────┘  └─────────────────┘
          │
 ┌────────▼──────────────────────────────────────────┐
 │ Provider Adapters                                 │
 │ Real API A · Real API B · Local/Mock Provider     │
 └───────────────────────────────────────────────────┘

     Metrics / Logs / Traces → OpenTelemetry → Dashboard
     Request Queue           → KEDA / HPA    → Autoscaling
```

建议只接入一到两个真实 API，其余提供方使用本地小模型或可控制延迟、价格和故障的 Mock Provider。这样既能演示真实调用，也不依赖昂贵 GPU。

---

## 6. 结合的云计算前沿话题

### 6.1 AI Gateway 与模型感知路由

传统 API Gateway 主要根据 URL、Header 和权重路由；AI Gateway 开始进一步考虑模型、请求优先级、服务负载和推理特征。Kubernetes 社区已经成立 AI Gateway Working Group，Gateway API Inference Extension 也在推进模型感知路由和 AI 流量标准化。

PolyGate 将这一方向简化为课程项目可实现的版本：根据价格、健康状态、任务类别和用户约束动态路由。

### 6.2 多云与供应商容错

模型 API 天然具有多供应商特征。PolyGate 把不同模型服务视为多个云后端，通过健康检查、熔断、重试和故障切换降低单一供应商故障、限流或价格变化带来的影响。

### 6.3 Serverless 与事件驱动弹性

批量摘要、文档处理等任务可以进入消息队列，由 Worker 异步处理。KEDA 可以根据待处理消息数量扩缩 Worker；Knative 还可以让低频组件在闲置时缩容到零。

这使“自动扩缩容”不再只是写一个 HPA YAML，而是直接服务于突发 AI 请求和成本节约。

### 6.4 AI 可观测性

OpenTelemetry 正在标准化 GenAI 调用中的模型名称、token 数量、延迟、工具调用和 trace 等信息。PolyGate 可以统一采集：

- 每个 Provider 的成功率和 p95 延迟；
- 输入、输出 token；
- 每次请求费用；
- 缓存命中率；
- 路由和故障切换链路；
- 不同租户的额度使用情况。

### 6.5 FinOps 与成本感知计算

传统云平台关注 CPU、存储和网络成本；AI 应用又增加了按 token 计费。PolyGate 可以将预算作为一等约束，在满足基本质量和延迟的情况下优先选择更便宜的服务，并通过缓存和配额避免重复支出。

### 6.6 云原生治理与多租户

系统可以为不同用户或团队设置：

- 每分钟请求限制；
- 每日费用上限；
- 可使用的模型列表；
- 敏感数据禁止发送到外部 Provider；
- 完整请求审计记录。

这使项目从个人 AI 工具上升为一个可供组织使用的云平台。

---

## 7. 演示设计

一次完整演示可以控制在五分钟内：

1. 用户提交一个带预算和延迟要求的摘要任务；
2. Dashboard 展示多个 Provider 的价格、延迟和健康状态；
3. PolyGate 自动选择合适模型并说明原因；
4. 再次提交相似请求，展示语义缓存命中和费用节省；
5. 人为让当前 Provider 返回错误或增加延迟；
6. 系统触发熔断并自动切换 Provider；
7. 使用负载生成器制造请求突发，展示队列增长和 Worker 自动扩容；
8. 最后展示费用、延迟、错误率和完整 trace。

评委看到的不是若干孤立 Kubernetes 功能，而是一条统一故事：

> 请求增加时系统自动扩容，服务异常时自动切换，重复请求通过缓存节约费用，所有决策都可以被观察和解释。

---

## 8. 推荐实现范围

课程项目的合理范围是：

- 一个完整 AI Web 应用；
- 一个统一 OpenAI-compatible Gateway；
- 两个真实或模拟 Provider；
- 基于规则的动态路由；
- Redis 语义或精确缓存；
- 健康检查、重试、熔断和 fallback；
- Prometheus/OpenTelemetry 可观测性；
- KEDA 或 HPA 弹性伸缩；
- Kubernetes 部署和漂亮 Dashboard。

不必自行训练模型，也不必实现复杂机器学习路由算法。项目的竞争力主要来自完整性、真实运行效果和清楚的云端故事。

---

## 9. 总结

PolyGate 的本质仍然属于 AI 中转与网关，但它不应以“卖 API 额度”或“统一转发”为故事中心，而应定位为：

> **帮助上层 AI 应用在多个不稳定、价格不同、能力不同的模型服务之间，实现可靠、弹性、低成本和可治理访问的云原生平台。**

它所结合的热点不是昂贵的大模型训练，而是 AI Gateway、多云容错、Serverless、事件驱动扩缩容、OpenTelemetry AI 可观测性、FinOps 和多租户治理。这些都能使用普通 CPU 云资源和少量 API 额度完成。

---

## 参考方向

- [Kubernetes: Announcing the AI Gateway Working Group](https://kubernetes.io/blog/2026/03/09/announcing-ai-gateway-wg/)
- [Kubernetes: Introducing Gateway API Inference Extension](https://kubernetes.io/blog/2025/06/05/introducing-gateway-api-inference-extension/)
- [OpenTelemetry: GenAI Observability](https://opentelemetry.io/blog/2026/genai-observability/)
- [KEDA: Kubernetes Event-driven Autoscaling](https://keda.sh/)
- [Knative: Configuring Scale to Zero](https://knative.dev/docs/serving/autoscaling/scale-to-zero/)
