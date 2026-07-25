# PolyGate 管理员策略中心设计

- 日期：2026-07-24
- 状态：团队设计已确认，待实施计划
- 适用版本：Policy Schema v1
- 目标方案：参数化策略中心（A）+ 私有 Policy Editor + Grafana 管理入口

## 1. 背景

PolyGate 当前包含两层动态决策：

1. Gateway 根据质量、隐私、预算、延迟、Provider 健康和能力选择模型。
2. Automation 根据业务场景、紧急程度、等待时间和防饥饿规则决定任务执行顺序。

当前 Provider 的价格、延迟、隐私和能力来自配置文件，但以下规则仍直接写在
Python 中：

- balanced 模式的真实 Provider 价差容忍度；
- 路由前假定的输出 token 数；
- 预算、延迟不满足时是否放宽约束；
- high quality 的 Provider 选择策略；
- urgency 分数；
- scenario 权重和默认偏好；
- waiting bonus 和 starvation 参数。

本设计将这些可调参数从代码中抽离，让后台管理员通过受控页面完成修改、影响预览、
发布和回滚，而不是直接编辑 Python、YAML 或 Kubernetes 清单。

## 2. 目标

第一版必须实现：

- 管理员通过图形化表单修改预定义策略参数；
- 发布前完成 Schema、数值范围和安全护栏校验；
- 发布前展示路由与任务优先级影响；
- 发布后 5 秒内由 Gateway 和 Automation Worker 热加载；
- 发布过程不重启 Gateway 或 Worker；
- 策略和最近 20 个版本在 Pod、Redis 重启后保留；
- 支持版本比较、审计说明和一键回滚；
- Gateway 和 Worker 在控制面不可用时继续使用 Last Known Good；
- Grafana 展示当前策略版本、组件加载版本和发布结果；
- Grafana 提供进入私有 Policy Editor 的管理入口；
- 不破坏现有 OpenAI 兼容 API 和 Web Chat。

## 3. 非目标

第一版明确不实现：

- 任意 IF/THEN 规则语言或可视化规则引擎；
- Provider 增删、Endpoint 修改和 API Key 管理；
- 多管理员账号系统或完整 RBAC；
- 灰度发布、定时发布或按用户分组发布；
- 对已排队任务批量重新评分；
- 公网 Policy Editor；
- iframe 嵌入；
- Grafana App Plugin；
- 多 Worker 并发调度；
- 整个 EKS 集群删除后的自动恢复。

## 4. 设计原则

### 4.1 参数可调，安全规则不可调

管理员可以修改成本、延迟、质量和优先级参数，但不能关闭隐私、能力和认证底线。

### 4.2 策略控制面与请求数据面分离

Automation 兼任 Policy Control Plane。Gateway 和 Worker 只读取策略，不持有
Kubernetes 写权限。

### 4.3 持久化事实来源与快速缓存分离

Kubernetes ConfigMap 是持久化事实来源，Redis DB 0 是快速缓存。Redis 数据丢失
不会导致策略和版本历史丢失。

### 4.4 发布失败不影响现有请求

所有组件在新策略通过完整校验后才原子替换本地策略。任何读取、解析或校验失败都
继续使用 Last Known Good。

### 4.5 所有决策可解释、可审计、可回滚

每个版本记录时间、修改说明、来源版本和发布结果。回滚创建新版本，不改写历史。

## 5. 总体架构

```text
管理员浏览器
    │
    │ port-forward + Bearer POLICY_ADMIN_KEY
    ▼
Policy Editor /admin/policies
    │
    ▼
Automation Policy API
    ├── Policy Schema 与 Guardrail 校验
    ├── Gateway 路由影响模拟
    ├── Automation 优先级影响模拟
    ├── ConfigMap 持久化
    ├── Redis 快速缓存
    └── 当前策略内存快照
             │
             ├── Gateway Pod 1 每 5 秒读取
             ├── Gateway Pod 2 每 5 秒读取
             └── Automation Worker 每 5 秒读取
                       │
                       ▼
                 Last Known Good

Prometheus 抓取 Policy API、Gateway 和 Worker 指标
Grafana 展示策略版本、版本漂移、发布结果和路由影响
Grafana Text Panel 提供 Open Policy Editor 链接
```

