# deploy/  —— 成员 C（Kubernetes 与可观测性）

## 每次重新开工先看这个
> **Session 过期 / 合盖太久 / 页面卡住重刷之后，先看 [`RUNBOOK.md`](./RUNBOOK.md)。**
> 5 分钟刷新凭证 + 确认环境状态，别每次都重新排查一遍。

## 你的验收物（来自项目书）
> 从清单部署；探针、资源限制和 HPA 有可见证据。

## 第 1 天（不依赖任何人的代码）
1. 验证集群出网、Envoy/Gateway、Redis 存储类、Prometheus 资源余量（对照 AWS 教程）
2. `kubectl apply -f redis.yaml`，确认使用 `emptyDir` 的 Redis Pod Running
3. 用 Web 镜像跑通 Deployment+Service+NodePort 部署链路（教程 case study）
4. 搞顺 build→push→镜像仓库 流程（Learner Lab 用 ECR 或公共仓库）

## 本地构建镜像

脚本默认使用当前 Git 提交号作为不可变镜像标签，避免重新使用旧 `v1`：

```bash
./scripts/build-kubernetes-images.sh
```

脚本默认构建 Gateway、Mock Provider 和 Web 三个稳定镜像。登录 ECR 后再显式开启
推送，并记住输出的 `IMAGE_TAG`：

```bash
PUSH_IMAGES=1 ./scripts/build-kubernetes-images.sh
```

## C1 Automation 私有接线

**C1 不立即部署到 EKS**。当前 Automation 仍使用进程内存保存 Preview 和 Job，
Redis Store/Worker 集成通过前只准备清单和脚本，不创建 ECR 仓库、不推送镜像、
也不修改当前集群。

Automation 的构建和部署均为显式 opt-in，默认
`INCLUDE_AUTOMATION=0`，现有 P0/P1 命令行为不变。未来完成本地集成后，使用同一个
不可变标签执行：

```bash
C1_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"

IMAGE_TAG="$C1_IMAGE_TAG" INCLUDE_AUTOMATION=1 PUSH_IMAGES=1 \
  ./scripts/build-kubernetes-images.sh

IMAGE_TAG="$C1_IMAGE_TAG" INCLUDE_AUTOMATION=1 \
  ./scripts/deploy-kubernetes-application.sh
```

集群内地址固定为 `http://automation:8020`，Service 为 ClusterIP。当前单副本限制
必须保持到 A/B 将状态迁移到 Redis 并完成并发测试之后。

## C2 Automation Worker 接线

Automation 已改用 Redis DB 0，并通过 `automation:` key prefix 隔离状态。
Worker 与 Automation API 共用同一个镜像，但使用独立 Deployment 和启动命令：

```text
Automation API:  uvicorn automation.app.main:get_app --factory --host 0.0.0.0 --port 8020
Worker:          python -m automation.app.worker
```

当前为了保证队列领取的确定性，API 和 Worker 都固定为单副本，Worker 使用
`strategy: Recreate` 和 `WORKER_CONCURRENCY=1`。Automation API 的 liveness
继续使用 `/health`，readiness 改用真正检查 Redis 的 `/ready`。Worker 不创建
Service；Kubernetes 探针和 Prometheus 直接访问 Pod 的 `9000` 端口。

Gateway 代码在本地仍允许 opt-in 认证，但 Kubernetes 部署要求启用认证，且 Web
Deployment 需要一个独立的服务端身份。为避免 Web 和 Worker 分别更新同一个
Secret 时互相覆盖，部署前一次性创建完整的 `gateway-client-secrets`。
`api-keys` 是 Gateway 接受的 key 列表，
`web-api-key` 和 `worker-api-key` 分别只注入对应客户端：

```bash
read -s WEB_GATEWAY_KEY
printf "\n"
read -s WORKER_GATEWAY_KEY
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

如需为 CLI 或 Pi 增加独立身份，把对应 key 追加到 `api-keys` 的逗号分隔列表，
不要复用 Web 或 Worker 身份。部署脚本只检查 key 名，不会输出 Secret 内容；
Secret 或必需 key 缺失时会在任何应用资源更新前退出。

部署时仍显式开启 Automation：

```bash
IMAGE_TAG=<刚构建并推送的不可变标签> INCLUDE_AUTOMATION=1 \
  ./scripts/deploy-kubernetes-application.sh
