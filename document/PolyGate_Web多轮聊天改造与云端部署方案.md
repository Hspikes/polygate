# PolyGate Web 多轮聊天改造与云端部署方案

> 状态：已确认实施方案
> 日期：2026-07-22
> 适用范围：PolyGate Web 客户端、多轮聊天、Markdown 渲染、本地会话持久化及 EKS 云端部署
> 明确不包含：SSE 流式输出、服务端 Conversation Service、账号体系、Agent tools、多模态输入

## 1. 结论

PolyGate Web 从当前纯 HTML 单文件升级为：

> **Vite + React + TypeScript + react-markdown + remark-gfm + Zod + Vitest/Testing Library**

核心架构决策：

- 浏览器维护 Conversation 和完整消息历史；
- Gateway 保持无状态，继续接收完整 `messages[]`；
- 第一阶段不实现流式输出；
- Markdown 使用成熟解析库，不自行实现 parser；
- 保留现有视觉风格和普通 CSS，不引入重量级 UI 框架；
- 使用 React 内置 `useReducer + Context` 管理状态，暂不引入 Redux/Zustand；
- 本地会话使用 `localStorage`，不在 Gateway 隐式保存；
- 云端先使用 **EKS Web Nginx Pod + 同源 `/api` 反向代理**；
- 若后续产品化，再演进到 **CloudFront + Private S3 + ALB/EKS Gateway**。

该方案最终生成纯静态 HTML/CSS/JS，不要求云端运行 Node.js。Node.js 只在开发和构建阶段使用。

## 2. 当前实现基线

当前 `web/` 没有使用前端框架或构建平台：

- `web/index.html` 同时包含 HTML、CSS 和 JavaScript，共约 1133 行；
- 没有 `package.json`、TypeScript、组件系统或前端自动化测试；
- 通过 `python -m http.server 8080` 提供静态文件；
- 每次请求只发送一条 `user` message；
- `renderCard()` 使用 `innerHTML` 覆盖上一次回答；
- 回答只按纯文本转义，Markdown 表格和代码块无法正确渲染；
- Gateway 已能接收并转发完整 `messages[]`，基础多轮无需服务端 Session；
- Web 尚未进入 `docker-compose.yml` 或 Kubernetes 部署清单；
- Gateway 当前使用 NodePort 暴露，CORS 为 `allow_origins=["*"]`。

因此，主要改造点在客户端状态和渲染架构；Gateway 只需要少量契约、缓存和云端入口配套修改。

## 3. 产品目标与非目标

### 3.1 本轮目标

- 支持真正连续的多轮对话；
- 新建、切换、重命名和删除会话；
- 刷新后恢复本地会话；
- 每轮 assistant message 保留自己的决策卡；
- 支持发送中、失败、取消、重试和重新生成；
- 支持 CommonMark/GFM、表格、列表、代码块和复制；
- 展示会话累计成本、token 和 Provider 切换；
- 上下文接近上限时给出明确提示；
- 支持桌面和移动端；
- 本地、Docker Compose 和 EKS 使用一致的相对 API 地址；
- 具备最小但有效的自动化测试。

### 3.2 本轮非目标

- SSE/WebSocket 流式输出；
- 跨设备同步和服务端聊天记录；
- 登录、用户账号、分享会话；
- 工具调用和 Agent loop；
- 多模态 content block；
- 离线 PWA；
- SSR、SEO 和 Next.js；
- 复杂状态框架和重量级设计系统。

## 4. 技术选型

### 4.1 方案比较

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 原生 JS + Markdown 库 | 迁移最少 | 会话、异步状态和 DOM 仍需手工管理，长期易失控 | 只适合临时 Demo |
| Vite + React + TypeScript | 组件、类型、状态和测试能力适合聊天 UI；仍可纯静态部署 | 有一次工程化迁移成本 | **采用** |
| Next.js/完整 UI 框架 | 功能丰富 | SSR、服务端路由和通用设计系统均非当前需求 | 不采用 |

