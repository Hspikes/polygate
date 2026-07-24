# PolyGate D 线 Pull Request 代码审查报告

## 1. 报告信息

| 项目 | 内容 |
|---|---|
| 项目名称 | PolyGate 云原生智能网关 |
| 审查日期 | 2026-07-24 |
| 审查人 | Member C（Kubernetes 与可观测性） |
| 审查基线 | `origin/main` |
| 审查终点 | `754acce` |
| 当前结论 | 功能实现整体完整，但存在 3 个 EKS 部署阻塞问题；修复前不应重新部署新镜像 |

本报告审查 D 同事近期合入 `main` 的 Web、Gateway 可靠性、监控和决策记录相关改动，重点关注：

- 新功能做了什么；
- 是否符合现有接口与安全边界；
- 是否影响 Kubernetes 部署；
- 是否影响 Prometheus/Grafana；
- 是否与后续 Policy Management 工作冲突；
- 当前是否可以重新构建并部署到 EKS。

---

## 2. 审查范围

本次审查覆盖以下 4 个已合并 PR：

| PR | Commit | 标题 | 主要范围 |
|---|---|---|---|
| #27 | `cfdf580` | `feat(web): secure gateway auth and error contract` | Web 服务端认证、错误分类、Request ID 展示 |
| #28 | `05635bb` | `feat(monitoring): add reliable SLI and alerts` | Gateway SLI、Prometheus rules、Grafana 面板和告警 |
| #29 | `024f109` | `feat(gateway): bound retries with reliability budgets` | Provider 重试、时间预算、failover、504 和相关指标 |
| #31 | `754acce` | `feat(gateway): persist redacted decision records` | Redis 短期决策记录和查询 API |

这些改动已经存在于本地工作树，当前 `HEAD` 与 `origin/main` 均指向 `754acce`。

---

## 3. 功能变更说明

### 3.1 PR #27：Web 服务端身份与错误契约

该 PR 将 Web 到 Gateway 的认证从浏览器侧移到 Nginx 服务端边界。

主要实现：

1. 新增 `WEB_GATEWAY_API_KEY`。
2. Web Nginx 在转发 `/api/*` 时覆盖浏览器传入的 `Authorization`：

   ```nginx
   proxy_set_header Authorization $web_gateway_authorization;
   ```

3. `/v1/*` 保持面向 CLI、Pi 和其他 OpenAI 客户端，继续转发客户端自己的 Bearer Key。
4. Web 前端新增错误类型：

   - `auth`
   - `rate_limit`
   - `timeout`
   - `budget`
   - `routing`
   - `provider`
   - `validation`
   - `network`

5. Web 可以从响应头读取 `X-PolyGate-Request-ID`。
6. 错误界面显示并允许复制 Request ID。
7. Web 的 Gateway 在线检测改为调用经过认证的 `/api/v1/models`。

安全意义：

- Web API Key 不进入 Vite 构建变量；
- Key 不进入浏览器 JavaScript；
- 浏览器不能自行替换 Web 内部身份；
- 公共 `/v1` 和浏览器 `/api` 保持不同认证边界。

### 3.2 PR #28：可靠 SLI、Recording Rules 与告警

该 PR重新定义了 Gateway 可用性指标，避免把所有 4xx 和客户端取消都算成服务故障。

新增观测维度：

- Service Error Rate；
- Client Rejection Rate；
- Cancellation Rate；
- Provider Circuit Breaker State；
- Gateway target availability；
- Provider error/timeout；
- Gateway Pod restart；
- HPA 达到最大副本。

新增 Prometheus recording rules：

```text
polygate:gateway_service_requests:rate5m
polygate:gateway_service_errors:rate5m
polygate:gateway_service_error_ratio:rate5m
polygate:gateway_client_rejection_ratio:rate5m
polygate:gateway_cancellation_ratio:rate5m
```

新增告警：

```text
GatewayTargetDown
HighProviderErrorOrTimeoutRate
GatewayP95LatencyAboveSLO
ProviderCircuitOpenTooLong
GatewayPodRestarting
GatewayHPAAtMaxReplicas
```

同时更新了：

- `monitoring/prometheus/polygate-rules.yml`
- Kubernetes Prometheus 配置；
- Grafana Overview Dashboard；
- Monitoring API 契约；
- Monitoring API 查询和测试；
- 监控部署脚本和 preflight。

当前项目没有部署 Alertmanager，因此这些告警只会显示在：

```text
http://localhost:9090/alerts
```

不会自动发送邮件、Slack 或其他通知。这是当前设计限制，不是本次代码错误。

### 3.3 PR #29：Gateway 可靠性预算

该 PR为 Gateway 增加有限重试、failover 和请求级时间预算。

新增环境变量：

