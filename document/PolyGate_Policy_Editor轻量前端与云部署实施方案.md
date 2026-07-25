# PolyGate Policy Editor 轻量前端与云部署实施方案

> 状态：团队决策稿，可进入实施
> 日期：2026-07-25
> 适用范围：Task 7 私有 Policy Editor 及其 Docker、Compose、EKS 交付
> 取代方案：原“React + TypeScript + JSON Forms + XState + Zod + MSW”前端方案已废弃，不再作为本轮实施依据

相关基线：

- [`Policy Management 总体设计`](../docs/superpowers/specs/2026-07-24-policy-management-design.md)
- [`Policy Management 实施计划`](../docs/superpowers/plans/2026-07-24-policy-management.md)
- [`Task 7 交接说明`](./PolyGate_D_Task7私有PolicyEditor交接说明_2026-07-25.md)
- [`Policy Draft v1 Schema`](../contracts/policy.schema.json)
- [`Policy 联调 Examples`](../contracts/policy-examples.json)
- [`Automation Kubernetes Deployment`](../deploy/automation.yaml)

## 1. 最终结论

本轮 Policy Editor 采用：

> **自托管 Alpine CSP 构建 + 原生 HTML/CSS/JavaScript + 配置驱动表单 + 浏览器 Fetch API**

具体约束：

- 页面仍是 Automation 镜像内的静态资源；
- 不创建独立 SPA 工程；
- 不引入 Vite、React、TypeScript、JSON Forms、XState、Zod 或 MSW；
- 不引入 Node build stage 或 Node runtime；
- Alpine 使用 CSP-friendly build，并作为固定版本的本地静态文件提交；
- 生产页面不引用 CDN、外部字体、外部图标或其他公网资源；
- 页面与 Policy API 同源，通过 `/v1/...` 绝对路径请求；
- 管理员密钥只保存在 `policy-admin.js` 的私有 IIFE 闭包变量中；
- Automation 继续使用 ClusterIP，只通过 `kubectl port-forward` 访问；
- 表单字段、范围和固定场景以 Policy v1 Schema 与交接说明为准；
- 只实现已交付的 v1 API，不为 capabilities、schema v2、分页或未来管理员模块预埋运行时代码。

该方案不是通用管理平台，而是当前后端已冻结接口上的最小安全编辑器。

## 2. 为什么废弃旧方案

旧方案建立在“后端仍不稳定”的前提上，因此设计了：

- 前端 Domain/Port/Adapter 多层抽象；
- MockPolicyAdapter 和 MSW 故障注入；
- Zod transport decoder；
- XState 发布状态机；
- JSON Forms 和 UI Schema registry；
- capabilities、unknown schema version 和未来 v2 Adapter；
- 独立 `automation/admin-ui` Node 工程和多阶段镜像。

截至本方案形成时，Policy v1 Schema、管理 API、Gateway simulation、Worker hot reload、ConfigMap、Secret、RBAC 和监控均已进入 `main`，Task 7 交接说明也给出了逐端点实测结果。继续实施旧方案会把一次课程项目内部管理页扩展成长期管理平台，不能有效缩短当前关键路径。

旧方案预计 5,200–7,600 行手写代码、8–11 人日；本方案预计 1,500–2,250 行、2.5–4 人日。减少的主要是未来兼容层、Mock 基础设施、构建链和重复测试，不减少 Validate、Preview、Publish、History、Compare、Rollback 与安全门禁。

## 3. 云部署复核结论

### 3.1 Automation 镜像

当前 `automation/Dockerfile` 已执行：

```dockerfile
COPY automation /app/automation
```

因此 `automation/admin/` 及本地 vendor 文件会自动进入镜像。无需增加 Node stage，也无需在运行镜像中安装前端工具链。

前端资源只读，不需要写入容器文件系统，与 Kubernetes 中的：

```yaml
readOnlyRootFilesystem: true
runAsNonRoot: true
```