如果团队整体明显更熟悉 Vue，可用 Vue 3 + TypeScript 做等价替换；在当前没有既有前端约束的情况下，默认采用 React。

### 4.2 运行时依赖

| 依赖 | 用途 | 决策 |
|---|---|---|
| `react`、`react-dom` | 组件渲染与 UI 状态 | 必需 |
| `react-markdown` | 将模型 Markdown 渲染为 React 元素 | 必需 |
| `remark-gfm` | 表格、删除线、任务列表、自动链接 | 必需 |
| `zod` | 校验 Gateway 响应与 localStorage 数据 | 推荐采用 |
| `rehype-highlight` | 代码语法高亮 | 第二阶段可选 |

### 4.3 开发依赖

- Vite；
- TypeScript，开启 `strict`；
- Vitest；
- React Testing Library；
- `@testing-library/user-event`；
- Playwright，仅覆盖关键端到端链路；
- ESLint 和 React Hooks 规则。

### 4.4 明确不引入

- Redux/Zustand：当前 `useReducer + Context` 足够；
- React Router：会话切换暂不需要 URL 路由；
- Axios：原生 `fetch + AbortController` 足够；
- MUI/Ant Design/Tailwind：保留 PolyGate 现有视觉语言；
- UUID/date 工具库：使用 `crypto.randomUUID()` 和 `Intl.DateTimeFormat`；
- Markdown HTML 直出：不使用 `dangerouslySetInnerHTML`。

所有 npm 依赖通过 `package-lock.json` 固定，CI/部署使用 `npm ci`。

## 5. 客户端架构

### 5.1 推荐目录

```text
web/
├── package.json
├── package-lock.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── public/
│   └── config.json                 # 可选运行时配置
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── domain/
│   │   ├── conversation.ts
│   │   └── gateway.ts
│   ├── api/
│   │   └── gateway-client.ts
│   ├── store/
│   │   ├── conversation-reducer.ts
│   │   └── ConversationProvider.tsx
│   ├── storage/
│   │   └── local-conversations.ts
│   ├── components/
│   │   ├── ChatShell.tsx
│   │   ├── ConversationSidebar.tsx
│   │   ├── MessageList.tsx
│   │   ├── MessageBubble.tsx
│   │   ├── MarkdownMessage.tsx
│   │   ├── Composer.tsx
│   │   ├── RoutingPanel.tsx
│   │   └── DecisionCard.tsx
│   └── styles/
│       ├── tokens.css
│       ├── layout.css
│       └── markdown.css
└── tests/
```

现有颜色、字体、卡片、间距和响应式规则迁移到普通 CSS 文件，不做无必要的视觉重写。

### 5.2 Conversation 数据模型

```text
Conversation
  id
  title
  createdAt
  updatedAt
  settings
  messages[]
  storageMode: persistent | ephemeral

Message
  id
  role: system | user | assistant
  content
  createdAt
  status: sending | complete | error | cancelled
  requestSettings
  decisionCard
  error
```

`requestSettings` 保存该轮实际使用的路由参数，防止用户后来修改设置后历史记录含义发生漂移。

### 5.3 Reducer Actions

```text
CREATE_CONVERSATION
SELECT_CONVERSATION
RENAME_CONVERSATION
DELETE_CONVERSATION
UPDATE_SETTINGS
APPEND_USER_MESSAGE
START_REQUEST
COMPLETE_REQUEST
FAIL_REQUEST
CANCEL_REQUEST
RETRY_REQUEST
REGENERATE_RESPONSE
RESTORE_PERSISTED_STATE
```

Reducer 必须是纯函数，网络请求、localStorage 和 `AbortController` 放在独立 service/hook 中。

### 5.4 请求生命周期

发送流程：