### 5.1 组件职责

#### Automation Policy API

- 管理策略生命周期；
- 验证管理员 Bearer Key；
- 读取和更新 Policy ConfigMap；
- 维护最近 20 个版本；
- 维护 Redis 缓存；
- 提供 active policy 只读端点；
- 提供 validate、preview、publish 和 rollback；
- 暴露控制面指标；
- 在 `/admin/policies` 提供 Policy Editor 静态资源。

#### Gateway

- 启动时读取挂载的 ConfigMap；
- 每 5 秒从 Policy API 获取 active policy；
- 校验后原子替换本地策略；
- 使用策略参数执行 Provider 路由；
- 提供不调用 Provider 的内部路由模拟接口；
- 将 policy version 加入缓存键；
- 暴露当前 loaded version 和 reload failure 指标。

#### Automation Worker

- 启动时读取挂载的 ConfigMap；
- 每 5 秒从 Policy API 获取 active policy；
- 使用当前 queue policy 计算 waiting bonus 和 starvation；
- 新任务使用提交时的 urgency/scenario 策略；
- 已排队任务保留提交时的 initial score 和 policy version；
- 暴露当前 loaded version 和 reload failure 指标。

#### Grafana

- 继续作为只读观测平面；
- 展示策略状态与发布效果；
- 提供打开 Policy Editor 的链接；
- 不直接持有写策略权限；
- 不关闭 HTML sanitization，不嵌入 iframe。

## 6. Kubernetes 资源与权限

新增资源：

- ConfigMap：`polygate-routing-policy`
- Secret：`polygate-policy-admin`
- ServiceAccount：`polygate-policy-controller`
- Role：仅允许读取和更新 `polygate-routing-policy`
- RoleBinding：将 Role 绑定给 Automation Deployment

Automation 使用 `polygate-policy-controller` ServiceAccount。Role 不允许访问：

- 其他 ConfigMap；
- 任何 Secret；
- Pod；
- Deployment；
- Node；
- ClusterRole 或其他集群级资源。

Gateway 和 Worker 只读挂载 `polygate-routing-policy`，不获得 Kubernetes API
写权限。

管理员密钥通过 Secret 注入 Automation：

```text
POLICY_ADMIN_KEY_FILE=/var/run/secrets/polygate-policy/admin-key
```

优先使用只读 Secret volume，而不是将密钥写入命令行、ConfigMap 或日志。

## 7. Policy Schema v1

管理员编辑的 PolicyDraft：

```json
{
  "schema_version": 1,
  "gateway": {
    "assumed_output_tokens": 256,
    "balanced_price_tolerance": 0.2,
    "budget_mode": "soft",
    "latency_mode": "soft",
    "high_quality_strategy": "prefer_provider",
    "high_quality_provider": "deepseek-pro",
    "high_quality_fallback": "prefer_real",
    "low_quality_strategy": "prefer_provider",
    "low_quality_provider": "deepseek-flash",
    "low_quality_fallback": "lowest_cost"
  },
  "automation": {
    "urgency_scores": {
      "critical": 100,
      "high": 60,
      "normal": 30,
      "low": 10
    },
    "scenarios": {
      "production_incident": {
        "weight": 40,
        "defaults": {
          "quality": "high",
          "privacy": "high",
          "max_cost_usd": 0.01,
          "latency_target_ms": 1000
        }
      },
      "customer_escalation": {
        "weight": 25,
        "defaults": {
          "quality": "balanced",
          "privacy": "standard",
          "max_cost_usd": 0.01,
          "latency_target_ms": 1500
        }
      },
      "finance_summary": {
        "weight": 15,
        "defaults": {
          "quality": "balanced",
          "privacy": "high",
          "max_cost_usd": 0.005,
          "latency_target_ms": 3000
        }
      },
      "marketing_batch": {
        "weight": 0,
        "defaults": {
          "quality": "cheap",
          "privacy": "standard",
          "max_cost_usd": 0.002,
          "latency_target_ms": 5000
        }
      }
    },
    "queue": {
      "waiting_bonus_interval_seconds": 5,
      "waiting_bonus_points": 1,
      "waiting_bonus_cap": 30,
      "starvation_streak_threshold": 3,
      "starvation_wait_seconds": 20
    }
  }
}
```

