# deploy/  —— 成员 C（Kubernetes 与可观测性）

## 你的验收物（来自项目书）
> 从清单部署；探针、资源限制和 HPA 有可见证据。

## 第 1 天（不依赖任何人的代码）
1. 验证集群出网、Envoy/Gateway、Redis 存储类、Prometheus 资源余量（对照 AWS 教程）
2. `kubectl apply -f redis.yaml`，确认 PVC 绑定、Pod Running
3. 用一个 hello 镜像跑通 Deployment+Service+NodePort 部署链路（教程 case study）
4. 搞顺 build→push→镜像仓库 流程（Learner Lab 用 ECR 或公共仓库）

## 部署顺序（第 4 天集成）
```bash
kubectl apply -f redis.yaml
kubectl apply -f mock-providers.yaml   # 先把 REGISTRY 换成你的镜像地址
kubectl apply -f gateway.yaml          # ConfigMap 里的 providers.yaml 用集群内 DNS
kubectl apply -f hpa.yaml              # 需要 metrics-server
```

## 关键提醒（踩过的坑）
- **NodePort 不通**：EKS 节点默认拒绝入站，必须按教程给 worker 节点的 EC2 网卡加 Security Group（All TCP / 0.0.0.0/0），且每次新 session 都要重做。
- **providers.yaml 双份**：`contracts/providers.yaml` 是本地 compose 用的（localhost），集群里要用 service DNS（`http://mock-a:8080`）。别混。
- **HPA 看不到扩容**：确认 metrics-server 已装、gateway 设了 resources.requests、node group 扩容上限够高（见提案里给老师的问题）。
- **真实 key 用 Secret**：`kubectl create secret generic provider-secrets --from-literal=real-a-key=xxx`，绝不写进清单。

## P0 完成判据
- 整套 P0 能从清单部署到 EKS
- 探针/资源限制生效，`kubectl describe` 能看到证据
