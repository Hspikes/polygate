# PolyGate 进阶云原生特性建议（SOTA Roadmap）

> 面向已完成 P0 + 部分 P1/P2 的 PolyGate，讨论如何加入更复杂、更前沿的云计算特性。
> 核心结论：**Kubernetes Operator（Provider CRD + 控制器）是最该做、且在 Learner Lab 上风险最低的那一个。**

## 1. 现状定位

PolyGate 目前是一个**规则路由的云原生 AI 网关**，已经跑到 P0 + 部分 P1/P2：

- 网关（FastAPI，OpenAI 兼容）+ 规则路由（隐私 / 健康 / 预算 / 延迟 / 质量）—— `gateway/app/router.py`
- 1 个真实 Provider + 2 个 Mock Provider（可注入故障 `/admin/config`），Redis 精确缓存，决策卡片
- 已上 EKS：Deployment + NodePort Service + **CPU HPA（autoscaling/v2）** + ConfigMap 装 Provider 列表 + Secret 装 API key
- Prometheus + Grafana + kube-state-metrics + 监控 API，带 provisioned 大屏

### 关键约束（决定了什么该做、什么别碰）

- Learner Lab 临时凭证 **4 小时过期**；
- **IAM 受限**：EBS CSI Driver 因 Pod Identity 角色关联不上被迫放弃，Redis 退化成 `emptyDir`；
- NodePort 每个新 session 都要手动挂安全组。

这个约束直接决定了哪些前沿特性适合做 —— 见第 2 节。

---

## 2. Operator：最该做、风险最低

proposal 的 P2 已经写了 "Provider CRD"（`document/PolyGate_按原文风格修订版.md` 第 173 行）。把它做成**最漂亮的 operator 版本**，有一个别人想不到的理由：

> **Operator 只依赖 Kubernetes 集群内 RBAC，完全不碰 AWS IAM。**
> 杀死 EBS CSI 的那套 Pod Identity / IAM 角色问题，在这里根本不存在。
> 这是 Learner Lab 上唯一能"稳稳做出来的 SOTA 特性"。

### 2.1 自定义资源 `kind: Provider`

把 `deploy/gateway.yaml` 里那段静态 ConfigMap 升级成一等 Kubernetes 对象：

```yaml
apiVersion: polygate.io/v1
kind: Provider
metadata: { name: mock-c }
spec:
  kind: mock
  endpoint: http://mock-c:8080/v1/chat/completions
  model: mock-cheap
  pricePer1kInput: 0.00005
  privacy: internal
  typicalLatencyMs: 900
status:                    # ← 由 controller 回写，不是人写的
  health: healthy
  observedLatencyMs: 870
  lastProbeTime: "2026-07-20T..."
  phase: Ready
```

### 2.2 控制器（reconcile 循环）

建议用 **Kopf**（Kubernetes Operator Pythonic Framework）—— 整套栈都是 Python/FastAPI，
不必为了 operator 去学 Go / Kubebuilder。控制器做两件事：

1. **watch** `Provider` 对象增删改 → 重新渲染 `providers-config` ConfigMap（或直接写 Redis）
   → 触发网关热加载。`kubectl apply -f mock-c.yaml` 一敲，新 Provider 立刻出现在 dashboard 上。
2. **timer** 定期探活每个 Provider，把健康与实测延迟**回写到 CRD 的 status 子资源**。

第 2 点直接干掉现有代码里那个悬着的 TODO —— `gateway/app/main.py:39-40` 的静态 `HEALTH`
映射（注释写着"B 在 P1 用真实探针替换"）。改成 operator 探活 → 写 `Provider.status.health`
→ 网关读它。`kubectl get providers` 会像内建资源一样带出 HEALTH 列。

### 2.3 集成方式（保持网关可脱离集群运行）

为了不破坏"A/B/D 只用本地 compose 开发"的铁律，推荐：

- **本地 compose**：网关照旧读 `contracts/providers.yaml` / Redis 种子数据；
- **集群内**：operator 把 CRD 渲染进 `providers-config` ConfigMap，网关热加载或 `rollout restart`。

