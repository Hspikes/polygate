# PolyGate Web

Vite + React + TypeScript 多轮流式聊天客户端。浏览器维护会话和完整消息历史；所有环境
统一通过同源 `/api` 访问 Gateway。

## 本地开发

```bash
cd web
npm ci
npm run dev
```

打开 <http://localhost:8080>。Vite 会把 `/api/*` 代理到
`http://localhost:8000/*`。Gateway 开启 `POLYGATE_API_KEYS` 时，在仓库根目录 `.env`
中设置独立的 `WEB_GATEWAY_API_KEY`，并把同一值加入 `POLYGATE_API_KEYS`；Vite 只在
开发代理的服务端读取该值，不会把它编译进浏览器代码。如果 Gateway 尚未启动，可
点击“演示回答”加载本地 fixture。

常用验证：

```bash
npm test
npm run lint
npm run build
npm run e2e
```

## Compose

从仓库根目录执行：

```bash
docker compose up --build
```

Web 入口为 <http://localhost:8080>。Nginx 提供静态文件，并把 `/api/*` 反向代理给
Compose 内部的 `gateway:8000`。Nginx 会在运行时用 `WEB_GATEWAY_API_KEY` 覆盖
`/api/*` 请求中的 Authorization；公开 `/v1/*` 则继续透传客户端自己的凭证。可运行
`./scripts/web-smoke-test.sh` 验证静态健康、鉴权就绪、代理和多轮请求。

## 流式回答

真实聊天统一以 `stream=true` 请求标准 OpenAI SSE。浏览器使用增量 UTF-8 解码和 SSE
分帧，在 `[DONE]` 前持续更新当前 assistant message；标准 tool-call/usage chunk 不会被
误当成文本。用户取消会 abort fetch 并关闭上游流。流在 `[DONE]` 前结束时，已收到的
内容会保留并明确标记“回答不完整”，不会伪装为成功。

流正常结束后，Web 使用响应 Header 中的 request ID 查询
`GET /api/v1/decisions/{request_id}`，再展示最终 Provider、成本、tokens、重试和
failover。短暂的 404 会有限重试；记录服务不可用不会丢弃已经完成的回答，而会显示带
request ID 的提示。

## 数据与隐私

- 普通会话以带 `schemaVersion` 的结构保存在当前浏览器 `localStorage`；
- 流式 delta 只更新内存；localStorage 只在完成、失败或取消等稳定状态写入；
- 恢复前使用 Zod 校验，损坏数据会安全回退；
- 高隐私会话自动使用“不保存”模式，刷新后消失；
- 不保存 Gateway Key、Provider Key 或其他凭证；
- Markdown 不启用原始 HTML，外部图片被禁用，链接协议受限。

## 生产镜像

`Dockerfile` 使用 Node 构建静态资源，再交给非特权 Nginx 镜像运行。运行时只监听
8080，提供 `/healthz`、SPA fallback、安全响应头和无缓冲 `/api` 流式代理。容器和
Kubernetes 探针只用 `/healthz` 判断 Nginx 自身是否可用；浏览器在线状态和 Web smoke
继续通过 `/api/v1/models` 判断 Gateway 与内部 Web Key 是否真正就绪。Kubernetes 在
保持 `readOnlyRootFilesystem=true` 的同时，仅为 Nginx 运行时生成配置所需的
`/etc/nginx/conf.d` 挂载受限 `emptyDir`。