1. 校验 composer 和路由设置；
2. 在当前 Conversation 中追加 user message；
3. 创建 `sending` 状态的 assistant placeholder；
4. 只取有效的 `system/user/assistant` 历史组成 `messages[]`；
5. 调用 `/api/v1/chat/completions`；
6. 成功后填充 assistant content、decision card 和实际设置；
7. 失败后保留 user message，将 placeholder 标记为 `error`；
8. 重试复用原 user message，不能重复插入；
9. 重新生成删除/替换最后一条 assistant 回答，再提交同一历史；
10. 切换会话后，迟到响应仍按 conversation ID 写回原会话。

进行中的请求按 request/conversation ID 管理，不能只使用一个全局 `busy` 布尔值。

`AbortController`、Promise 和 timer 属于瞬态状态，不写入 localStorage。

## 6. Markdown 渲染与安全

### 6.1 渲染链路

```text
assistant.content
      ↓
react-markdown
      ↓
remark-gfm
      ↓
自定义 link / code / table 组件
      ↓
React DOM
```

支持范围：

- 标题、段落、列表和引用；
- GFM 表格、删除线、任务列表；
- fenced code block 和 inline code；
- 代码语言标签和复制按钮；
- 安全链接；
- 合理的长文本换行与横向滚动。

### 6.2 安全规则

- 不启用 `rehype-raw`；
- 不使用 `dangerouslySetInnerHTML`；
- 模型返回的原始 HTML 不成为真实 DOM；
- 链接只允许安全协议；
- 外部链接设置 `rel="noopener noreferrer"`；
- 第一阶段禁用 Markdown 外部图片，避免模型生成的 URL 自动触发第三方请求；
- 如果未来必须支持 HTML，应引入 `rehype-sanitize`，并放在所有不安全转换之后；
- CSP 默认只允许 `connect-src 'self'`，按实际监控需求再放宽。

语法高亮不是 Markdown 正确性的前提。第一阶段可以只做代码块样式和复制，第二阶段再按需引入 `rehype-highlight`，避免初始 bundle 包含大量不使用的语言定义。

## 7. 本地持久化与隐私

MVP 使用 `localStorage`，存储结构必须包含：

```text
schemaVersion
activeConversationId
conversations[]
```

要求：

- 使用 Zod 校验恢复数据；
- 数据损坏时回退为空状态，不能导致页面白屏；
- 提供 schema migration；
- 提供“不保存历史”的 ephemeral 模式；
- `privacy=high` 时明确显示本地持久化状态，推荐默认不保存；
- 支持一键清空全部本地会话；
- 不存储 API Key、Provider Key 或其他凭证。

云端部署不会让 localStorage 自动跨设备同步。跨设备同步需要未来新增账号和 Conversation Service，不属于本轮范围。

## 8. 上下文治理

第一阶段：

- 默认向 Gateway 发送当前有效完整历史；
- 客户端显示保守的 token/字符估计；
- 为回答预留输出空间；
- 接近上限时提示用户；
- 超限时提供“新建会话”或“压缩历史”的明确动作；
- 不能静默删除 system 指令；
- 不能静默截断后仍声称发送了完整上下文。

真正的硬限制由 Gateway 执行，因为 `model=auto` 的 context window 在路由完成后才能确定。摘要压缩可作为后续增强，且必须记录摘要覆盖的原消息范围。

需要同步修复：当前 Gateway cache key 包含 privacy、scope、quality 和 max cost，但没有 `latency_target_ms`；修改延迟目标后可能命中旧路由结果。缓存 key 应覆盖所有会影响路由的约束。

## 9. UI 结构

页面从 Landing Page 演进为 Chat App Shell：

```text
┌──────────────────────────────────────────────────────────┐
│ PolyGate       会话累计成本 / token      Gateway 状态   │
├───────────────┬──────────────────────────────────────────┤
│ 新建会话      │ User message                             │
│               │                                          │
│ 会话列表      │ Assistant Markdown                       │
│ - Conversation│ [Provider] [Cost] [Latency] [Decision ▾] │
│ - Conversation│                                          │
│               │ ...                                      │
├───────────────┴──────────────────────────────────────────┤
│ Routing preferences ▸                                    │
│ [Composer                              ] [Send/Cancel]    │
└──────────────────────────────────────────────────────────┘
```