网关本身不直接耦合 k8s API，仍可在 compose 里独立跑。

### 2.4 答辩叙事（分数所在）

Operator 体现的是 Kubernetes 的核心思想：**声明式期望状态 + 控制循环持续收敛
（level-based reconciliation）**。这不是"在 K8s 上跑了个应用"，而是**用 CRD 扩展了
Kubernetes API 本身**，把"AI Provider"变成集群的一等公民 —— 正是 proposal 6.1 引用的
"AI Gateway Working Group / Gateway API" 那条线的落地，是从"会用 K8s"到"把 K8s 当平台
去扩展"的跨越。

- **成本**：中等（一个 CRD YAML + 约 150 行 Kopf 控制器 + RBAC）。
- **风险**：低。

---

## 3. 其余 SOTA 选项（按 ROI 排序）

| 特性 | 怎么接到 PolyGate | Demo 亮点 | 工作量 | Learner Lab 风险 |
|---|---|---|---|---|
| **KEDA 事件驱动扩缩** | 把 `deploy/hpa.yaml` 的 CPU HPA 换/补成 KEDA `ScaledObject`，**基于已有 Prometheus 指标**（请求速率 / p95 延迟 / cache-miss 率）扩缩。再加一条 Redis 队列 + worker 的批量分类异步链路，KEDA 按队列长度**缩容到零** | "不是按 CPU，而是按真实 AI 负载信号扩缩"；worker 空闲时归零 | 中-高 | 中（要装 KEDA；异步链路属 P3 新架构） |
| **OpenTelemetry GenAI 追踪** | 网关埋 OTel span（gateway→router→provider），用 GenAI 语义约定带上 model / tokens / cost；接 Tempo / Jaeger | 把"用 request_id 拼日志"升级成真正的分布式 trace 瀑布图 | 中 | 低 |
| **Gateway API + Inference Extension** | 把 NodePort 换成 Envoy Gateway（Gateway API），可选上 Inference Extension 做模型感知路由 | proposal 6.1 引用的那个 WG 的**原样落地**，引用可信度最高 | 高 | 中-高（组件重，易卡） |
| **渐进式发布（Argo Rollouts / Flagger）** | 金丝雀发布新版网关，按 Prometheus 错误率自动回滚 | "发新版本 → 5% 流量 → 错误率超标自动回滚" | 中 | 中 |
| **策略即代码（Kyverno）** | 强制"privacy=external 的 Provider 不得进高隐私命名空间"、强制 Pod 必带 probes / limits | 呼应 proposal 6.6 治理章节，纯声明式 | 低-中 | 低 |
| **GitOps（Argo CD）** | 从 repo 声明式部署 + 自愈 | 手删 Pod / 改副本 → 自动被拉回期望态 | 中 | 低-中 |

---

## 4. 推荐组合

给定时间盘与 Learner Lab 约束，做这个组合最划算：

1. **主线：Provider Operator（Kopf + CRD + 探活回写 status）**
   —— 风险最低、正好在路线图上、叙事最强，还顺手解决 `HEALTH` 静态映射的 TODO。
2. **加分：KEDA 基于自有 Prometheus 指标扩缩**
   —— 复用已搭好的监控，把"CPU HPA"升级成"按 AI 工作负载信号扩缩"，几乎不新增基础设施。
3. **观测升级（便宜且引用可信）：OpenTelemetry 追踪**。

三者叠起来的故事：**声明式管理 Provider（operator）→ 按真实负载弹性伸缩（KEDA）
→ 全链路可追踪（OTel）**，正好覆盖 proposal 第 6 章引用的三条前沿线
（AI Gateway / 弹性 / GenAI 可观测性），且每一条都能落到现有代码上。

---

## 5. 下一步

优先搭 **Provider Operator** 脚手架：

- `polygate.io/v1 Provider` CRD；
- Kopf 控制器（watch 渲染 ConfigMap + timer 探活回写 status）；
- RBAC；
- 一份 `kubectl apply` 就能演示的 demo Provider。

可选：先出设计文档（架构图 + reconcile 流程 + 集成点 + 分工），团队评审后再写码，
符合"先冻结契约再开发"的纪律。
