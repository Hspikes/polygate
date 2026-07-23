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

脚本会构建 Gateway、Mock Provider 和 Web 三个镜像。登录 ECR 后再显式开启推送，
并记住输出的 `IMAGE_TAG`：

```bash
PUSH_IMAGES=1 ./scripts/build-kubernetes-images.sh
```

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
- **Agent 客户端 key 也用 Secret**：`kubectl create secret generic gateway-client-secrets --from-literal=api-keys='<long-random-key>'`。多个过渡期 key 可用英文逗号分隔；公网开放 `/v1` 前必须创建该 Secret。

## Web 入口验证

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