./scripts/deploy-kubernetes-monitoring.sh
```

部署完成后，在两个终端分别保留私有端口转发：

```bash
kubectl port-forward service/automation 8020:8020
kubectl port-forward deployment/automation-worker 9000:9000
```

然后运行端到端检查：

```bash
./scripts/kubernetes-automation-smoke-test.sh

GRAFANA_PASSWORD=<当前管理员密码> INCLUDE_AUTOMATION=1 \
  ./scripts/kubernetes-monitoring-smoke-test.sh
```

这个检查会验证 `/ready`、Worker 健康、Redis 幂等入队、Worker 调用 Gateway
完成任务、七项 Worker Prometheus 指标、Prometheus Worker target，以及 Grafana
Automation 面板。Automation API、Worker 和监控均保持集群内部访问，不增加
NodePort 或 LoadBalancer。

## 部署顺序（第 4 天集成）

使用和构建、推送时完全相同的标签：

```bash
IMAGE_TAG=<刚才输出的提交号> ./scripts/deploy-kubernetes-application.sh
./scripts/deploy-kubernetes-monitoring.sh
```

## 关键提醒（踩过的坑）
- **Redis 用 `redis.yaml`（emptyDir）**：这个账号的
  EBS CSI Driver 插件因 Learner Lab 的 IAM 权限限制，Pod Identity 角色关联失败，
  controller 一直 `CrashLoopBackOff`，排查后判定修复成本过高。Redis 只是缓存，
  Pod 重启丢缓存可接受。PVC 参考版本保存在 `reference/redis-pvc.yaml`。
- **Web NodePort 不通**：当前唯一公开入口固定为 Web `30080`。Security Group 只开放
  该端口，并尽量限制为演示者 IP；Gateway 与 Mock Provider 都保持 ClusterIP。
- **providers.yaml 双份**：`contracts/providers.yaml` 是本地 compose 用的（localhost），集群里要用 service DNS（`http://mock-a:8080`）。别混。
- **HPA 看不到扩容**：确认 metrics-server 已装、gateway 设了 resources.requests、node group 扩容上限够高（已手动设为 min=2/desired=2/max=4）。
- **监控部署**：实际连接集群前先运行
  `./scripts/kubernetes-monitoring-preflight.sh`。详细说明见
  [`monitoring/README.md`](./monitoring/README.md)。
- **真实 Provider key 用 Secret**：`kubectl create secret generic provider-secrets --from-literal=real-a-key=xxx`，绝不写进清单。
- **Gateway 客户端 key 也用 Secret**：Web、Worker、CLI 使用不同身份。
  `gateway-client-secrets` 的完整创建命令见上面的“C2 Automation Worker 接线”；
  不要用只包含部分 key 的命令覆盖已有 Secret。
  Web Key 仅由 Nginx 运行时读取；不要使用 `VITE_` 前缀，也不要写入镜像、JavaScript
  或日志。公网开放 `/v1` 前必须创建该 Secret。

## Web 入口验证

Web Pod 的 readiness/liveness 只检查 `/healthz`，用于判断非特权 Nginx 和静态页面
是否可服务；它不会因 Gateway 短暂故障而从 Service endpoints 中移除。浏览器在线
状态和下方 smoke test 会另外调用鉴权后的 `/api/v1/models`。Web 根文件系统保持
只读，运行时模板只写入挂载在 `/etc/nginx/conf.d` 的 `emptyDir`。

```bash
kubectl get service web
WEB_BASE=http://<节点公网IP>:30080 ./scripts/web-smoke-test.sh
```

如暂时不开放 Security Group，可先执行 `kubectl port-forward service/web 8080:80`，
再使用默认 `WEB_BASE` 运行 smoke test。故障注入管理端只通过受控的 port-forward 访问。

## P0 完成判据
- 整套 P0 能从清单部署到 EKS
- Web 是唯一公开入口，`/api` 同源代理可以完成多轮请求
- 探针/资源限制生效，`kubectl describe` 能看到证据
- Prometheus 能分别发现每个 Gateway Pod，Grafana 能显示 CPU、内存和 HPA 副本变化