兼容。浏览器负责 Alpine 运行时和页面状态，服务端资源请求量很小，不需要提高 Automation 当前 CPU/内存 request 或 limit。

### 3.2 Compose

Compose 已把 Automation 绑定到：

```text
127.0.0.1:8020:8020
```

页面与 API 同源，不需要 CORS。开发环境使用显式启用的 `POLICY_ALLOW_ENV_ADMIN_KEY=true`；这只是后端读取本地 key 的方式，key 仍由管理员手动输入页面，不能注入生成的 HTML 或 JavaScript。

Compose 使用内存 Policy Repository，发布结果在容器重启后恢复到基线版本。这是本地联调语义，不应由前端模拟持久化。

### 3.3 EKS

`service/automation` 为 ClusterIP：

```text
浏览器
  │ kubectl port-forward service/automation 8020:8020
  ▼
Automation Pod
  ├── /admin/policies
  ├── /admin/assets/*
  └── /v1/admin/policies*
```

本方案不新增 NodePort、LoadBalancer、Ingress、公共 Web 代理或安全组规则。Grafana 只能提供打开编辑器的提示/链接，不能携带管理员密钥。

生产后端从挂载文件读取 `POLICY_ADMIN_KEY_FILE`。浏览器不会读取 Kubernetes Secret，也不会通过页面 API自动获取 key；授权管理员通过既有运维渠道取得 key 后手动输入。

### 3.4 无外部运行依赖

Alpine CSP 的固定版本产物提交到仓库，并随 Automation 镜像发布。页面加载时不得访问：

- npm registry；
- jsDelivr、unpkg 或其他 CDN；
- Google Fonts；
- 外部图标服务；
- Grafana API；
- 公网 Web NodePort。

这样可以在无公网出口、DNS 受限或课堂 EKS 环境中完整加载。

### 3.5 CSP 决策

无构建 Vue 的浏览器模板编译模式可能要求运行时模板编译，并使严格 CSP 出现额外分叉。为避免为一个私有编辑页开放 `unsafe-eval`，最终选择 Alpine 官方 CSP-friendly build。

页面至少返回：

```text
Content-Security-Policy:
  default-src 'self';
  script-src 'self';
  style-src 'self';
  img-src 'self' data:;
  connect-src 'self';
  object-src 'none';
  base-uri 'none';
  frame-ancestors 'none';
  form-action 'none'
```

实现中禁止内联 `<script>`、内联 `<style>`、`x-html` 和 `dangerouslySetInnerHTML` 等等价能力。后端 change note、created_by、错误信息全部作为文本渲染。

脚本使用固定顺序的外部 `defer` 资源：

```html
<script defer src="/admin/assets/policy-admin.js"></script>
<script defer src="/admin/assets/vendor/alpine-csp.min.js"></script>
```

`policy-admin.js` 先注册 `alpine:init` 监听器，Alpine CSP 随后启动；不得通过内联脚本解决初始化顺序。

### 3.6 缓存与滚动发布

页面和第一方 JS/CSS 返回：

```text
Cache-Control: no-store
```

原因是当前没有前端构建步骤和 hash 文件名。禁止缓存可以避免 Automation 滚动更新期间 `index.html` 与旧 `policy-admin.js` 混用。Vendor 文件虽然有版本和校验和，也可统一使用 `no-store`，简化私有管理页行为。

页面不注册 service worker，不使用 Cache Storage，也不保存离线草稿。Pod 重建、Redis 重启和浏览器刷新不会留下前端持久化状态。

## 4. 工程结构

```text
automation/
├── admin/
│   ├── index.html
│   ├── policy-admin.js
│   ├── policy-admin.css
│   └── vendor/
│       ├── alpine-csp.min.js
│       └── README.md
├── app/
│   └── main.py
├── tests/
│   └── test_policy_admin_ui.py
├── Dockerfile
└── README.md
```