### 7.1 Gateway 字段

| 字段 | 语义 | 有效范围 |
|---|---|---|
| `assumed_output_tokens` | 路由前成本估算的输出长度 | 1–32768 |
| `balanced_price_tolerance` | balanced 模式允许真实 Provider 高出的价格比例 | 0–2 |
| `budget_mode` | 无 Provider 满足预算时放宽或拒绝 | `soft` / `hard` |
| `latency_mode` | 无 Provider 满足延迟时放宽或拒绝 | `soft` / `hard` |
| `high_quality_strategy` | high 模式的 Provider 选择方式 | `prefer_real` / `lowest_cost` / `prefer_provider` |
| `high_quality_provider` | high 模式的首选 Provider ID | `prefer_provider` 时必填 |
| `high_quality_fallback` | high 首选不可用时的回退方式 | `prefer_real` / `lowest_cost` |
| `low_quality_strategy` | cheap 模式的 Provider 选择方式 | `lowest_cost` / `prefer_provider` |
| `low_quality_provider` | cheap 模式的首选 Provider ID | `prefer_provider` 时必填 |
| `low_quality_fallback` | cheap 首选不可用时的回退方式 | `prefer_real` / `lowest_cost` |

`quality=balanced` 不读取 high/low Provider 绑定，继续使用
`balanced_price_tolerance`、预算和延迟规则。现有 Policy 使用
`high_quality_strategy=prefer_real` 或 `lowest_cost` 时保持原有行为。

#### 7.1.1 Policy 与 Provider Registry 边界

Policy 只引用 Provider Registry 中稳定的 Provider ID，不保存 endpoint、下游
model、API Key、价格、延迟或能力。Provider Registry 继续作为这些运行时属性的
唯一来源。

默认真实 Provider 注册为两个独立条目：

```yaml
- name: deepseek-pro
  kind: real
  endpoint: https://api.deepseek.com/v1/chat/completions
  api_key_env: REAL_A_API_KEY
  model: deepseek-v4-pro

- name: deepseek-flash
  kind: real
  endpoint: https://api.deepseek.com/v1/chat/completions
  api_key_env: REAL_A_API_KEY
  model: deepseek-v4-flash
```

两者可以共享 endpoint 和凭证，但必须拥有独立 Provider ID、价格、典型延迟、
健康状态、熔断状态和指标标签。管理员不能在 Policy Editor 中编辑 endpoint、
model 或密钥。

发布引用新 Provider 的 Policy 前，必须先部署包含该 Provider 的 Registry。
Validate、Preview 和 Publish 均检查被引用 Provider 存在且 `kind=real`。Gateway
若收到引用不存在 Provider 的新策略，拒绝切换并继续使用 Last Known Good。

#### 7.1.2 运行时决策顺序

Gateway 使用以下固定顺序：

```text
privacy / capability / health 硬过滤
→ budget / latency 约束
→ quality 策略
→ 首选 Provider
→ fallback
```

- `quality=high` 且 `privacy=standard`：首选 `high_quality_provider`；
- `quality=cheap` 且 `privacy=standard`：首选 `low_quality_provider`；
- `quality=balanced`：保持现有成本、延迟和真实 Provider 权衡逻辑；
- `privacy=high`：外部 Provider 在策略选择前即被排除，管理员策略不能覆盖；
- 首选 Provider 因健康、能力、预算或延迟约束不可用时，执行对应 fallback；
- fallback 和 Provider failover 都必须写入可读决策理由。

### 7.2 Automation 字段

可修改：

- urgency 分数；
- scenario 权重；
- scenario 默认质量、隐私、预算和延迟；
- waiting bonus 间隔、分值和上限；
- starvation streak 和等待阈值。

必须满足：

```text
critical > high > normal > low
```

所有分数、权重、时间和金额字段都由 Pydantic 与 JSON Schema 同时限制范围。

### 7.3 不可编辑 Guardrails

以下规则不属于 PolicyDraft 的可写字段，由服务端固定：

```json
{
  "privacy_high_internal_only": true,
  "finance_privacy": "high",
  "require_provider_capability_match": true,
  "reject_unknown_provider": true,
  "admin_auth_required": true
}
```

