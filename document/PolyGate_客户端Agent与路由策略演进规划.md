# PolyGate 客户端、Agent 接入与路由策略演进规划

> 状态：设计建议稿
> 日期：2026-07-22
> 适用基础：当前 `main` 上已经跑通的 P0 网关、Web、Redis、Mock Provider、Prometheus/Grafana 与 Kubernetes 部署

> Web 多轮聊天的已确认技术选型、Markdown 安全策略和云端部署细节，见
> [`PolyGate_Web多轮聊天改造与云端部署方案.md`](./PolyGate_Web多轮聊天改造与云端部署方案.md)。

## 1. 目标与核心判断

PolyGate 当前已经能够完成单轮文本请求、规则路由、精确缓存、成本估算和决策卡展示。下一阶段不应只是继续增加若干路由评分字段，而应同时完成两次升级：

1. 从单轮 Prompt Demo 升级为可实际连续使用的聊天与 Agent 模型访问 API；
2. 从“选一个 Provider”的规则函数升级为“策略控制面 + 路由计划 + 执行状态”的智能网关。

推荐的总体判断如下：

- Web 多轮聊天采用客户端维护上下文，Gateway 继续保持无状态；
- pi 等 Agent 框架位于 PolyGate 上层，由 Agent 管理 session 和执行工具；
- PolyGate 不执行本地工具，只负责模型请求兼容、能力过滤、路由、可靠性和治理；
- 路由创新重点放在版本化 Policy、执行策略、会话黏性和策略预演，而不是堆叠更多权重；
- 在动态路由之前，必须先补齐实时健康、重试、熔断、fallback 和 Provider 能力描述。

## 2. 当前实现基线

### 2.1 已具备的能力

- `POST /v1/chat/completions` 接受 `messages[]`；
- `model=auto` 自动路由，也可强制指定 Provider；
- 根据隐私、健康、预算、延迟和质量进行规则过滤；
- Redis 精确/轻规范化缓存；
- 记录 Provider、成本、延迟、token、缓存和请求结果指标；
- Web 展示回答和决策卡；
- Kubernetes、HPA、Prometheus、Grafana 和 Mock 故障注入链路已经存在。

### 2.2 当前主要缺口

客户端方面：

- `web/index.html` 每次只发送一条 user message；
- 没有会话列表、新建会话、重试和刷新后恢复；
- 没有上下文窗口预算和截断/压缩策略；
- 当前页面每次只渲染一个回答，而不是完整对话流。

API 与 Agent 兼容方面：

- Message 只允许 `system/user/assistant`，`content` 只能是字符串；
- 不支持 `developer`、`tool`、`tool_calls`、`tool_call_id` 和多模态 content block；
- Adapter 只向上游传递 `model + messages`；
- `tools`、`tool_choice`、`temperature`、`max_tokens`、`response_format`、`stream` 等字段会丢失；
- 不支持 SSE 流式代理；
- Gateway 固定返回 `finish_reason=stop`，没有完整保留上游响应；
- Gateway 的最终响应缺少标准 `usage`；
- Provider 调用是同步 HTTP，请求较长时会占用同步 worker；
- 没有 Gateway API Key、租户限流和每日预算。

路由方面：

- `HEALTH` 仍是静态空表，未知状态默认健康；
- 延迟判断依赖注册表里的 `typical_latency_ms`；
- 预算不足时会自动放宽预算，因此 `max_cost_usd` 不是严格上限；
- `quality=high` 目前近似等于优先 `kind=real`，并不代表经过验证的任务质量；
- Provider 失败后直接返回 502，尚无重试、熔断和 fallback；
- Prometheus 已记录实际延迟和成功率，但这些状态还没有反馈给路由器；
- 返回结果只表示“选中了谁”，没有表达 fallback、hedge、升级等执行计划。

## 3. 推荐目标架构

```text
Web Chat                         pi / Other Agent
  ├─ conversation state           ├─ session / compaction
  ├─ local persistence            ├─ tool loop
  └─ decision-card UI             └─ workspace and tool permissions
              │                            │
              └──────── OpenAI-compatible API ────────┐
                                                       ▼
                                              PolyGate Gateway
                                      ┌─────────────────────────┐
                                      │ API compatibility layer │
                                      │ Policy resolver         │
                                      │ Capability hard gates   │
                                      │ Route planner           │
                                      │ Execution/failover      │
                                      │ Decision/audit record   │
                                      └─────────────────────────┘
                                                │
                          ┌─────────────────────┼─────────────────────┐
                          ▼                     ▼                     ▼
                     Provider A           Provider B           Local/Mock

Control plane: Provider registry + versioned policies + tenant quotas
Runtime state: Redis health/breaker/EWMA/session affinity/budget ledger
Observability: Prometheus + Grafana + structured logs + decision lookup
```