`vendor/README.md` 必须记录：

- Alpine 包名和精确版本；
- 上游下载地址；
- SHA-256；
- 许可证；
- 更新步骤。

Vendor 产物不计入手写代码量，但必须进入代码审查和镜像安全扫描。

## 5. 页面结构

```text
PolicyAdminApp
├── Admin key gate
├── Active policy status
├── Gateway controls
├── Urgency score controls
├── Scenario controls
├── Queue controls
├── Locked guardrails
├── Validation summary
├── Impact preview
│   ├── Diff table
│   ├── Routing before/after
│   ├── Priority before/after
│   └── Queue order before/after
├── Change note
├── Validate / Preview / Publish actions
└── Version history
    ├── Compare
    └── Rollback
```

页面无路由器、无嵌套路由，也不支持 `/admin/policies/{version}` 浏览器深链接。所有功能位于一个页面，版本选择保存在当前内存状态。

## 6. 配置驱动表单

Policy v1 有 34 个可见控件，但不需要手写 34 份重复逻辑。`policy-admin.js` 定义字段描述：

```js
const GATEWAY_FIELDS = [
  {
    path: "gateway.assumed_output_tokens",
    label: "Assumed output tokens",
    kind: "number",
    min: 1,
    max: 32768,
    step: 1,
  },
  // ...
];
```

Alpine `x-for` 负责渲染控件，`readField(path)` 和 `writeField(path, value)` 负责嵌套对象读写。字段配置包括：

- path；
- label；
- input/select 类型；
- min、max、step；
- enum options；
- help text；
- disabled；
- 单位。

Scenario 只循环四个固定 key，不允许新增或删除。`finance_summary.defaults.privacy` 始终渲染为 disabled `high`。

本地即时校验只覆盖：

- JSON Schema 已定义的类型和范围；
- `critical > high > normal > low`；
- finance privacy 为 high；
- change note 长度 1–500。

服务端 Validate 仍是最终权威，本地通过不能跳过服务端请求。

## 7. 状态与发布门禁

不引入 XState。页面只维护：

```text
baseVersion
draftRevision
validatedRevision
previewedRevision
activePolicy
draft
validation
preview
history
changeNote
busyAction
```

任何字段变化执行：

```text
draftRevision += 1
validatedRevision = null
previewedRevision = null
validation = null
preview = null
```

Preview 按钮启用条件：

```text
validatedRevision == draftRevision
```

Publish 按钮启用条件：

```text
validatedRevision == draftRevision
previewedRevision == draftRevision
preview.base_version == baseVersion
changeNote.trim().length in 1..500
busyAction == null
```

`busyAction` 防止 Validate、Preview、Publish 和 Rollback 重复提交。

错误转换：

- 401：清除闭包中的 admin key，回到 key gate；
- 404：显示目标版本不存在；
- 409：显示交接文档规定的固定冲突文案，禁止继续发布；
- 422：按 `detail[].loc` 映射到字段 path，并结合本地字段配置展示范围；
- 503/网络错误：保留草稿，允许重试；
- 非 JSON/未知响应：显示协议错误，不猜测成功。

## 8. 管理员密钥

密钥只保存在：

```js
let adminKey = "";
```

该变量位于 `policy-admin.js` 的 IIFE 私有闭包，不进入 Alpine reactive state，也不挂到 `window`。第一方脚本先注册 `alpine:init` 监听器，随后由本地 Alpine CSP 脚本启动组件。连接时读取密码输入框的当前 value，写入闭包后立即清空输入框。

禁止：

- URL、query、hash；
- localStorage、sessionStorage、IndexedDB；
- cookie；
- Alpine store；
- DOM attribute 或 `data-*`；
- console、analytics、错误对象和错误文案；
- service worker、Cache API；
- HTML 或 JavaScript 构建时注入。

页面刷新、401 和显式断开都清除密钥。

## 9. API 对接

只实现交接中已冻结的端点：

