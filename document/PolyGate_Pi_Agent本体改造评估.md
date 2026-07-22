# PolyGate Pi Agent 本体改造评估

> 状态：修订后技术评估稿
> 日期：2026-07-22
> 评估基线：Pi `v0.81.1` / 主仓库 commit `a5afc3f171e422e08a2ccc342827719f9952f38a`
> 范围：只评估 Pi Agent 作为独立客户端时，增加 PolyGate 路由偏好填写卡所需的改造

## 1. 范围澄清

Web Chat 和 Pi 是两个彼此独立的 PolyGate 客户端：

```text
Web Chat ─────┐
              ├──> PolyGate Gateway
Pi Agent ─────┘
```

- Web Chat 有自己的路由偏好 UI；
- Pi 有自己的 TUI 路由偏好卡；
- Pi 不由 Web Chat 驱动；
- 本文不评估 Web Chat、RPC 嵌入、Gateway 内部实现或完整端到端链路。

本文中的“Pi Agent”具体指 `@earendil-works/pi-coding-agent` CLI/TUI 及其 Extension 运行时。

## 2. 结论

**推荐接入 Pi，并用一个小型项目级 Pi Extension 增加路由偏好卡；不需要 fork Pi，也不需要改造 Pi Agent loop。**

对 Pi 的必要改造只有四件事：

1. 注册 `polygate/auto` 自定义 Provider；
2. 注册 `/route` 命令，打开可填写的 TUI 路由卡；
3. 保存当前路由偏好，并在输入框附近显示简要状态；
4. 在 `before_provider_request` 中向 OpenAI-compatible 请求注入 `polygate` 字段。

Pi v0.81.1 现有 Extension API 已经直接支持这些能力：

- `pi.registerProvider()`；
- `pi.registerCommand()`；
- `ctx.ui.custom()` / `ctx.ui.select()` / `ctx.ui.input()`；
- `ctx.ui.setWidget()`；
- `pi.appendEntry()` 用于 session 内状态保存；
- `before_provider_request` 用于请求体替换。

因此这是一个**小型 Extension 适配任务**，不是一项 Pi 核心改造工程。

## 3. 路由偏好卡

### 3.1 推荐交互

不需要在每次发送 Prompt 时弹出卡片。推荐：

- 用户输入 `/route` 打开卡片；
- 修改并保存后，配置对后续请求生效；
- 在 Pi 输入框上方常驻一行摘要；
- Agent 正在运行时不允许修改，直接提示用户等待当前任务完成。

最后一条可以直接用 `ctx.isIdle()` 判断，因此不需要实现复杂的运行中配置快照机制。

### 3.2 卡片字段

卡片与当前 Web 客户端使用相同的四个路由维度，但两者代码彼此独立：

```text
┌─ PolyGate Routing Preferences ─────────┐
│ Quality       [Balanced        ▾]          │
│ Privacy       [Standard        ▾]          │
│ Maximum cost  [$ 0.002000        ]          │
│ Latency       [3000 ms          ]          │
│                                                │
│              [Cancel]  [Apply]                 │
└──────────────────────────────────────────────┘
```

Extension 内部状态：

```typescript
type RoutingPreferences = {
  quality: "balanced" | "high" | "cheap";
  privacy: "standard" | "high";
  maxCostUsd: number;
  latencyTargetMs: number;
};
```

发送前转换为：

```json
{
  "polygate": {
    "quality": "balanced",
    "privacy": "standard",
    "max_cost_usd": 0.002,
    "latency_target_ms": 3000
  }
}
```

表单校验只需要：

- `maxCostUsd >= 0`；
- `latencyTargetMs > 0`；
- 枚举字段必须在允许范围内；
- Cancel 不修改原配置。

### 3.3 常驻摘要

`ctx.ui.setWidget()` 显示一行即可：

```text
PolyGate  Balanced · Standard · max $0.002 · 3000 ms
```

它只让用户知道当前请求将使用什么路由偏好，不需要做复杂的可交互常驻组件。

## 4. 请求注入

在 `before_provider_request` 中：

1. 检查 `ctx.model?.provider === "polygate"`；
2. 检查 `event.payload` 是普通 JSON 对象；
3. 返回一个新对象，向其添加 `polygate` 字段；
4. 不修改 Pi 已经生成的 `messages`、`tools`、`stream` 等字段；
5. 切换到其他 Provider 时不注入 PolyGate 扩展。

示意代码：

```typescript
pi.on("before_provider_request", (event, ctx) => {
  if (ctx.model?.provider !== "polygate") return;
  if (!isRecord(event.payload)) return;

  return {
    ...event.payload,
    polygate: toGatewayConstraints(currentPreferences),
  };
});
```