边界必须保持清楚：

- Agent 工具在 pi 一侧执行；
- PolyGate 不获得用户工作区的 shell 或文件权限；
- PolyGate 只看到 Agent 发出的模型消息和工具结果；
- Provider 密钥只保存在 PolyGate；
- pi 只持有 PolyGate API Key。

## 4. 模块一：Web 多轮聊天

### 4.1 状态模型

第一阶段让浏览器维护如下状态：

```text
Conversation
  id
  title
  created_at / updated_at
  messages[]
  routing_policy
  privacy
  max_cost_usd
  latency_target_ms
```

每个 assistant message 附带当轮的 `request_id` 和决策卡。发送过程为：

1. 将新 user message 追加到本地 conversation；
2. 将当前有效上下文完整提交到 Gateway；
3. 成功后追加 assistant message 与决策卡；
4. 失败时保留 user message，并提供“重试本轮”；
5. 防止发送按钮重复点击造成两次并发请求。

### 4.2 持久化选择

MVP 使用 `localStorage` 即可：

- 优点：不需要账号系统和服务端 session；
- 优点：不会把完整聊天记录额外保存到 Gateway；
- 缺点：不能跨设备同步；
- 缺点：浏览器数据被清理后会丢失。

如果后续需要多用户和跨设备，再新增独立的 `/v1/conversations` 资源。不要让 `/v1/chat/completions` 隐式写入会话，否则会破坏无状态调用、请求重试和第三方客户端兼容性。

### 4.3 上下文预算

上下文治理至少包含：

- 保留 system/developer 指令；
- 为本轮输出预留 token；
- 优先保留最近若干轮；
- 超限时返回明确错误或截断提示，不能静默丢弃关键指令；
- 第二阶段再加入摘要压缩，并标记摘要覆盖的消息范围；
- Provider 切换时按新 Provider 的 context window 重新计算。

粗略字符数除以 4 只能用于 Demo，不应作为严格的 context 判定。正式实现应支持按模型配置 tokenizer；短期内可以采用保守估计并留出安全余量。

### 4.4 缓存策略

普通纯文本多轮聊天可继续用“完整有效上下文 + 路由约束”作为缓存 key，但命中率通常会低于单轮请求。

以下请求默认绕过缓存：

- 包含 `tools`；
- 包含 `tool` message；
- assistant message 中包含 `tool_calls`；
- 具有潜在副作用的 Agent 调用；
- 非确定性参数不一致；
- 请求明确指定 `cache_control=no-store`。

不能缓存并复用旧的 tool call ID，也不能让缓存结果触发一次已过期的工具动作。

### 4.5 Web MVP 验收

- 能连续完成至少 5 轮对话；
- 用户用“它”“上一条”等指代时，模型能看到之前内容；
- 刷新页面后能恢复本地会话；
- 可以新建、切换、重命名和删除会话；
- 每轮回答显示自己的 Provider、成本、延迟和 request ID；
- 修改 routing policy 后只影响后续轮次；
- 上下文过长时有可理解的提示或压缩记录。

## 5. 模块二：pi / Agent 接入

### 5.1 推荐方式

推荐把 PolyGate 注册为 pi 的自定义 OpenAI-compatible Provider：

```json
{
  "providers": {
    "polygate": {
      "baseUrl": "https://gateway.example.com/v1",
      "api": "openai-completions",
      "apiKey": "$POLYGATE_API_KEY",
      "models": [
        {
          "id": "auto",
          "name": "PolyGate Auto",
          "contextWindow": 64000,
          "maxTokens": 8192
        }
      ]
    }
  }
}
```

参考资料：

- <https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md>
- <https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md>
- <https://github.com/earendil-works/pi/blob/main/packages/ai/README.md>

### 5.2 最小兼容面

请求至少支持：