关键交互：

- 消息区域独立滚动，composer 固定在底部；
- Enter 发送、Shift+Enter 换行；
- 每条 assistant message 带可折叠决策卡；
- 会话顶部显示累计成本、token、缓存命中和 Provider 切换次数；
- 修改 routing preferences 只影响后续请求；
- 网络/Provider/路由/预算错误使用不同提示；
- 手机端会话栏改为抽屉。

## 10. 云端部署评估

### 10.1 技术栈适配性

Vite + React + TypeScript 非常适合云端：

- 构建结果是纯静态文件；
- 不需要在生产环境运行 Node.js；
- JS/CSS 可使用内容 hash 和长期缓存；
- Web 与 Gateway 可独立扩缩容；
- 同一套客户端构建可以通过相对 API 路径部署到本地、Compose、EKS 或 CDN；
- Markdown 和会话状态主要在浏览器执行，不增加 Gateway CPU 压力。

当前问题不在前端框架，而在公网入口、TLS、CORS 和部署清单尚未完成。

### 10.2 当前阶段推荐拓扑

考虑 EKS、Learner Lab IAM 权限和预算限制，当前采用：

```text
Browser
   │
   │ 一个公开入口
   ▼
Web Nginx Pod
   ├── /              → React dist/
   └── /api/*         → Gateway ClusterIP Service
                              │
                              ├── Redis
                              └── Providers
```

落地要求：

- 新增 `web/Dockerfile`，使用 Node build + Nginx runtime 多阶段构建；
- 新增 Nginx 配置，提供静态文件和 `/api` 反向代理；
- 新增 `web` Compose service；
- 新增 Kubernetes Web Deployment 和 Service；
- Gateway 改为内部 ClusterIP；
- Mock Provider 默认保持内部 ClusterIP；
- 故障注入管理端通过 `kubectl port-forward` 或受控管理入口访问；
- 浏览器固定调用相对地址 `/api/v1/chat/completions`；
- 配置 Web readiness/liveness `/healthz`；
- 非流式代理设置合理的 request size 和 `proxy_read_timeout`。

### 10.3 课程 Demo 入口

如果 Learner Lab 无权安装 AWS Load Balancer Controller：

```text
Browser → Web NodePort → Nginx → Gateway ClusterIP
```

约束：

- 只公开 Web 的一个 NodePort；
- Security Group 只开放该端口；
- 来源尽量限制为演示者 IP；
- 不再为 Gateway 和 Mock Provider 开放公共 NodePort；
- 不使用 `All TCP / 0.0.0.0/0` 作为长期配置；
- 该模式仅用于教学/演示，不标记为生产部署。

### 10.4 更接近生产的 EKS 入口

若 IAM 允许安装 AWS Load Balancer Controller：

```text
Browser → HTTPS ALB → Kubernetes Ingress
                         ├── /     → Web Service
                         └── /api  → Gateway Service
```

优点：

- 一个域名和 HTTPS 入口；
- L7 path routing；
- Gateway/Provider 不暴露节点端口；
- 可以结合 ACM 证书和安全组；
- 保持前端同源调用。

采用前先验证：

- Learner Lab IAM 是否允许创建 Controller 所需角色/策略；
- 子网标签是否满足 ALB 发现；
- 是否允许创建 ALB、Security Group 和 ACM/域名资源；
- 固定费用是否符合课程预算。

权限预检失败时回退到 Web NodePort，不能阻塞客户端实现。

### 10.5 产品化演进

长期推荐：

```text
                       ┌─ 默认路径 → Private S3：React 静态文件
Browser → CloudFront ──┤
                       └─ /v1/*   → ALB → EKS Gateway
```

要求：

- S3 bucket 保持私有；
- CloudFront 使用 OAC 访问 S3；
- 静态 hash 资源长期缓存，`index.html` 短缓存；
- `/v1/*` API behavior 禁用缓存；
- API behavior 转发必要 method/header/body；
- CloudFront/ALB 使用 HTTPS；
- Gateway 仍在 EKS 中独立扩缩容。