Policy Editor 可以显示这些值，但必须使用只读锁定控件。

## 8. 版本模型

服务端保存：

```json
{
  "version": 4,
  "status": "active",
  "created_at": "2026-07-24T10:30:00Z",
  "created_by": "policy-admin",
  "change_note": "Increase balanced tolerance for support cases",
  "rollback_from": null,
  "policy": {}
}
```

规则：

- version 是单调递增整数；
- change note 必填；
- ConfigMap 保存 active version 和最近 20 个完整版本；
- 第 21 个版本发布时删除最旧的非 active 历史版本；
- rollback 复制目标版本内容并创建新版本；
- rollback 不改变、删除或复用旧版本号；
- 每个 Automation Job 保存提交时的 policy version。

## 9. API 设计

### 9.1 运行时只读 API

```http
GET /v1/policies/active
```

响应：

```json
{
  "version": 4,
  "schema_version": 1,
  "published_at": "2026-07-24T10:30:00Z",
  "policy": {}
}
```

响应头：

```http
ETag: "policy-v4"
```

Gateway 和 Worker 请求时携带 `If-None-Match`。版本未变化时返回
`304 Not Modified`。

该端点不包含秘密，只能从集群内部访问，因此不要求管理员密钥。

### 9.2 管理 API

```text
GET  /v1/admin/policies
GET  /v1/admin/policies/{version}
GET  /v1/admin/policies/providers
POST /v1/admin/policies/validate
POST /v1/admin/policies/preview
POST /v1/admin/policies/publish
POST /v1/admin/policies/{version}/rollback
```

所有管理 API 要求：

```http
Authorization: Bearer <POLICY_ADMIN_KEY>
```

#### Provider Catalog

`GET /v1/admin/policies/providers` 返回 Policy Editor 可选择的 Provider ID、kind、
model、价格、典型延迟、能力和当前健康状态，不返回 endpoint、API Key 环境变量或
任何秘密。Automation 从 Gateway 的只读内部 Provider Catalog 获取这些数据，
Browser 不直接访问 Gateway。

Gateway 提供：

```http
GET /internal/routing/providers
```

该接口只允许集群内部 Automation 调用，并与 `/internal/routing/simulate` 使用同一
Provider Registry 快照。Provider Catalog 不写缓存、业务指标或策略状态。

#### Validate

验证：

- Schema；
- 未知字段；
- 数值范围；
- urgency 顺序；
- Finance 隐私锁；
- Guardrails；
- queue 参数；
- high/low strategy 与条件必填字段；
- 被引用 Provider 是否存在且为真实 Provider；
- schema version。

Validate 使用 Gateway 当前 Provider Catalog 完成引用检查，但不写入 ConfigMap、
Redis 或内存。Provider Catalog 不可用时返回 503，不能在缺少引用校验的情况下
继续 Preview 或 Publish。

#### Preview

Preview 返回：

- 当前 base version；
- 字段级 before/after diff；
- 校验 warning；
- 路由模拟，包括策略模式、首选 Provider、最终 Provider、模型、成本、延迟与
  fallback 原因；
- 优先级模拟；
- 队列顺序模拟。

路由模拟由 Gateway 提供：

```http
POST /internal/routing/simulate
```

该接口：

- 仅允许集群内部 Automation 调用；
- 使用真实 Provider 配置和当前健康快照；
- 不调用任何 Provider；
- 不产生 DeepSeek 费用；
- 不写缓存和业务指标。

Automation 自己模拟 urgency、scenario 和 queue 参数。

#### Publish

请求：

```json
{
  "base_version": 4,
  "change_note": "Increase balanced tolerance for customer escalations",
  "policy": {}
}
```

流程：

```text
Bearer 认证
→ Schema 与 Guardrail 校验
→ Provider Registry 引用校验
→ base_version 并发检查
→ 最终影响预览
→ 使用 Kubernetes resourceVersion 更新 ConfigMap
→ 原子切换 Automation 内存策略
→ 刷新 Redis 缓存
→ 记录指标与审计日志
→ 返回新版本
```

#### Rollback

```http
POST /v1/admin/policies/2/rollback
```