`event.payload` 的公开类型是 `unknown`，因此需要一个很小的类型守卫，但这不构成大量开发工作。

## 5. 状态保存

路由偏好可用 `pi.appendEntry("polygate-routing-prefs", preferences)` 写入 Pi session。扩展在 `session_start` 时扫描最后一条同类 entry 并恢复状态。

这种 entry：

- 不进入 LLM 上下文；
- 不增加 Prompt token；
- 能跟随 Pi session 恢复；
- 不需要额外数据库或配置服务。

如果课程演示不要求重启后恢复，MVP 甚至可以先只保存在 Extension 内存中，之后再加 `appendEntry()`。

## 6. 建议目录

不需要拆分七八个模块。一个小型 Extension 足够：

```text
.pi/extensions/polygate-routing/
├── index.ts         # Provider、/route、widget、request hook
├── routing-card.ts  # TUI 卡片组件
└── routing.test.ts  # 校验和 payload 注入测试
```

如果路由卡代码较短，前两个文件也可以合并。

## 7. 修正后工作量

### 7.1 可运行原型

| 子项 | 估时 |
|---|---:|
| Extension 骨架和 PolyGate Provider 注册 | 0.5 小时 |
| `/route` 表单及四个字段 | 2–3 小时 |
| widget 摘要与内存状态 | 0.5 小时 |
| `before_provider_request` 注入 | 0.5–1 小时 |
| 本地手工验证 | 1 小时 |
| **合计** | **4.5–6 小时** |

因此，**可演示原型约为半个到一个人日**。

### 7.2 稳定版

在原型上增加：

- session 恢复；
- 非法输入和 Cancel 测试；
- 非 PolyGate Provider 隔离测试；
- Pi 版本锁定；
- 安装与使用说明。

**稳定交付版约为 1–2 人日。**

上述估时只包含 Pi 路由偏好卡及请求注入，不包含 Web Chat、Gateway 改造、Agent tool calling 兼容、流式 Decision Record 查询或调用后决策卡。

### 7.3 可选功能：调用后决策卡

如果后续希望 Pi 像 Web Chat 一样，在回答后展示 Provider、成本、延迟和路由理由，可以再增加调用后决策卡。

这不是本次“填写路由卡”的基线范围。在 Gateway 已能通过 request ID 返回 Decision Record 的前提下，Pi 侧预计另增加 **0.5–1 人日**。

## 8. 风险

| 风险 | 级别 | 应对 |
|---|---|---|
| Pi 版本升级导致 payload 结构变化 | 低–中 | 演示环境锁定 `0.81.1`，保留一个 payload 契约测试 |
| TUI 数值输入和中文 IME | 低 | 复用 Pi TUI 现有 `Editor`/输入组件 |
| 运行中修改路由配置 | 低 | `ctx.isIdle()` 为 false 时拒绝打开或保存 |
| 非 PolyGate Provider 被注入自定义字段 | 低 | 注入前检查 `ctx.model.provider` |

这些都是小型 Extension 的常规风险，不构成选用 Pi 的阻碍。

## 9. 验收标准

1. Pi 能选择 `polygate/auto` Provider；
2. `/route` 能打开路由偏好卡；
3. 能填写 quality、privacy、maximum cost 和 latency target；
4. 非法数值不能保存；
5. 保存后 widget 立即显示新配置；
6. 发给 PolyGate 的 Provider payload 包含正确 `polygate` 对象；
7. Pi 原有 messages、tools 和 streaming 字段不被破坏；
8. 切换到其他 Provider 后不注入 `polygate`；
9. Agent 运行中修改配置会被明确拒绝；
10. 稳定版中，恢复 Pi session 后路由偏好仍存在。

## 10. 最终建议

- **Go：接入 Pi v0.81.1；**
- **用项目级 Extension 增加 `/route` 填写卡和 payload 注入；**
- **可演示版按 0.5–1 人日计划，稳定版按 1–2 人日计划；**
- 不 fork Pi；
- 不将路由偏好写入 Prompt；
- 不把 Web Chat 和 Pi 合并成一套客户端或驱动关系；
- 调用后决策卡作为可选的第二步，不计入本次基线工作量。

## 11. 主要依据

- [Pi 主仓库与包划分](https://github.com/earendil-works/pi)
- [Pi Extension API](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)
- [Pi TUI 自定义组件](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/tui.md)
- [Pi Custom Provider 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/custom-provider.md)
- [Pi Extension 类型定义](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/extensions/types.ts)