该拓扑不作为本轮交付，避免同时引入 S3、CloudFront、ACM、DNS、ALB 和额外 IaC。

## 11. 云端安全基线

公开部署前至少完成：

- HTTPS；
- Gateway API Key 或用户认证；
- 请求速率限制；
- 每日/租户预算护栏；
- 精确 CORS allowlist，或完全同源访问；
- 请求体大小和超时限制；
- Provider Key 只保存在 Kubernetes Secret；
- Web 不包含任何 Provider/Gateway 密钥；
- CSP、`X-Content-Type-Options`、`Referrer-Policy` 和点击劫持防护；
- `/metrics`、Mock admin、Grafana 等管理接口不直接公开；
- 日志不得记录完整 prompt、凭证或 localStorage 内容。

建议 CSP 起点：

```text
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
connect-src 'self';
img-src 'self' data:;
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
```

上线前应尽量移除 `style-src 'unsafe-inline'`；第一阶段保留是为了降低现有 CSS 迁移阻力。

## 12. 测试策略

### 12.1 Vitest

- reducer 新增、切换、重命名和删除会话；
- 重试不会重复插入 user message；
- 重新生成只替换目标 assistant message；
- 路由设置只影响后续请求；
- localStorage 损坏安全恢复；
- schemaVersion migration；
- Gateway response/decision card Zod 校验；
- 上下文请求组装。

### 12.2 React Testing Library

- 输入、发送、取消和重试；
- loading/error/complete 状态；
- 决策卡展开和折叠；
- Enter/Shift+Enter；
- Markdown 表格、列表和代码块；
- 危险 HTML 不执行；
- 路由设置和累计成本展示。

### 12.3 Playwright

最少覆盖：

1. 连续发送两轮，第二轮请求包含第一轮历史；
2. 刷新后恢复会话；
3. Gateway 错误后重试成功；
4. 新建和切换会话，迟到响应写回正确会话；
5. Markdown 表格、代码块和危险 HTML；
6. 通过 Compose/EKS Web 入口完成一次真实请求。

## 13. 分阶段实施与工作量

### Phase 1：工程化与静态迁移

- Vite + React + TypeScript；
- 拆分现有 CSS；
- 建立组件骨架；
- Fixture 继续可用；
- 保持现有视觉基线。

建议投入：0.5–1.5 人日。

### Phase 2：多轮 Conversation

- reducer/store；
- 消息列表和 composer；
- 完整历史请求；
- 多会话 CRUD；
- 每轮决策卡和累计统计。

建议投入：2–3 人日。

### Phase 3：持久化与请求可靠性

- localStorage + Zod + migration；
- ephemeral/high-privacy 模式；
- cancel/retry/regenerate；
- 并发和迟到响应正确性；
- 上下文提示。

建议投入：1.5–2.5 人日。

### Phase 4：Markdown 与体验

- react-markdown + remark-gfm；
- code copy；
- Markdown 样式与安全策略；
- 手机端和无障碍修正。

建议投入：0.5–1.5 人日。

### Phase 5：测试

- Vitest/Testing Library；
- 关键 Playwright E2E；
- 本地/Fixture/Gateway 三种模式回归。

建议投入：1–1.5 人日。

### Phase 6：云端集成

- Web Dockerfile/Nginx；
- Compose service；
- Kubernetes Deployment/Service；
- `/api` 同源代理；
- 部署和 smoke-test 脚本；
- NodePort 或 ALB 入口。

建议投入：2–3 人日；如使用 ALB/Ingress/HTTPS，额外 1.5–3 人日。

总工作量：

| 范围 | 估算 |
|---|---:|
| 非流式多轮客户端 | 6–9 人日 |
| EKS 教学云端集成 | 2–3 人日 |
| 当前推荐总计 | **8–12 人日** |
| ALB/Ingress/HTTPS | 额外 1.5–3 人日 |
| S3 + CloudFront 产品化 | 未来额外 3–6 人日 |