```text
PROVIDER_TIMEOUT_SECONDS=10
PROVIDER_STREAM_IDLE_TIMEOUT_SECONDS=90
GATEWAY_NON_STREAM_BUDGET_SECONDS=30
GATEWAY_STREAM_START_BUDGET_SECONDS=30
PROVIDER_MAX_RETRIES=2
PROVIDER_RETRY_BASE_DELAY_SECONDS=0.2
PROVIDER_RETRY_MAX_BACKOFF_SECONDS=5
```

主要行为：

1. 对网络错误、超时、408、409、429 和 5xx 进行有限重试。
2. 使用指数退避和 jitter。
3. 支持上游 `Retry-After`。
4. 每次 Provider 尝试不能超过当前请求剩余预算。
5. 自动路由请求可在首个 Provider 失败后切换到下一个 Provider。
6. 用户强制指定 Provider 时不会静默 failover。
7. Provider 超时或请求预算耗尽返回 HTTP 504。
8. 所有 Chat 请求，包括 401、422、502、504，都会获得 Request ID。
9. 流式请求只允许在下游 200 响应提交前进行 retry/failover。

新增指标：

```text
polygate_provider_retries_total
polygate_failovers_total
polygate_streams_total
polygate_request_budget_exhausted_total
```

相比原有“Provider 失败后直接 502”的行为，新流程为：

```text
Provider 调用
→ 可重试错误则有限重试
→ 自动模式下尝试 failover
→ 超过请求预算返回 504
→ 不可恢复错误返回 502
```

### 3.4 PR #31：Redis 短期决策记录

该 PR新增：

```http
GET /v1/decisions/{request_id}
```

Decision Record 保存：

- Request ID；
- 请求最终状态；
- 最初 Provider；
- 最终 Provider；
- 路由原因；
- cache hit；
- 是否为 stream；
- 估算成本；
- 延迟；
- token 数量；
- retries；
- failover 次数；
- 创建时间和过期时间。

明确不保存：

- prompt；
- messages；
- tool arguments；
- Authorization；
- Provider API Key；
- Provider endpoint；
- 原始异常内容。

记录保存到 Redis DB 0，默认 TTL：

```text
DECISION_RECORD_TTL_SECONDS=3600
```

由于当前 EKS Redis 使用 `emptyDir`，Redis Pod 重建后 Decision Record 会消失。这与其“短期、可丢失”的用途一致。

---

## 4. 验证结果

本次审查执行了以下实际验证。

### 4.1 Gateway 测试

在 Python 3.12、`linux/amd64` Gateway 镜像中执行完整测试：

```text
59 passed
5 skipped
4 deprecation warnings
```

跳过项不属于失败；warning 来自 FastAPI 现有 `on_event` 生命周期弃用提示。

### 4.2 Web 测试

在 Web build stage 镜像中执行：

```text
Web unit/component tests: 45 passed
Web lint:                 passed
Web production build:     passed
```

### 4.3 Kubernetes Monitoring Preflight

执行：

```bash
./scripts/kubernetes-monitoring-preflight.sh
```

结果：

```text
10 local pre-deployment checks passed
```

其中包括：

- Kubernetes schema validation；
- Prometheus 配置验证；
- 11 条 Prometheus rule 验证；
- Grafana PromQL 验证；
- ConfigMap 渲染；
- ECR image anchor 检查。

### 4.4 Web Deployment Regression

执行：

```bash
python3 scripts/tests/test-web-deployment.py
```

结果：

```text
4 passed
1 failed
```

失败内容：

```text
AssertionError: '/api/v1/models' != '/healthz'
```

### 4.5 只读 Web 容器启动验证

使用与 Kubernetes 相同的只读根文件系统条件运行 Web 镜像：

```bash
docker run \
  --rm \
  --read-only \
  --tmpfs /tmp \
  -e WEB_GATEWAY_API_KEY=review-key \
  polygate-web:review
```

结果：

```text
20-envsubst-on-templates.sh:
can't create /etc/nginx/conf.d/default.conf:
Read-only file system
```

这证明当前 Web 镜像不能按 `deploy/web.yaml` 的安全配置在 EKS 中启动。

---

## 5. 审查发现

### 5.1 P1：Nginx 模板无法写入只读根文件系统

涉及文件：

- `web/Dockerfile`
- `deploy/web.yaml`

原因：

1. Web 镜像将 Nginx 配置复制为：

   ```text
   /etc/nginx/templates/default.conf.template
   ```

2. 容器启动时，Nginx entrypoint 使用 `envsubst` 生成：

   ```text
   /etc/nginx/conf.d/default.conf
   ```

3. Kubernetes 配置启用了：

   ```yaml
   readOnlyRootFilesystem: true
   ```