- `model`；
- `messages`，包括 `developer/user/assistant/tool`；
- 字符串和 content block；
- `tools`、`tool_choice`、`parallel_tool_calls`；
- `temperature`、`top_p`；
- `max_tokens` 或 Provider 对应字段；
- `stop`；
- `response_format`；
- `stream`、`stream_options`；
- 可选 PolyGate policy/session header。

响应至少完整保留：

- `id/object/created/model`；
- `choices[].message.content`；
- `choices[].message.tool_calls`；
- `finish_reason`；
- `usage`；
- 标准错误状态和可诊断错误体。

Adapter 不应重新构造一个最小响应。更稳妥的方式是：

1. Gateway 校验 PolyGate 自己关心的字段；
2. 对允许的 OpenAI 字段做白名单透传；
3. 仅改写物理 `model` 和 Provider 鉴权；
4. 完整保存上游响应语义；
5. 在旁路生成 PolyGate 决策记录。

### 5.3 流式响应与决策卡

当前非流式响应可以在顶层附加 `polygate`。流式 SSE 中，成本和 token 使用通常要到结束时才完整，不能提前生成最终决策卡，也不宜插入第三方客户端无法识别的自定义 SSE 数据。

推荐契约：

- 响应头返回 `X-PolyGate-Request-ID`；
- 可选返回路由开始时已知的 `X-PolyGate-Provider` 和 `X-PolyGate-Policy`；
- 请求结束后，将完整决策写入 Redis；
- 提供 `GET /v1/decisions/{request_id}`；
- Web 在流结束后查询完整决策卡；
- pi 可忽略这些扩展，仍保持 OpenAI 客户端兼容。

不要依赖在 SSE 最后插入自定义 `polygate` event，除非客户端明确支持。

### 5.4 故障与取消语义

- 首字节输出前失败：允许重试或切换 fallback；
- 已输出部分 token 后失败：不能透明切换 Provider 后继续拼接；
- 中途失败应终止流并记录 partial failure；
- 客户端断开时应取消上游请求；
- retry 只针对明确可重试的网络错误、429 和部分 5xx；
- tool result 请求是否重试必须结合幂等性；
- 每次尝试都记录 Provider、耗时、错误类型和费用。

### 5.5 Agent 接入验收

最小端到端测试：

1. pi 通过 `polygate/auto` 发起请求；
2. 请求携带一个无副作用工具，例如读取固定测试文件；
3. Provider 以流式 tool call 返回；
4. pi 执行工具并提交 tool result；
5. Provider 返回最终文本；
6. Gateway 记录两次模型调用、token、成本和同一 session；
7. 关闭主 Provider 后，下一次请求在首字节前切换 fallback；
8. 决策查询接口能说明 Provider、policy、能力过滤和 failover。

## 6. 模块三：路由系统升级

### 6.1 设计原则

路由过程分成五步：

```text
Resolve policy
    ↓
Apply hard eligibility gates
    ↓
Read runtime state
    ↓
Build execution plan
    ↓
Execute and record decision
```

硬约束不能被评分抵消：

- 隐私与地域；
- tools/vision/streaming 能力；
- context window；
- 明确的严格预算；
- 熔断或 unavailable；
- 租户权限。

软目标才参与排序：

- 预估成本；
- EWMA/p95 延迟；
- 近期成功率；
- 会话黏性；
- 静态质量档位或经过最低样本量验证的质量信号。

### 6.2 版本化 Policy Profile

用户选择高层意图，管理者管理具体规则：

| Policy | 用户场景 | 默认执行方式 |
|---|---|---|
| `interactive` | 普通聊天 | 低延迟优先，single + fallback |
| `economy` | 批量和低成本 | 严格预算，禁止 hedge |
| `private` | 敏感内容 | internal only |
| `agent` | 工具任务 | tools + stream + 足够上下文 + session affinity |
| `critical` | 关键回答 | 允许 hedge 或升级验证 |

示例：

```yaml
id: agent-interactive
version: 3
hard_requirements:
  privacy: standard
  supports_tools: true
  supports_streaming: true
  min_context_window: 64000
strategy:
  type: sticky-single-with-fallback
  max_attempts: 2
  failover_before_first_byte_only: true
objectives:
  latency_weight: 0.5
  cost_weight: 0.2
  reliability_weight: 0.3
limits:
  max_cost_usd: 0.02
```