估算按一名熟悉仓库的开发者计算，不含 Learner Lab IAM/凭证阻塞时间。

## 14. 验收标准

### 14.1 客户端

- 连续完成至少 10 轮对话；
- 后续请求包含正确历史，能理解“上一条”“它”等指代；
- 刷新后恢复持久化会话；
- 新建、切换、重命名和删除会话；
- 发送中可取消；
- 失败可重试且不重复 user message；
- 最后一轮可重新生成；
- 每轮保留独立决策卡和请求设置；
- 展示会话累计成本、token 和 Provider 切换；
- Markdown 表格、列表、链接和代码块正确显示；
- 原始 HTML/脚本不执行；
- `privacy=high` 能使用不持久化模式；
- 上下文接近上限时有明确提示；
- 桌面和移动端基本可用。

### 14.2 云端

- `docker compose up --build` 可以同时启动 Web 和 Gateway；
- Web 只调用相对 `/api` 路径；
- EKS 从清单部署 Web Deployment/Service；
- Web readiness/liveness 生效；
- Gateway 和 Provider 默认不直接暴露公网；
- 通过唯一公开入口完成多轮请求；
- 浏览器无 CORS/mixed-content 错误；
- 前端镜像标签不可变并接入现有构建/部署脚本；
- smoke test 覆盖 Web 页面、Gateway health 和一次 chat completion；
- 管理与监控接口没有被意外公开。

## 15. 风险与控制

| 风险 | 控制措施 |
|---|---|
| 一次迁移导致 UI 回归 | 先组件化复制现有视觉，再增加功能；保留截图基线 |
| localStorage 数据损坏 | Zod 校验、schemaVersion、migration、错误回退 |
| Markdown XSS | 不启用 raw HTML，不用 innerHTML，限制 URL/图片 |
| 异步响应写错会话 | request 绑定 conversation ID，按 ID reducer 更新 |
| 历史无限增长 | 客户端预警 + Gateway 硬限制，后续摘要压缩 |
| 修改路由参数命中旧缓存 | cache key 覆盖所有路由约束 |
| Learner Lab 无 ALB IAM | Web NodePort 回退，不阻塞客户端 |
| API 公开导致费用滥用 | API Key、限流、预算护栏、最小公网暴露 |
| 环境 URL 写死 | 相对 `/api` 路径或运行时 config，不使用构建期固定 URL |

## 16. 实施原则

1. 先冻结 Conversation 和 Message 类型，再开始组件开发；
2. 先完成非流式正确性，再做体验增强；
3. Markdown 使用库，PolyGate 只实现定制渲染组件；
4. 先保持现有视觉，再调整 App Shell；
5. Gateway 保持无状态；
6. Web 与 API 使用同源入口；
7. NodePort 是教学回退，不是生产架构；
8. 客户端功能完成不依赖 ALB 权限；
9. 每个阶段都保留 Fixture 离线演示能力；
10. 所有云端部署改动进入脚本和清单，不依赖手工控制台操作作为唯一流程。

## 17. 参考资料

- Vite Getting Started: <https://vite.dev/guide/>
- Vite Static Deployment: <https://vite.dev/guide/static-deploy.html>
- React + TypeScript: <https://react.dev/learn/typescript>
- React `useReducer`: <https://react.dev/reference/react/useReducer>
- react-markdown: <https://github.com/remarkjs/react-markdown>
- remark-gfm: <https://github.com/remarkjs/remark-gfm>
- rehype-sanitize: <https://github.com/rehypejs/rehype-sanitize>
- Zod: <https://zod.dev/>
- Vitest: <https://vitest.dev/guide/>
- React Testing Library: <https://testing-library.com/docs/react-testing-library/intro/>
- Playwright: <https://playwright.dev/docs/intro>
- AWS Load Balancer Controller: <https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html>
- EKS ALB Ingress: <https://docs.aws.amazon.com/eks/latest/userguide/alb-ingress.html>
- CloudFront OAC: <https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html>
- CloudFront Cache Behaviors: <https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesCacheBehavior.html>