回滚必须携带当前 base version 和 change note。系统重新校验 v2 内容并创建新的
active version。

## 10. 热更新语义

Gateway 和 Worker：

1. 启动时读取挂载的 ConfigMap；
2. 校验成功后建立初始 Last Known Good；
3. 每 5 秒调用 `/v1/policies/active`；
4. 收到 304 时不处理；
5. 收到新版本时先完整校验；
6. 在进程内原子替换策略引用；
7. 暴露 loaded version；
8. 拉取或校验失败时继续使用旧版本。

目标是新策略在正常网络情况下 5 秒内生效。

### 10.1 已排队任务

- Job 创建时保存 `initial_score` 与 `policy_version`；
- 已排队任务不因后续策略发布而重新评分；
- 新任务使用新版本的 urgency score 和 scenario weight；
- waiting bonus、streak、starvation 等运行参数可以热更新；
- 运行中的任务不被抢占。

## 11. 缓存一致性

Gateway 缓存键必须加入 active policy version：

```text
hash(messages + request constraints + policy_version)
```

因此：

- v4 缓存不能被 v5 请求命中；
- 不需要发布时清空整个 Redis；
- 旧缓存等待现有 TTL 自然过期；
- rollback 创建 v6，不会错误复用 v4 或 v5 缓存。

## 12. Policy Editor

地址：

```text
http://localhost:8020/admin/policies
```

访问方式：

```bash
kubectl port-forward service/automation 8020:8020
```

Task 7 当前交付边界以已经合并的 `contracts/policy.schema.json`、
`contracts/policy-examples.json` 和 Automation Policy API 为准。本文其他章节描述的
`prefer_provider`、Provider Catalog 与 high/low Provider 绑定属于后续策略演进；在
Schema、examples、Gateway runtime 和 Automation API 全部实现并重新交接前，不进入
本轮 Editor。前端不能调用尚不存在的 `/v1/admin/policies/providers`。

当前前端实现采用自托管 Alpine CSP build 与原生 HTML/CSS/JavaScript。它作为
Automation 镜像内静态资源交付，不使用 Node build/runtime、CDN、外部字体或公网
资源。页面、静态资源和 API 同源；`/admin/*` 使用 self-only CSP（不含
`unsafe-eval`/`unsafe-inline`）和 `Cache-Control: no-store`。详细实施决策见
`document/PolyGate_Policy_Editor轻量前端与云部署实施方案.md`。

页面包含：

- 管理员密钥输入；
- active version 和系统状态；
- Gateway Routing 参数；
- urgency scores；
- scenario weights 和 defaults；
- queue 参数；
- 只读 Guardrails；
- 字段级错误；
- before/after diff；
- routing/priority/queue impact preview；
- change note；
- Validate、Preview、Publish；
- 版本历史、比较和 Rollback。

密钥要求：

- 只保存在页面内存；
- 不写 URL；
- 不写 localStorage；
- 不进入日志；
- 页面刷新后清除。

交互要求：

- 未通过 Validate 时不能 Preview；
- 未完成 Preview 时不能 Publish；
- change note 为空时不能 Publish；
- 发布前显示最终 diff；
- 409 时要求重新加载；
- Guardrails 使用锁定控件；
- API 错误必须映射到具体字段。

等 Provider Catalog 相关契约真正进入 `main` 后，再单独增加 Provider 下拉框、条件
必填字段和 fallback 预览；该扩展不得阻塞 Task 7。

## 13. 决策卡片兼容

建议增加可选字段：

```json
{
  "policy_version": 5,
  "routing_strategy": "prefer_provider",
  "preferred_provider": "deepseek-pro"
}
```

`decision-card.schema.json` 明确要求 A 与 D 共同确认变更。因此该字段只有在 A/D
完成契约对齐后才能加入。

如果未获确认，第一版使用：

```http
X-PolyGate-Policy-Version: 5
```

现有决策卡片结构保持不变，Grafana 仍显示策略版本。
在可选字段尚未完成契约对齐时，现有 `chosen_provider` 表示最终 Provider，
`reason` 必须包含策略模式、首选 Provider 和 fallback 原因。

## 14. 可观测性

新增指标：