4. 当前只给 `/tmp` 挂载 `emptyDir`，没有给 `/etc/nginx/conf.d` 提供可写卷。

影响：

- 新 Web Pod 启动即退出；
- Deployment rollout 无法完成；
- Web NodePort 无可用 endpoints；
- 重新部署新版本会造成用户入口不可用。

推荐修复：

```yaml
volumeMounts:
  - name: nginx-tmp
    mountPath: /tmp
  - name: nginx-conf
    mountPath: /etc/nginx/conf.d

volumes:
  - name: nginx-tmp
    emptyDir: {}
  - name: nginx-conf
    emptyDir: {}
```

应保留 `readOnlyRootFilesystem: true`，不建议通过关闭只读根文件系统规避问题。

### 5.2 P1：Web readiness 与 Gateway 错误耦合

涉及文件：

- `deploy/web.yaml`
- `scripts/tests/test-web-deployment.py`

当前 readiness：

```yaml
readinessProbe:
  httpGet:
    path: /api/v1/models
```

问题：

1. 已导致现有部署回归测试失败。
2. Gateway 暂时故障时，Web Pod 会变成 NotReady。
3. 两个 Web Pod 都会从 Service endpoints 中移除。
4. 用户无法打开静态页面查看明确错误信息。
5. Web 的静态服务能力和 Gateway 的业务可用性被错误合并。

推荐方案：

```yaml
readinessProbe:
  httpGet:
    path: /healthz

livenessProbe:
  httpGet:
    path: /healthz
```

浏览器内部的“网关在线”状态继续使用：

```text
/api/v1/models
```

这样可以同时满足：

- Kubernetes 判断 Web/Nginx 自身是否可用；
- 浏览器判断 Gateway 和 Web 内部身份是否可用。

### 5.3 P1：部署脚本没有提前验证必需 Secret

涉及文件：

- `deploy/web.yaml`
- `deploy/gateway.yaml`
- `deploy/automation.yaml`
- `scripts/deploy-kubernetes-application.sh`
- `deploy/README.md`

Web 现在强制要求：

```text
Secret: gateway-client-secrets
Key:    web-api-key
```

该引用不是 optional。Secret 或 key 缺失时，Pod 会进入：

```text
CreateContainerConfigError
```

但部署脚本没有在修改集群前验证 Secret，而是在应用 Redis、Mock、Gateway 和 Web 后才等待 rollout。

影响：

- 部署过程产生部分更新；
- Web rollout 卡住；
- 错误出现得晚且不直观；
- 启用 Automation 时可能同时遗漏 Worker Key。

完整 Secret 应至少包含：

```text
api-keys
web-api-key
worker-api-key
```

建议部署脚本在任何 `kubectl apply` 前执行 fail-fast 检查：

```bash
kubectl get secret gateway-client-secrets \
  --namespace "$NAMESPACE"
```

并检查 key 名，不输出 key 值。

启用认证和 Automation 时，建议一次性创建：

```bash
read -rsp "Web Gateway key: " WEB_GATEWAY_KEY
printf "\n"
read -rsp "Worker Gateway key: " WORKER_GATEWAY_KEY
printf "\n"

kubectl create secret generic gateway-client-secrets \
  --namespace default \
  --from-literal=api-keys="$WEB_GATEWAY_KEY,$WORKER_GATEWAY_KEY" \
  --from-literal=web-api-key="$WEB_GATEWAY_KEY" \
  --from-literal=worker-api-key="$WORKER_GATEWAY_KEY" \
  --dry-run=client \
  --output=yaml \
  | kubectl apply --filename=-

unset WEB_GATEWAY_KEY WORKER_GATEWAY_KEY
```

CLI Key 如需独立身份，可继续加入 `api-keys` 的逗号分隔列表。

### 5.4 P2：Policy Management 契约编号冲突

D 新增：

```text
Contract #11: decision-record.schema.json
```

但当前 Policy Management 实施计划仍要求：

```text
Add contract rows 11–13
```

因此未来新增 Policy 契约时会发生编号重复。

推荐：

- 将 Policy 契约编号改为 `#12–#14`；或
- 删除人工连续编号，以文件名作为唯一稳定标识。

该问题不会破坏当前运行，但必须在 Policy Management Task 1 契约 PR 前修正。

---

## 6. 非阻塞说明

### 6.1 Prometheus 告警不会主动通知

当前有告警规则，但没有 Alertmanager。

可以用于：

- 演示告警 pending/firing；
- Grafana/Prometheus 页面展示；
- 课程汇报说明。

暂时不能：

- 发邮件；
- 发 Slack/Teams；
- 自动通知管理员。

### 6.2 Decision Record 不是永久审计日志

Decision Record：

