# deploy/  —— 成员 C（Kubernetes 与可观测性）

## 每次重新开工先看这个
> **Session 过期 / 合盖太久 / 页面卡住重刷之后，先看 [`RUNBOOK.md`](./RUNBOOK.md)。**
> 5 分钟刷新凭证 + 确认环境状态，别每次都重新排查一遍。

## 你的验收物（来自项目书）
> 从清单部署；探针、资源限制和 HPA 有可见证据。

## 第 1 天（不依赖任何人的代码）
1. 验证集群出网、Envoy/Gateway、Redis 存储类、Prometheus 资源余量（对照 AWS 教程）
2. `kubectl apply -f redis.yaml`，确认 PVC 绑定、Pod Running
3. 用一个 hello 镜像跑通 Deployment+Service+NodePort 部署链路（教程 case study）
4. 搞顺 build→push→镜像仓库 流程（Learner Lab 用 ECR 或公共仓库）

## 部署顺序（第 4 天集成）
```bash
kubectl apply -f redis-no-pvc.yaml      # 注意：用这个，不是 redis.yaml（见下方说明）
kubectl apply -f mock-providers.yaml   # 先把 REGISTRY 换成你的镜像地址
kubectl apply -f gateway.yaml          # ConfigMap 里的 providers.yaml 用集群内 DNS
kubectl apply -f hpa.yaml              # 需要 metrics-server
```

## 关键提醒（踩过的坑）
- **Redis 用 `redis-no-pvc.yaml`（emptyDir），不是 `redis.yaml`（PVC 版）**：这个账号的
  EBS CSI Driver 插件因 Learner Lab 的 IAM 权限限制，Pod Identity 角色关联失败，
  controller 一直 `CrashLoopBackOff`，排查后判定修复成本过高，改用 emptyDir。
  Redis 只是缓存，Pod 重启丢缓存可接受。`redis.yaml` 留着，万一以后 IAM 通了可以切回去。
- **NodePort 不通**：EKS 节点默认拒绝入站，必须按教程给 worker 节点的 EC2 网卡加 Security Group（All TCP / 0.0.0.0/0），且每次新 session 都要重做。
- **providers.yaml 双份**：`contracts/providers.yaml` 是本地 compose 用的（localhost），集群里要用 service DNS（`http://mock-a:8080`）。别混。
- **HPA 看不到扩容**：确认 metrics-server 已装、gateway 设了 resources.requests、node group 扩容上限够高（已手动设为 min=2/desired=2/max=4）。
- **真实 key 用 Secret**：`kubectl create secret generic provider-secrets --from-literal=real-a-key=xxx`，绝不写进清单。

## P0 完成判据
- 整套 P0 能从清单部署到 EKS
- 探针/资源限制生效，`kubectl describe` 能看到证据