每次请求必须记录 policy ID 和 version，保证可审计、可回滚、可比较。

### 6.3 Provider 能力注册表

Provider 配置应增加：

```yaml
capabilities:
  tools: true
  streaming: true
  vision: false
  json_schema: false
context_window: 64000
max_output_tokens: 8192
regions: [external]
```

`kind=real/mock` 不应继续作为质量判断依据。Mock 只用于演示和测试；真实生产路由应依赖模型能力、已验证的任务档案和运行状态。

### 6.4 Route Plan

Router 返回的内部结果从 `(chosen, reason)` 升级为：

```json
{
  "policy_id": "agent-interactive",
  "policy_version": 3,
  "strategy": "sticky-single-with-fallback",
  "primary": "real-a",
  "fallbacks": ["mock-a"],
  "session_affinity": "kept",
  "requirements": ["tools", "streaming", "context>=64000"],
  "candidates": [
    {"provider": "real-a", "eligible": true},
    {"provider": "mock-b", "eligible": false, "reason_code": "TOOLS_UNSUPPORTED"}
  ]
}
```

执行层消费 Route Plan，Router 本身不直接调用 Provider。这样才能独立测试策略和故障执行。

### 6.5 会话感知路由

同一 session 默认保持 Provider 黏性，除非：

- Provider 熔断或失去能力；
- 上下文超过 Provider 限制；
- 租户预算不足；
- policy 发生不兼容切换；
- 用户显式要求迁移。

建议用可选 `X-PolyGate-Session-ID` 或 `polygate.session_id`。Web 能直接传递；pi 若要获得会话黏性，需要在自定义 Provider/扩展中注入动态 session ID。

Redis 中只保存短期映射：

```text
session_id -> provider + policy_version + expires_at
```

不需要在 Gateway 保存完整聊天内容。

### 6.6 实时运行状态

不要在请求热路径上查询 Prometheus。推荐：

- 每次 Provider 调用更新 Redis 中的 EWMA latency；
- 记录滑动窗口错误和 429；
- 共享 circuit breaker 状态；
- Router 读取本地短缓存或 Redis snapshot；
- Prometheus 继续消费指标用于 Dashboard 和历史分析；
- CRD/Operator 负责较慢的 Provider 生命周期和配置状态；
- Redis runtime state 负责秒级故障与熔断。

### 6.7 执行策略

第一批只实现：

- `single`：调用一个 Provider；
- `single-with-fallback`：首字节前失败后按顺序切换；
- `sticky-single-with-fallback`：增加 session affinity。

后续再考虑：

- `hedge`：延迟关键请求并发调用两个 Provider，使用首个有效结果；
- `escalate`：便宜模型无法满足条件时升级到高能力模型；
- `generate-and-verify`：关键任务由一个模型生成，另一个模型验证。

hedge 和 verify 会显著增加成本，必须由 policy 明确授权，并计入租户预算，不能作为默认行为。

### 6.8 策略预演与管理视角

新增不调用模型的接口：

```http
POST /v1/routes/preview
```

返回：

- 解析后的 policy 和版本；
- eligible/rejected Provider；
- 每个拒绝原因的机器码和人类说明；
- primary/fallback；
- 最便宜和最快的替代方案；
- 预算或隐私条件改变后的 counterfactual。

管理界面可以用它完成策略发布前预演。后续可把历史请求特征脱敏后重放，对比新旧 policy 的“如果当时使用新策略会怎样”，但不实际调用模型。

## 7. 建议契约变更

### 7.1 Gateway Request v2

原则：OpenAI 字段尽量保持兼容，PolyGate 扩展集中在 `polygate` 或 `X-PolyGate-*` Header。

建议扩展：

```json
{
  "polygate": {
    "policy": "agent-interactive",
    "privacy": "standard",
    "max_cost_usd": 0.02,
    "latency_target_ms": 3000,
    "session_id": "optional-session-id",
    "cache_control": "no-store"
  }
}
```

兼容策略：

- 老的 `quality` 保留一段迁移期；
- `quality=cheap/balanced/high` 映射到 policy；
- 新客户端优先发送 `policy`；
- 不能识别的 OpenAI 字段返回明确错误，不能静默丢弃重要参数；
- 对 Provider 不支持的字段，错误中说明是 Provider capability 问题。

### 7.2 Decision Record v2

建议增加：