- 只保留默认 1 小时；
- Redis 重启后可能丢失；
- 不适合作为合规审计数据；
- 适合演示 Request ID 追踪和短期故障排查。

### 6.3 EKS 仍运行旧镜像

本次仅拉取代码，没有自动构建或部署新镜像。

现有 EKS 上的旧镜像不会因为 `git pull` 改变。只有执行以下流程后，云端才会更新：

```text
build linux/amd64
→ push ECR
→ deploy immutable tag
→ rollout
```

因此当前线上环境暂时不会受到 Web 启动问题影响。

---

## 7. 对 C 线工作的影响

### 7.1 需要新增或修改的 Kubernetes 配置

C 线后续需要处理：

1. 为 `/etc/nginx/conf.d` 增加 `emptyDir`。
2. 恢复 Web readiness `/healthz`。
3. 在部署脚本加入 Secret fail-fast。
4. 统一 `gateway-client-secrets` 三个 key。
5. 将新 Gateway reliability 环境变量纳入部署文档。
6. 保留 `DECISION_RECORD_TTL_SECONDS=3600`。
7. 重新部署 Prometheus rules 和 Grafana dashboard。
8. 重新运行 Web、Gateway、Monitoring 和 Automation smoke。

### 7.2 与 Policy Management 的兼容性

可以复用的能力：

- Request ID；
- Decision Record；
- retry/failover 指标；
- circuit breaker 指标；
- Prometheus alert rules；
- Web 服务端身份。

后续需要继续修改：

- Gateway cache key 加入 `policy_version`；
- CORS expose headers 加入 `X-PolyGate-Policy-Version`；
- Decision Record 是否增加 `policy_version` 需要四人确认；
- Policy 契约编号需要避开现有 #11；
- Grafana 后续增加 active/loaded policy version 和 drift 面板。

---

## 8. 建议修复顺序

### 阻塞修复

1. D 修复 Web `/etc/nginx/conf.d` 可写卷。
2. D/C 对齐 readiness，恢复 `/healthz`。
3. C 更新部署脚本 Secret preflight。
4. C 更新统一 Secret 文档。
5. 重新运行所有本地测试。

### 修复后验证

必须通过：

```text
Web unit tests
Web lint
Web production build
Web deployment test
Web read-only container startup
Gateway full tests
Monitoring preflight
Docker Compose smoke
```

然后才能：

```text
commit
→ push
→ PR review
→ merge main
→ build linux/amd64
→ push ECR
→ deploy EKS
→ run smoke tests
```

---

## 9. 给 D 同事的对接文案

```text
【D 线 PR Review 结果】

我已审查 #27/#28/#29/#31。

通过项：
- Gateway：59 passed，5 skipped
- Web：45 passed
- Web lint/build：passed
- Monitoring preflight：10 passed

当前有三个 EKS 接线阻塞点：

1. Web 镜像启动时会通过 envsubst 写
   /etc/nginx/conf.d/default.conf，
   但 deploy/web.yaml 启用了 readOnlyRootFilesystem=true，
   且只给 /tmp 挂载 emptyDir。
   实际容器验证会报 Read-only file system。
   建议给 /etc/nginx/conf.d 增加 emptyDir，继续保留只读根文件系统。

2. readiness 改为 /api/v1/models 后，
   scripts/tests/test-web-deployment.py 已失败；
   Gateway 故障时还会导致静态 Web 也从 Service endpoints 中消失。
   建议 readiness/liveness 保持 /healthz，
   浏览器在线状态继续检查 /api/v1/models。

3. Web 现在强制依赖 gateway-client-secrets/web-api-key，
   但部署脚本没有提前检查。
   C 线会补 fail-fast，并统一 api-keys、web-api-key、
   worker-api-key 的创建方式。

请先处理前两项并 push，通知我 commit SHA。
我会重新验证 Web 只读容器、部署清单和 smoke，再进入 EKS build/push/deploy。
```

---

## 10. 最终结论

D 本轮实现显著增强了：

- Web 到 Gateway 的认证边界；
- 用户可理解的错误反馈；
- Request ID 追踪；
- Provider retry 和 failover；
- 有界请求时间；
- SLI 与告警；
- 短期决策记录。

Gateway、Web 业务测试和监控语法验证均表现良好。

但是，当前 Web Kubernetes 配置与新 Nginx 模板机制不兼容，已通过真实容器启动验证确认；同时 readiness 和 Secret 接线存在部署回归。因此本轮结论是：

```text
代码功能：基本通过
监控配置：通过
Kubernetes Web 部署：暂不通过
是否可以立即重新部署 EKS：否
```

完成 P1 修复并重新验证后，才能构建新的 `linux/amd64` 镜像并更新 EKS。
