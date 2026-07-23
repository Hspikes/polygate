# PolyGate C1 Automation Kubernetes 骨架设计

## 1. 目标

C1 在不依赖 A/B/D 未完成代码、也不修改当前 EKS 工作负载的前提下，为
Automation API 准备可验证的 Kubernetes 运行骨架和安全的镜像/部署接线入口。

C1 完成后，仓库应能离线证明：

- Automation 可以作为独立 Deployment 运行；
- 集群内服务可以通过 `automation:8020` 访问它；
- Automation 可以通过 Service DNS 访问 Gateway 和 Redis；
- 镜像构建与部署脚本支持 Automation，但默认不会误部署当前内存版本；
- 现有 Gateway、Web、HPA 和监控清单不发生行为变化。

## 2. 范围

### C1 包含

1. 新增 `deploy/automation.yaml`，包含一个 Deployment 和一个 ClusterIP Service。
2. 扩展 `scripts/build-kubernetes-images.sh`，通过
   `INCLUDE_AUTOMATION=1` 选择性构建/推送 Automation 镜像。
3. 扩展 `scripts/deploy-kubernetes-application.sh`，通过同一开关选择性应用并等待
   Automation Deployment。
4. 扩展现有部署回归测试和 Kubernetes 离线 schema 校验。
5. 修正 `deploy/RUNBOOK.md` 中已经过期的集群名和 C1 操作边界。
6. 更新部署文档，写明 C1 只准备接线，不要求立即更新 EKS。

### C1 不包含

- 不创建或修改任何 AWS/ECR/EKS 云资源；
- 不执行 `kubectl apply` 到当前集群；
- 不部署 Agent Service 或 Worker Deployment；
- 不修改 Web Nginx 路由；
- 不添加 Automation HPA；
- 不修改 Prometheus 或 Grafana；
- 不把当前内存 Job Store 描述为生产可用队列；
- 不决定 B 的 Worker 命令、租约、重试和并发策略；
- 不决定 D 的 Agent 端口、SSE 代理和 Secret 名称。

## 3. 运行架构

```text
                   Kubernetes namespace: default

  future Agent -------------------------------------+
                                                    |
                                                    v
                                          automation Service
                                           ClusterIP :8020
                                                    |
                                             automation Pod
                                               replicas: 1
                                               /health
                                                /     \
                                               /       \
                                              v         v
                                     gateway:8000   redis:6379/1
```

Automation 保持 ClusterIP，不创建 NodePort、LoadBalancer 或公网安全组规则。浏览器
不会直接访问该 Service；最终由 D 的 Web/Agent 同源入口代理内部请求。

## 4. Kubernetes 资源设计

### 4.1 Deployment

- 名称：`automation`
- 标签：`app: automation`
- 副本：`1`
- 容器名：`automation`
- 容器端口：命名端口 `http`，值为 `8020`
- 镜像锚点：
  `356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-automation:v1`
- 更新策略：Deployment 默认 RollingUpdate
- ServiceAccount token：`automountServiceAccountToken: false`

当前 API 使用进程内存保存 Preview 和 Job，因此固定单副本。A/B 将状态迁移到 Redis
并通过并发测试前，不增加副本，也不配置 HPA。

### 4.2 环境变量

```text
GATEWAY_URL=http://gateway:8000
AUTOMATION_REDIS_URL=redis://redis:6379/1
```

环境变量只包含内部服务地址，不包含 Secret。后续如果 A/B 增加凭证，应使用
`secretKeyRef`，不得把真实值提交到 YAML。

### 4.3 探针

- startupProbe：`GET /health`，允许应用最多约 60 秒启动；
- readinessProbe：`GET /health`，决定 Service 是否发送流量；
- livenessProbe：`GET /health`，持续失败时重启容器。

所有探针使用命名端口 `http`，避免端口数字在多处漂移。

### 4.4 资源和安全上下文

初始资源预算：

```yaml
requests: { cpu: "50m", memory: "64Mi" }
limits:   { cpu: "300m", memory: "256Mi" }
```

安全约束：

- Pod 使用 `seccompProfile: RuntimeDefault`；
- 容器使用固定非 root UID/GID `10001`；
- `allowPrivilegeEscalation: false`；
- drop 全部 Linux capabilities；
- root filesystem 只读；
- `/tmp` 使用 `emptyDir`；
- 不挂载 Kubernetes API token。