- `policy_id` / `policy_version`；
- `strategy`；
- `chosen_provider` / `chosen_model`；
- `fallback_chain`；
- `attempts[]`；
- `reason_codes[]`；
- `session_affinity`；
- `cache_status`；
- `first_byte_latency_ms`；
- `total_latency_ms`；
- `tokens` / `cost`；
- `request_id` / `route_trace_id`。

用户卡片显示精简解释，管理界面按 `request_id` 展开完整记录。

## 8. 分阶段实施顺序

### Phase A：可靠性基础

目标：现有单轮链路先成为可靠 Gateway。

- 异步 Provider client；
- 实时健康和 shared circuit state；
- timeout 分类；
- retry/fallback；
- Provider capability schema；
- 硬约束与软目标分离；
- 补齐单元测试和故障注入测试。

建议投入：2–4 人日。

### Phase B：Web 多轮聊天

- conversation store；
- 完整消息列表 UI；
- localStorage；
- 新建/切换/删除/重试；
- 上下文预算；
- 每轮决策卡。

建议投入：1–2 人日。

### Phase C：pi 最小兼容闭环

- OpenAI 字段白名单透传；
- tool message/tool call；
- SSE streaming；
- 标准 usage/error/finish reason；
- 取消与首字节前 failover；
- Agent 流量缓存 bypass；
- 一个真实 Provider 的端到端测试。

建议投入：3–5 人日。

### Phase D：路由策略主创新

- Policy Profile 和版本；
- Route Plan；
- session affinity；
- `/v1/routes/preview`；
- Decision Record v2；
- 管理界面展示 rejected candidates 和 policy version。

建议投入：3–5 人日。

### Phase E：可选增强

- hedge；
- escalate/verify；
- 用户反馈和任务质量档案；
- 历史请求脱敏重放；
- 租户预算账本和每日费用护栏。

只有前四个阶段稳定后再进入。

## 9. 测试矩阵

至少覆盖：

| 场景 | 预期 |
|---|---|
| 多轮普通聊天 | 上下文连续，Provider 决策逐轮记录 |
| 高隐私 + external | 硬拒绝，不得因缓存或 fallback 绕过 |
| tools 请求 + 不支持 tools 的 Provider | capability gate 排除 |
| tools 请求重复发送 | 默认不命中缓存 |
| 主 Provider 首字节前 500 | fallback 成功，attempts 完整 |
| 主 Provider 输出部分 token 后断开 | 终止流，不拼接另一 Provider 输出 |
| 客户端取消 | 上游请求被取消，记录 cancelled |
| context 超限 | 路由到更大上下文 Provider或明确失败 |
| 严格预算无候选 | 明确拒绝，不自动超预算 |
| 同 session 连续请求 | 默认保持 Provider |
| policy version 更新 | 新请求使用新版本，旧记录仍可追溯 |
| preview | 不调用 Provider、不产生模型费用 |

## 10. 明确不建议同时做的事情

- 不要同时实现服务端会话存储和内嵌 pi runtime；
- 不要在路由热路径调用另一个 LLM 做任务分类或质量裁判；
- 不要查询 Prometheus 决定每个请求的实时路由；
- 不要把多个软评分权重全部暴露给普通用户；
- 不要对 Agent/tool 流量直接启用现有精确缓存；
- 不要在已经输出流式 token 后透明切换 Provider；
- 不要把 `real/mock` 当作长期质量指标；
- 不要在没有真实 health/fallback 的前提下先做复杂 bandit 或机器学习路由。

## 11. 推荐的下一次设计评审议题

在编码前先冻结以下四份契约：

1. Gateway Request v2：OpenAI 透传字段和 PolyGate 扩展字段；
2. Provider Capability schema；
3. Policy Profile 与 Route Plan schema；
4. 流式 Decision Record 查询方式。

评审通过后，建议从 Phase A 和 Phase B 开始。Phase C 只选一个真实 Provider 做完整 tool-use 垂直切片，不要一开始追求所有 Provider 的统一兼容。

最终项目叙事应是：

> Web 和 Agent 通过同一个兼容 API 使用多模型；用户只表达任务意图，管理者通过版本化策略控制成本、隐私和可靠性；PolyGate 根据能力和实时状态生成可解释的执行计划，在故障发生时安全切换，并让每次选择都可观测、可预演、可审计。
