# gateway/  —— 成员 A（网关与策略）

## 你的验收物（来自项目书）
> 同一请求能解释为何选择某 Provider —— 决策卡片里的 `reason` 必须是人话。

## 本地开发（不依赖 B 的 Mock）
```bash
cd gateway
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PROVIDERS_FILE=../contracts/providers.yaml
FAKE_ADAPTER=1 uvicorn app.main:app --reload   # 用假 Adapter，返回罐头数据
```
访问 http://localhost:8000/providers 看注册表；POST /v1/chat/completions 测路由。

## Gateway 指标

Gateway 在 `GET /metrics` 暴露 Prometheus 文本格式的运行指标。它记录：

- 每个聊天请求的结果与端到端耗时，包括 400/403/422 客户端错误
- 缓存命中/未命中
- 各 Provider 的调用结果与耗时
- 成功调用消耗的输入/输出 token
- 成功调用的估算费用

本地启动 Gateway 后可直接检查：

```bash
curl http://localhost:8000/metrics
```

每个 `POST /v1/chat/completions` 只计数一次。请求结果包括
`success`、`cache_hit`、`client_error`、`routing_error`、
`provider_error` 和未预期的 `server_error`。指标标签只包含 Provider
和这些有限的结果类型；`request_id`、prompt、路由原因和原始错误文本
继续写入日志，不进入指标标签。

## 接入 B 的 Mock（第 3 天集成）
去掉 `FAKE_ADAPTER=1`，用 `docker compose up` 起全套即可。

## Pi Agent 可运行闭环

Gateway v0.3 支持 Pi 使用的 OpenAI Chat Completions 子集：content blocks、
`developer/tool` 消息、`tools/tool_choice`、增量 tool call、SSE usage 和
`data: [DONE]`。工具仍由 Pi 在本地执行，Gateway 不获得工作区权限。

本地启动 Mock、Gateway 后执行：

```bash
git submodule update --init --recursive
docker compose up --build redis mock-a mock-b gateway

PI_BIN="$(command -v pi)" \
POLYGATE_BASE_URL=http://127.0.0.1:8000/v1 \
POLYGATE_API_KEY=local-development \
./scripts/pi-gateway-smoke-test.sh
```

测试会完成两次模型调用：第一次从 Mock 流式收到 `read` tool call，Pi
读取扩展中的固定 fixture，第二次把 tool result 发回 Gateway 并收到
`mock ok`。测试不调用真实模型，也不会修改工作区文件。

设置 `POLYGATE_API_KEYS` 后，`/v1/models` 和 `/v1/chat/completions` 要求
Bearer Key；变量为空时保留原有本地 Web 开发兼容性：

```bash
POLYGATE_API_KEYS=local-development docker compose up --build gateway
```

所有 `stream=true`、tools、tool history、content-block、消息元数据、会话 ID
和显式生成参数请求默认绕过 P0 精确缓存，避免不同语义的请求复用旧答案。
流式请求只允许在首个下游 SSE event 之前重试或切换 Provider，输出开始后
发生错误会终止流，不拼接其他 Provider 的输出。Gateway 会为内部计费请求
usage，但只在客户端设置 `stream_options.include_usage=true` 时向下游透传
usage-only chunk。

远程 Pi 通过 Web/Nginx 公网入口使用 `https://<host>/v1`；该位置关闭
响应/请求缓冲并保留 Authorization。生产环境必须配置非默认客户端 Key。

## 你负责的文件
- `app/main.py`     入口、缓存查、组装决策卡片、request_id（**已种下，别删**）
- `app/router.py`   ⭐ 路由核心，P0 的规则都在这，扩展策略只改这里
- `app/adapters.py` Adapter 归一化（真实 API 的鉴权细节和 B 一起弄）
- `app/cache.py`    精确/规范化缓存（normalize 规则是和 B 的共享契约）
- `app/cost.py`     成本估算
- `app/registry.py` 读 providers.yaml

## P0 完成判据
- `/v1/chat/completions` 端到端返回「答案 + 决策卡片」
- 卡片包含 chosen_provider / reason / cache_hit / cost / latency / tokens / request_id
- 改约束（quality/privacy/max_cost）能看到路由结果和 reason 相应变化

## 留给 P1 的 TODO（别在 P0 做）
- main.py 里 provider 调用失败处 → 加重试 + 熔断 + failover（B 主导）
- HEALTH 静态表 → 换成 B 的实时健康探测
