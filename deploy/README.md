# PolyGate Kubernetes Deployment

## Deployment goal

PolyGate 通过版本化清单和脚本部署；探针、资源限制、内部服务边界和 HPA 均应
能够从 Kubernetes 对象及监控指标中验证。

部署前确认目标集群出网、镜像仓库权限、节点架构、Redis 存储策略和 Prometheus
资源余量。默认清单使用 ECR 兼容的镜像仓库与 `linux/amd64` 镜像。

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

## Automation deployment

Automation 的构建和部署均为显式 opt-in，默认
`INCLUDE_AUTOMATION=0`。启用时使用同一个不可变标签执行：

```bash
AUTOMATION_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"

IMAGE_TAG="$AUTOMATION_IMAGE_TAG" INCLUDE_AUTOMATION=1 PUSH_IMAGES=1 \
  ./scripts/build-kubernetes-images.sh

IMAGE_TAG="$AUTOMATION_IMAGE_TAG" INCLUDE_AUTOMATION=1 \
  ./scripts/deploy-kubernetes-application.sh
```

集群内地址固定为 `http://automation:8020`，Service 为 ClusterIP。当前单副本限制
用于保证队列领取的确定性；扩大副本数前必须先验证并发领取不变量。

## Automation Worker

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

## Policy Control Plane

Policy v1 使用 `polygate-routing-policy` ConfigMap 保存 active version 和最多
20 个完整版本。部署脚本只会在 ConfigMap 不存在时，从
`contracts/policy-examples.json["store"]` 创建初始版本；已经存在时会明确输出
`Preserving existing ... version history`，不会用仓库默认值覆盖管理员发布历史。

Automation 是唯一允许更新该 ConfigMap 的控制面。它使用
`polygate-policy-controller` ServiceAccount，RBAC 只允许对
`polygate-routing-policy` 执行 `get` 和 `update`，不能读取 Secret、列举
ConfigMap 或修改其他 Kubernetes 资源。Gateway 和 Worker 保持无写权限，只挂载
只读 Policy Store，并通过私有 Automation Service 每 5 秒刷新。

首次启用 Automation 前创建独立管理密钥：

```bash
read -rsp "Policy admin key: " POLICY_ADMIN_KEY
printf "\n"

kubectl create secret generic polygate-policy-admin \
  --namespace default \
  --from-literal=admin-key="$POLICY_ADMIN_KEY" \
  --dry-run=client \
  --output=yaml \
  | kubectl apply --filename=-

unset POLICY_ADMIN_KEY
```

Kubernetes 只通过只读文件
`/var/run/secrets/polygate-policy/admin-key` 向 Automation 提供密钥，不设置明文
`POLICY_ADMIN_KEY` 环境变量。`INCLUDE_AUTOMATION=1` 时，如果 Secret 或
`admin-key` 缺失，部署脚本会在任何资源变更前退出。

本地 `docker compose up --build` 会先运行一次 `policy-bootstrap`，将冻结契约中的
默认 store 写入私有 named volume，再以只读方式挂载到 Gateway、Automation 和
Worker。仅本地 Compose 显式允许开发密钥：

```text
POLICY_ADMIN_KEY=local-policy-admin-development
POLICY_ALLOW_ENV_ADMIN_KEY=true
```

Automation 的本地 `8020` 端口只绑定 `127.0.0.1`，不会监听局域网网卡。这个明文
开发值不得复制到 Kubernetes 清单、生产日志或浏览器代码。

## Deployment sequence

**先跑本地集成闸门再上云。** 完整顺序见根
[README](../README.md#本地集成闸门部署前必须全绿)：后端与 Web 测试、契约与
部署回归、行为冒烟和安全不变量。

使用和构建、推送时完全相同的标签：

```bash
IMAGE_TAG=<刚才输出的提交号> ./scripts/deploy-kubernetes-application.sh
./scripts/deploy-kubernetes-monitoring.sh
```

部署后用同一批脚本对着 port-forward 再验一遍（这次是真 ConfigMap 持久化，
本地内存仓库验不到）：

```bash
kubectl port-forward deployment/automation 8020:8020 &
kubectl port-forward deployment/prometheus 9090:9090 &
POLICY_ADMIN_KEY=<key> ./scripts/kubernetes-policy-smoke-test.sh
INCLUDE_AUTOMATION=1 INCLUDE_POLICY=1 GRAFANA_PASSWORD=<pw> \
  ./scripts/kubernetes-monitoring-smoke-test.sh
```

## Operational notes

- **Redis 默认使用 `redis.yaml`（emptyDir）**：该模式适合可丢弃缓存；需要保留
  Redis 数据的环境应改用托管 Redis 或 `reference/redis-pvc.yaml` 中的持久化方案。
- **Web NodePort 不通**：当前唯一公开入口固定为 Web `30080`。Security Group 只开放
  该端口，并限制为受信任 CIDR；Gateway 与 Mock Provider 都保持 ClusterIP。
- **providers.yaml 双份**：`contracts/providers.yaml` 是本地 compose 用的（localhost），集群里要用 service DNS（`http://mock-a:8080`）。别混。
- **HPA 看不到扩容**：确认 metrics-server 已安装、Gateway 设置了
  `resources.requests`，并且 node group 仍有扩容余量。
- **监控部署**：实际连接集群前先运行
  `./scripts/kubernetes-monitoring-preflight.sh`。详细说明见
  [`monitoring/README.md`](./monitoring/README.md)。
- **真实 Provider key 用 Secret**：`kubectl create secret generic provider-secrets --from-literal=real-a-key=xxx`，绝不写进清单。为兼容已部署环境，Secret data key 暂时保留旧名 `real-a-key`。
- **Gateway 客户端 key 也用 Secret**：Web、Worker、CLI 使用不同身份。
  `gateway-client-secrets` 的完整创建命令见上面的“Automation Worker”；
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

## Deployment invariants

- 整套应用能够从版本化清单部署到 EKS
- Web 是唯一公开入口，`/api` 同源代理可以完成多轮请求
- 探针/资源限制生效，`kubectl describe` 能看到证据
- Prometheus 能分别发现每个 Gateway Pod，Grafana 能显示 CPU、内存和 HPA 副本变化