```text
polygate_policy_active_version
polygate_policy_loaded_version
polygate_policy_publications_total
polygate_policy_reload_failures_total
polygate_policy_last_publish_timestamp_seconds
```

指标职责：

- Policy API 暴露 active version、publication outcomes 和 last publish time；
- 每个 Gateway Pod 暴露 loaded version 与 reload failures；
- Worker 暴露 loaded version 与 reload failures。

Grafana 新增 Policy Management 区域：

- Active Policy Version；
- Gateway Loaded Version；
- Worker Loaded Version；
- Policy Publication Outcomes；
- Policy Reload Failures；
- Last Policy Publication；
- Open Policy Editor。

如果 active version 与任一 loaded version 不一致超过 30 秒，面板进入告警颜色。

Grafana继续使用默认 HTML sanitization。Editor 不使用 iframe，Text Panel 仅提供
链接。

## 15. 故障处理

| 故障 | 行为 |
|---|---|
| 管理密钥缺失或错误 | 返回 401 |
| Schema/Guardrail 非法 | 返回 422，不写任何状态 |
| base version 过期 | 返回 409 |
| ConfigMap 写失败 | 发布失败，旧策略继续生效 |
| Redis 刷新失败 | 新策略生效，标记 cache degraded 并重试 |
| Policy API 不可用 | Gateway/Worker 使用 Last Known Good |
| Gateway Provider Catalog 不可用 | Validate/Preview/Publish 返回 503，旧策略继续生效 |
| 客户端收到非法策略 | 拒绝切换，增加 reload failure |
| Policy 引用未知或非真实 Provider | Validate/Publish 返回 422，不激活 |
| 首选 Provider 被隐私规则排除 | 执行 fallback，隐私 Guardrail 不可覆盖 |
| 首选 Provider 不健康或不满足能力 | 执行 fallback，并记录原因 |
| Automation 重启 | 从 ConfigMap 恢复 active version 与历史 |
| Redis Pod 重建 | 从 ConfigMap 重建策略缓存 |
| Gateway/Worker 重启 | 从挂载 ConfigMap 建立初始策略 |
| 组件版本漂移 | Grafana 显示版本不一致 |

ConfigMap 能抵抗 Pod 和 Redis 重启，但删除整个 EKS 集群会删除 ConfigMap。仓库中的
默认 v1 policy 用于新集群 bootstrap。

## 16. 测试策略

### 16.1 契约测试

- 默认策略符合 Schema；
- examples 能被 Pydantic 解析；
- 未知字段和越界值被拒绝；
- urgency 顺序错误被拒绝；
- Finance 隐私修改被拒绝；
- `prefer_provider` 缺少 Provider ID 时被拒绝；
- 未知或非真实 Provider 引用被拒绝；
- Guardrails 不能关闭。

### 16.2 Policy API 测试

- 管理认证；
- validate 无副作用；
- preview 不调用 Provider；
- publish 递增版本；
- 409 并发冲突；
- ConfigMap 失败不激活；
- Redis 失败进入 degraded；
- 历史最多 20 个版本；
- rollback 创建新版本；
- ConfigMap 重启恢复；
- Provider Catalog 不可用时禁止发布；

### 16.3 Gateway 测试

- 5 秒刷新；
- soft/hard budget；
- soft/hard latency；
- balanced tolerance；
- high quality strategy：standard privacy 首选 `deepseek-pro`；
- low quality strategy：standard privacy 首选 `deepseek-flash`；
- balanced 策略行为保持不变；
- high privacy 不选择任何外部 DeepSeek Provider；
- 首选 Provider 不健康、能力不符或被约束排除时执行 fallback；
- Adapter 向下游发送 Registry 中准确的 model；
- dry-run 不调用 Provider；
- 缓存按 policy version 隔离；
- Policy API 故障时 Last Known Good；
- loaded version 指标。

### 16.4 Worker 测试

- 新任务使用当前策略；
- 旧任务保留 score/version；
- queue 参数热更新；
- Last Known Good；
- loaded version 指标；
- 单 Worker 行为不变。

### 16.5 EKS 端到端测试