| 功能 | 端点 | 前端行为 |
|---|---|---|
| Active | `GET /v1/policies/active` | 初始化或发布后重载 |
| History | `GET /v1/admin/policies` | 当前页面显示全部版本 |
| Version | `GET /v1/admin/policies/{version}` | compare/rollback 前读取 |
| Validate | `POST /v1/admin/policies/validate` | 成功后记录 revision |
| Preview | `POST /v1/admin/policies/preview` | 渲染 diff 和三类 simulation |
| Publish | `POST /v1/admin/policies/publish` | 接受 201，随后重载 active/history |
| Rollback | `POST /v1/admin/policies/{version}/rollback` | 接受 201，随后重载 |

`request()` 统一完成：

- Bearer header 注入；
- JSON request/response；
- 201、204、304 分支；
- 401/404/409/422/503 映射；
- 响应错误脱敏。

第一版不轮询 active policy，因此不会主动发送 `If-None-Match`。若后续增加轮询，必须保存 ETag 并把 304 当作成功且无 body 的结果。

Preview 使用源码内固定且版本化的 gateway/priority cases，不提供测试用例编辑器。这样可覆盖演示路径，又不把 Task 7 扩展成 simulation case builder。

## 10. FastAPI 静态资源

预期路由：

```text
GET /admin/policies                         -> index.html
GET /admin/assets/policy-admin.js           -> JavaScript
GET /admin/assets/policy-admin.css          -> CSS
GET /admin/assets/vendor/alpine-csp.min.js  -> pinned vendor asset
```

FastAPI 负责：

- 使用 `StaticFiles` 挂载 `/admin/assets`；
- 显式提供 `/admin/policies`；
- 对 `/admin/*` 响应添加 CSP 和 `Cache-Control: no-store`；
- 静态目录缺失时启动失败或给出清晰错误，不静默返回空白页；
- 不把 Secret/env 中的 key 渲染到 HTML。

静态资源路径使用绝对同源路径，不能依赖工作目录或浏览器当前 URL。

## 11. 测试策略

### 11.1 Python serving/security tests

`automation/tests/test_policy_admin_ui.py` 至少验证：

- `/admin/policies` 返回 200；
- 三个静态资源返回 200；
- Content-Type 正确；
- CSP 存在且不包含 `unsafe-eval`、`unsafe-inline`；
- `Cache-Control: no-store`；
- HTML 只引用同源资源；
- HTML/第一方 JS 不包含 admin key；
- 第一方 JS 不使用 localStorage、sessionStorage、cookie、IndexedDB、service worker；
- Finance privacy、五个 Gateway 字段、四个 urgency 和四个 scenario 标识存在；
- Validate、Preview、Publish、History、Compare、Rollback 控件存在。

### 11.2 逻辑测试

关键纯函数保持无 DOM 依赖并导出到测试可访问区域：

- nested path read/write；
- draft change invalidation；
- `canPreview`、`canPublish`；
- urgency 跨字段校验；
- 422 loc 到字段 path；
- compare diff；
- 409/401/503 行为。

本轮不为此单独创建完整 Vitest 工程。核心流程由 Task 10 的浏览器/API 集成回归覆盖。

### 11.3 Docker/Compose smoke

```bash
docker compose up -d --build automation
curl -i http://localhost:8020/admin/policies
curl -i http://localhost:8020/admin/assets/policy-admin.js
```

检查浏览器 Network：

- 所有资源来自 `localhost:8020`；
- 没有 CDN/公网请求；
- key 不出现在 URL 和请求日志；
- Publish 返回 201；
- 刷新后重新要求输入 key。

### 11.4 EKS smoke

```bash
kubectl port-forward service/automation 8020:8020
```

验收：

