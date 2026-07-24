# PolyGate 契约（冻结区 / Frozen Contracts）

> **规则**：这个目录里的文件是四人协作的唯一真相来源。
> 改动任何一个契约，必须在群里 @ 所有受影响的人并达成一致后才提交，
> 否则你会悄悄弄坏别人正在写的代码。

开工会议冻结的共享契约都在这里：

| # | 文件 | 定义什么 | 谁产出 | 谁消费 |
|---|---|---|---|---|
| 1 | `gateway-request.schema.json` | Agent-capable Chat Completions v2（OpenAI 字段 + polygate 约束） | A | Web、Pi、压测器 |
| 2 | `decision-card.schema.json` + `.example.json` | 决策卡片 JSON | A | D |
| 3 | （见 `providers/mock/README.md` 的 `/admin/config`） | Mock 故障注入接口 | B | D |
| 4 | `providers.yaml` | Provider 注册表（价格/隐私/endpoint） | A（B 校对价格） | A 路由、D 卡片 |
| 5 | Adapter 统一响应格式（见下） | 各 Provider → 网关的归一化格式 | B | A |
| 6 | `monitoring-overview.schema.json` + `.example.json` | 监控总览 JSON | Monitoring API | 监控前端 |
| 7 | `automation-intent.schema.json` | 企业需求卡片 | D | Automation API、Pi 插件 |
| 8 | `automation-preview.schema.json` | 模板编译、优先级和代码预览 | A | D、Pi 插件 |
| 9 | `automation-job.schema.json` | 异步 Job 状态和结果 | A/B | D、C 监控 |
| 10 | `automation-examples.json` | Automation 三份契约的联调示例 | A/B | C/D、测试脚本 |
| 11 | `decision-record.schema.json` + `.example.json` | 短 TTL 最终路由记录与查询响应 | Gateway | Web、Pi |

## 契约 #11：Decision Record v1

`GET /v1/decisions/{request_id}` 使用与 Chat Completions 相同的 Bearer 鉴权，返回一次
已完成请求的最终 Provider、成本、tokens、重试、failover 和 outcome。记录默认只在
Redis 保留 1 小时；过期返回 404，Redis 不可用返回 503。

该记录是严格允许列表，不存储 prompt、消息、工具参数、Authorization、Provider API
Key、上游 endpoint 或原始错误。标准 OpenAI SSE 保持不变；客户端从响应 Header 读取
request ID，再独立查询本契约。

## 契约 #5：Adapter 统一响应格式

所有 Provider（真实和 Mock）对网关返回的响应，必须是**标准 OpenAI chat/completions 响应**，
且**必须包含 `usage` 字段**（真实 API 自带；Mock 要自己伪造一个），否则 A 无法统一算钱：

```json
{
  "id": "chatcmpl-xxx",
  "choices": [{ "message": { "role": "assistant", "content": "..." } }],
  "usage": { "prompt_tokens": 340, "completion_tokens": 128, "total_tokens": 468 }
}
```

## 缓存 key 规范化规则（A 与 B 第一天一起定死，避免命中率飘）

当前采用：`sha256(normalize(messages) + privacy + scope + quality + max_cost_usd + latency_target_ms)`，
其中 `scope` 区分自动路由与强制 Provider，`normalize` =
去掉每条 message 首尾空白 + 保持 role/顺序不变 + 不改大小写。
所有可能改变路由结果的约束都必须进入缓存键，避免修改偏好后命中旧路由结果。
该 P0 键只用于纯 `role/content` 文本请求；tools、流式请求、消息元数据、
`session_id` 和显式生成参数一律绕过缓存，避免未进入上述键的语义发生碰撞。
> 语义缓存是 P3，P0 只做「精确 + 轻规范化」。如需扩展规范化规则，改 `gateway/app/cache.py::normalize`，并同步更新本节。
