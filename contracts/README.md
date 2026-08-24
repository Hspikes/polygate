# PolyGate Contract Registry

这个目录是 PolyGate 跨组件接口的唯一真相来源。任何不兼容变更都必须同时更新
生产者、消费者、示例数据和契约测试。

## Artifacts

| # | 文件 | 定义什么 | 主要消费者 |
|---|---|---|---|
| 1 | `gateway-request.schema.json` | Agent-capable Chat Completions v2（OpenAI 字段 + PolyGate 约束） | Gateway、Web、Pi、压测器 |
| 2 | `decision-card.schema.json` + `.example.json` | 可解释路由决策卡片 | Gateway、Web、Pi |
| 3 | `providers/mock/README.md` 中的 `/admin/config` | Mock 故障注入接口 | 集成与可靠性测试 |
| 4 | `providers.yaml` | Provider 注册表（价格、隐私、能力、endpoint） | Gateway、Provider adapters |
| 5 | Adapter 统一响应格式（见下） | Provider 响应归一化 | Gateway、Provider adapters |
| 6 | `monitoring-overview.schema.json` + `.example.json` | 监控总览 JSON | Monitoring API、监控前端 |
| 7 | `automation-intent.schema.json` | 企业需求卡片 | Automation、Web、Pi |
| 8 | `automation-preview.schema.json` | 模板编译、优先级和代码预览 | Automation、Web、Pi |
| 9 | `automation-job.schema.json` | 异步 Job 状态和结果 | Automation、Web、监控 |
| 10 | `automation-examples.json` | Automation 联调示例 | 契约测试与客户端 |
| 11 | `decision-record.schema.json` + `.example.json` | 短 TTL 最终路由记录 | Gateway、Web、Pi |
| 12 | `policy.schema.json` | Policy Draft v1（不包含固定 guardrails） | Policy API、Gateway、Worker、Editor |
| 13 | `policy-store.schema.json` | 策略版本与生命周期记录 | Policy API、监控 |
| 14 | `policy-examples.json` | Policy 草稿、版本与 active response 示例 | 契约测试与客户端 |

## 契约 #11：Decision Record v1

`GET /v1/decisions/{request_id}` 使用与 Chat Completions 相同的 Bearer 鉴权，返回一次
已完成请求的最终 Provider、成本、tokens、重试、failover 和 outcome。记录默认只在
Redis 保留 1 小时；过期返回 404，Redis 不可用返回 503。

该记录是严格允许列表，不存储 prompt、消息、工具参数、Authorization、Provider API
Key、上游 endpoint 或原始错误。标准 OpenAI SSE 保持不变；客户端从响应 Header 读取
request ID，再独立查询本契约。

## 契约 #5：Adapter 统一响应格式

所有 Provider（真实和 Mock）对网关返回的响应，必须是**标准 OpenAI chat/completions 响应**，
且**必须包含 `usage` 字段**（真实 API 自带；Mock 需要生成），否则 Gateway 无法统一估算成本：

```json
{
  "id": "chatcmpl-xxx",
  "choices": [{ "message": { "role": "assistant", "content": "..." } }],
  "usage": { "prompt_tokens": 340, "completion_tokens": 128, "total_tokens": 468 }
}
```

## 缓存 key 规范化规则

当前采用：`sha256(normalize(messages) + privacy + scope + quality + max_cost_usd + latency_target_ms + policy_version)`，
其中 `scope` 区分自动路由与强制 Provider，`normalize` =
去掉每条 message 首尾空白 + 保持 role/顺序不变 + 不改大小写。
所有可能改变路由结果的约束都必须进入缓存键，避免修改偏好后命中旧路由结果。
`policy_version` 是 active policy 的版本号：策略发布后路由结果可能变化，v4 缓存不能被 v5 请求命中；旧缓存等待 TTL 自然过期，无需在发布时清空 Redis；rollback 会生成新版本号（如 v6），也不会错误复用 v4/v5 的缓存。
该键只用于纯 `role/content` 文本请求；tools、流式请求、消息元数据、
`session_id` 和显式生成参数一律绕过缓存，避免未进入上述键的语义发生碰撞。
当前只实现「精确 + 轻规范化」缓存。扩展规范化规则时需同步修改
`gateway/app/cache.py::normalize` 及本节。

## Policy v1 指标名

Policy 管理相关的 Prometheus 指标名属于兼容性契约，不得与 Grafana 和告警规则
独立变更：

| 指标名 | 类型 | 暴露方 | 含义 |
|---|---|---|---|
| `polygate_policy_active_version` | Gauge | Policy API | 控制面当前 active 策略版本号 |
| `polygate_policy_loaded_version{component}` | Gauge | Gateway / Worker | 各组件当前已加载的策略版本号；`component="gateway"` 或 `component="automation-worker"`（固定字符串，Grafana 按这两个值分别查询） |
| `polygate_policy_publications_total{action,result}` | Counter | Policy API | 策略发布/回滚次数；`action=publish|rollback`，`result=success|rejected|degraded` |
| `polygate_policy_reload_failures_total{component,reason}` | Counter | Gateway / Worker | 拉取或校验新策略失败、继续使用 Last Known Good 的次数；`component="gateway"` 或 `component="automation-worker"`；`reason=network|http|validation|file` |
| `polygate_policy_last_publish_timestamp_seconds` | Gauge | Policy API | 最近一次成功发布的 Unix 时间戳 |

> 版本一致性判据：若 `polygate_policy_active_version` 与任一组件的
> `polygate_policy_loaded_version` 不一致超过 30 秒，Grafana 面板进入告警颜色。

## 决策卡片的 policy version

Gateway 当前通过响应头 `X-PolyGate-Policy-Version: <n>` 暴露版本，决策卡片结构
保持不变。Automation Preview/Job 契约包含可选的 `policy_version`（integer，
minimum 1）。