```text
读取 v1
→ Preview draft
→ Publish v2
→ 等待所有组件加载
→ 验证 Provider 选择变化
→ 验证缓存不跨版本命中
→ 验证 Grafana 版本与成本变化
→ Rollback 并生成 v3
→ 验证路由恢复
→ 重启 Automation
→ 验证 v3 与历史仍存在
```

## 17. 演示流程

1. Grafana 显示 active v1，两个 Gateway Pod 与 Worker 均为 v1。
2. 使用 `quality=high`、`privacy=standard` 发送请求。
3. 默认 high 策略选择 `deepseek-pro`，再以 `quality=cheap` 验证
   `deepseek-flash`。
4. 从 Grafana 打开 Policy Editor。
5. 将 `high_quality_strategy` 修改为 `lowest_cost`。
6. Preview 显示 `deepseek-pro` → 最低成本候选，以及成本和延迟变化。
7. 输入 change note，发布 v2。
8. 5 秒内 Grafana 显示所有组件加载 v2。
9. 使用不同 nonce 发送相同约束请求，验证选择变更。
10. 展示策略模式、首选 Provider、最终 Provider、成本、延迟和策略版本。
11. 回滚 v1，系统创建 v3。
12. 再次请求，high 路由恢复为 `deepseek-pro`。

## 18. 四人分工

### A：Gateway Policy Runtime

- 参数化 `router.py`；
- 为 `deepseek-pro` 与 `deepseek-flash` 建立独立 Provider；
- 实现 high/low preferred Provider 与 fallback；
- Gateway Policy Client；
- Last Known Good；
- `/internal/routing/simulate`；
- `/internal/routing/providers`；
- cache key 加入 policy version；
- policy version 响应头；
- 与 D 对齐决策卡片可选字段；
- Gateway 单元与集成测试。

### B：Automation Policy Control Plane

- Policy Pydantic models；
- ConfigMap Policy Repository；
- Redis 缓存；
- Gateway Provider Catalog 代理与引用校验；
- validate、preview、publish、history、rollback；
- 管理员 Bearer 认证；
- Worker 动态 queue policy；
- Job 保存 policy version；
- Policy API 与 Worker 测试。

### C：Kubernetes 与可观测性

- 默认 Policy ConfigMap；
- Provider ConfigMap 注册 Pro 与 Flash，并保证先于引用它们的 Policy 发布；
- Policy Admin Secret；
- ServiceAccount、Role、RoleBinding；
- ConfigMap/Secret volume；
- Deployment 环境变量、探针和资源；
- Prometheus 指标接入；
- Grafana Policy Management；
- EKS policy smoke test；
- 热更新、重启恢复和版本漂移验证；
- Runbook 与演示证据。

### D：Policy Editor

- `/admin/policies` 页面；
- 管理员密钥输入；
- 分组表单；
- high/low strategy、Provider 与 fallback 控件；
- 锁定 Guardrails；
- Validate、Preview、Publish；
- diff、history、compare、rollback；
- API 错误状态；
- 与 A 对齐可选 policy version；
- 前端测试和生产构建。

## 19. 并行实施顺序

第一批先冻结：

1. Policy JSON Schema；
2. API request/response examples；
3. 默认 v1 policy；
4. 指标名称；
5. decision-card 可选字段结论。

然后并行：

```text
A：Gateway Policy Runtime
B：Policy API + Worker
C：Kubernetes + Monitoring Skeleton
D：基于 examples 开发 Policy Editor
```

最后由 C 在 EKS 完成统一接线。

## 20. 完成判据

- 管理员无需编辑代码或 YAML；
- 发布前能看到路由和队列影响；
- 新策略 5 秒内生效；
- Gateway/Worker 不因发布重启；
- 两个 Gateway Pod 与 Worker 收敛到同一版本；
- Redis/Pod 重启不丢策略和历史；
- Guardrails 无法关闭；
- high/cheap 分别首选 Pro/Flash，balanced 行为保持不变；
- `privacy=high` 无法被 Provider 偏好覆盖；
- 未知 Provider 引用不能发布或热加载；
- cache key 按策略版本隔离；
- rollback 可审计；
- Grafana 展示版本、漂移和发布影响；
- 现有 Web 与 OpenAI 兼容接口保持可用；
- 本地测试、契约测试、Kubernetes preflight 和 EKS smoke test 全部通过。