这些约束由本地容器测试和 Kubernetes schema 校验确认。若 Automation 的依赖未来
需要写文件，只允许写显式挂载的临时目录，不放宽整个 root filesystem。

### 4.5 Service

- 名称：`automation`
- 类型：`ClusterIP`
- Service 端口：`8020`
- targetPort：`http`
- selector：`app: automation`

## 5. 安全的构建与部署开关

构建和部署脚本新增：

```text
INCLUDE_AUTOMATION=0   # 默认
```

只有设置为 `1` 时才：

- 构建 `polygate-automation:$IMAGE_TAG`；
- 在 `PUSH_IMAGES=1` 时推送该镜像；
- 替换 `deploy/automation.yaml` 中的固定镜像锚点；
- 应用 Automation 清单并等待 rollout。

脚本只接受 `0` 或 `1`，其他值立即失败。默认值为 `0` 的原因是：当前 Automation
仍使用进程内存 Job Store，且目标 ECR 仓库尚未在 C1 中创建。这样现有 P0/P1 部署
命令保持向后兼容。

未来 B 的 Redis Store/Worker 合并并通过本地集成后，C 再决定是否把默认值改为 `1`。

## 6. 失败处理

- 清单镜像锚点被意外修改：部署脚本在执行 `kubectl apply` 前失败；
- `IMAGE_TAG` 含非法字符：部署脚本立即失败；
- `INCLUDE_AUTOMATION` 不是 `0/1`：构建和部署脚本立即失败；
- `/health` 不可用：readiness 阻止流量，liveness 最终重启容器；
- Gateway 或 Redis 不可用：Automation Pod 不应因此崩溃；业务请求返回明确错误，
  具体重试行为由 A/B 后续实现；
- 当前内存状态导致 Pod 重启后 Job 丢失：作为 C1 已知限制写入文档，不通过增加
  副本掩盖问题。

## 7. 测试设计

实施采用测试先行：

1. 先扩展 `scripts/tests/test-deployment-automation.sh`，要求 Automation 镜像锚点、
   ClusterIP、单副本、安全开关和脚本接线；运行并观察它因缺少 C1 实现而失败。
2. 添加最小清单和脚本实现，再运行同一测试至通过。
3. 将 `deploy/automation.yaml` 加入
   `scripts/kubernetes-monitoring-preflight.sh` 的 kubeconform 校验列表。
4. 运行 `kubectl apply --dry-run=client`/kubeconform 离线检查，不连接 EKS。
5. 构建本地 `linux/amd64` Automation 镜像。
6. 以非 root、只读 root filesystem 启动镜像并检查 `/health`。
7. 运行现有 Automation 7 项 API 测试、契约测试、Compose 配置检查和 Gateway
   回归测试，确认 C1 没有破坏现有接口。

## 8. 提交、推送和同事对接

### 提交点 1：设计冻结

只提交本设计文档：

```text
docs: define C1 Automation Kubernetes design
```

推送后请 A/B 确认：`8020`、`/health`、两个环境变量、当前单副本限制。

### 提交点 2：C1 实现完成

实现与全部离线测试通过后，提交清单、脚本、测试、Runbook 和部署文档：

```text
feat(deploy): add opt-in Automation Kubernetes skeleton
```

推送后给 A/B/D 的交接结论应包括：

- Automation 集群 DNS：`http://automation:8020`；
- 当前默认不会部署到 EKS；
- Worker、Agent 和 Automation metrics 尚未接线；
- A/B/D 完成运行接口后必须使用统一交接模板 @C。

## 9. C1 完成判据

- 新清单通过 Kubernetes 1.34 strict schema validation；
- Automation Service 明确为 ClusterIP；
- Automation Deployment 明确为单副本；
- 安全上下文和资源限制存在且容器可以实际启动；
- 构建/部署脚本默认行为与现有 P0/P1 完全一致；
- `INCLUDE_AUTOMATION=1` 的渲染路径通过本地测试；
- 所有现有相关回归测试通过；
- 没有执行任何 AWS 写操作或 EKS 部署；
- 文档明确记录当前内存状态限制和后续 A/B/D 对接点。