1. 页面和本地 vendor 资源加载成功；
2. 未创建 Automation NodePort/LoadBalancer；
3. 使用授权 key 后读取 active/history；
4. 修改策略后必须 Validate → Preview → Publish；
5. Gateway 和 Worker 在约 5 秒内收敛到新版本；
6. Grafana 显示 active version 和 publish outcome；
7. Rollback 创建新版本；
8. Pod/Redis 重启后历史仍由 ConfigMap 恢复；
9. 页面刷新后 key 消失；
10. 浏览器没有任何外部网络依赖。

## 12. 工作量

| 工作项 | 手写代码量 | 预计人日 |
|---|---:|---:|
| HTML/Alpine 页面模板 | 180–260 | 0.4–0.6 |
| 表单配置、状态和 API | 650–900 | 1–1.5 |
| Preview/History/Compare/Rollback | 250–400 | 0.5–0.75 |
| CSS | 250–400 | 0.4–0.6 |
| FastAPI、安全头、测试和文档 | 220–350 | 0.5–0.75 |
| 联调余量 | — | 0.3–0.6 |
| **合计** | **1,500–2,250** | **2.5–4** |

不计入：固定版本的 vendor minified 文件、已有 Policy API 测试和 Task 11 的集群故障恢复修复。

如果增加可编辑 Preview cases、草稿持久化、多人账号、审计检索、schema v2 或多资源管理中心，需要重新评审技术栈，不能把这些需求悄悄塞入本轮。

## 13. 实施顺序

1. 增加失败的静态资源、安全头和安全字符串测试；
2. 挂载 `/admin/assets` 与 `/admin/policies`；
3. Vendor 固定版本 Alpine CSP 并记录 checksum/license；
4. 实现 key gate、active/history 加载；
5. 实现配置驱动表单与即时校验；
6. 实现 revision 门禁；
7. 实现 Validate、Preview、Publish；
8. 实现 History、Compare、Rollback；
9. 完成错误映射和安全清理；
10. 完成 Docker/Compose smoke；
11. 交给 Task 10/11 做本地跨组件与 EKS 验证。

## 14. 完成判据

- [ ] Task 7 交接要求的全部页面功能存在；
- [ ] 只使用已冻结 v1 API；
- [ ] 页面无 CDN 和外部运行依赖；
- [ ] Automation 镜像无 Node build/runtime；
- [ ] CSP 不需要 `unsafe-eval` 或 `unsafe-inline`；
- [ ] 管理员 key 只驻留私有 JS closure；
- [ ] 编辑后旧 Validate/Preview 必然失效；
- [ ] 409 不覆盖新版本；
- [ ] 503 保留当前草稿；
- [ ] Finance privacy 锁定；
- [ ] Preview、History、Compare、Rollback 可用；
- [ ] Compose 和 EKS 使用同一份静态文件；
- [ ] Automation 仍为 ClusterIP；
- [ ] Gateway/Worker 热更新演示通过。

## 15. 被明确否决的内容

以下内容从本轮方案中删除，后续不得以“架构预留”名义重新加入：

- `automation/admin-ui` 独立 Node 工程；
- React、TypeScript、JSON Forms；
- XState；
- Zod transport decoder；
- MSW 和有状态 MockPolicyAdapter；
- PolicyControlPlane Port/Adapter 多版本体系；
- capabilities/schema endpoints；
- schema v2 registry；
- Future HttpPolicyV2Adapter；
- Refine、Appsmith 或其他低代码平台；
- 公网管理入口；
- 浏览器草稿持久化。

未来如果管理员中心扩展到 Policy、Providers、API Keys、Audit Logs 等至少三个独立资源，再单独提出管理平台升级 ADR。升级不能阻塞当前 Task 7。

## 参考资料

- Alpine CSP build：<https://alpinejs.dev/advanced/csp>
- Alpine installation：<https://alpinejs.dev/essentials/installation>
- Vue standalone integration：<https://vuejs.org/guide/extras/ways-of-using-vue.html>
- FastAPI StaticFiles：<https://fastapi.tiangolo.com/tutorial/static-files/>
