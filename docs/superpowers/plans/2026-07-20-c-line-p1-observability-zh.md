# PolyGate C 线 P1 可观测性实施计划（中文版）

> **给执行者：** 按任务顺序逐项完成；每个任务完成后先验证，再进入下一项。你在本机终端执行命令并保留输出；不要把 AWS 凭证、Secret 或密码提交到 Git。

**目标：** 在 EKS 上完成 C 线 P1：metrics-server、CPU HPA 扩缩容证据、私有 Prometheus/Grafana 监控大屏，以及清晰的 Git 和团队交接。用户侧的单入口 LoadBalancer 在所有功能冻结后再做。

**架构：** Gateway 在 P1 期间继续通过现有 NodePort 对外。metrics-server 为 HPA 提供 Kubernetes Resource Metrics API。Prometheus、kube-state-metrics、Grafana 都部署在 `default` 命名空间，并使用 `ClusterIP`；管理员仅通过 `kubectl port-forward` 访问。Prometheus 和 Grafana 使用 `emptyDir`，容器重启后历史数据可丢失，这是 Learner Lab 环境可接受的取舍。

**技术栈：** EKS 1.34、Kubernetes Metrics Server、`autoscaling/v2` HPA、Prometheus 3.13.1、kube-state-metrics 2.19.1、Grafana 12.4.0、Bash、Docker、ECR。

## 全局约束

- AWS 账号：`356029564744`。
- Region：`us-east-1`。
- EKS 集群：`G3EKS`。
- 节点架构：`x86_64`；Apple Silicon Mac 推送的应用镜像必须是 `linux/amd64`。
- 应用命名空间：`default`。
- Redis、Prometheus、Grafana 全部用 `emptyDir`；不依赖 EBS CSI。
- Prometheus、Grafana、kube-state-metrics 只能是 `ClusterIP`，不能改成公网 NodePort。
- Monitoring API 仍仅用于本地 Compose，不部署进 EKS。
- 永远不要提交 AWS key、session token、`~/.kube/config`、DeepSeek key、Grafana 密码或任何 Secret YAML。
- 在 P1 监控和 HPA 验收完成前，不创建用户前端的 LoadBalancer。

---

## 任务 1：修复新账号下的部署自动化

**要修改的文件：**

- `scripts/build-kubernetes-images.sh`
- `scripts/deploy-kubernetes-application.sh`
- `scripts/kubernetes-monitoring-preflight.sh`
- `deploy/monitoring/README.md`

**为什么先做：** 现有监控清单已在仓库中，但三个脚本还保留旧账号 `896133844534` 和 Gateway `v1` 的锚点；构建脚本也没有指定 `linux/amd64`。直接执行会在新账号/Apple Silicon 环境失败。

- [ ] **1.1 先复现当前 preflight 问题**

```bash
./scripts/kubernetes-monitoring-preflight.sh
```

预期：最后的 image-anchor 检查因仍要求旧账号和 `v1` 而失败。

- [ ] **1.2 修改镜像构建脚本**

在 `scripts/build-kubernetes-images.sh` 中，把 registry 设置改为：

```bash
ECR_REGISTRY="${ECR_REGISTRY:-356029564744.dkr.ecr.us-east-1.amazonaws.com}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
PUSH_IMAGES="${PUSH_IMAGES:-0}"
```

两处 `docker build` 都增加平台参数：

```bash
docker build \
  --platform "$TARGET_PLATFORM" \
  --pull \
  --tag "$GATEWAY_IMAGE" \
  "$ROOT_DIR/gateway"

docker build \
  --platform "$TARGET_PLATFORM" \
  --pull \
  --tag "$MOCK_IMAGE" \
  "$ROOT_DIR/providers/mock"
```

- [ ] **1.3 修改应用部署脚本的锚点**

在 `scripts/deploy-kubernetes-application.sh` 中使用：

```bash
ECR_REGISTRY="${ECR_REGISTRY:-356029564744.dkr.ecr.us-east-1.amazonaws.com}"
PINNED_GATEWAY_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-gateway:v2"
PINNED_MOCK_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-mock:v1"
```

保留脚本现有的 `sed` 临时替换逻辑：它应把 Git SHA tag 临时应用到集群，而不是重写仓库内的固定清单。

- [ ] **1.4 修改监控 preflight**

在 `scripts/kubernetes-monitoring-preflight.sh` 中，把两处：

```bash
-kubernetes-version 1.35.0
```

改为：

```bash
-kubernetes-version 1.34.0
```

把文件结尾处旧镜像锚点检查替换为：

```bash
if grep -Fq \
  "356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-gateway:v2" \
  "$ROOT_DIR/deploy/gateway.yaml" \
  && grep -Fq \
    "356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-mock:v1" \
    "$ROOT_DIR/deploy/mock-providers.yaml"; then
  ok "Application manifests contain the current ECR image anchors"
else
  echo "Current application image anchors are missing" >&2
  exit 1
fi
```

- [ ] **1.5 更新监控 README 的构建示例**

在 `deploy/monitoring/README.md` 中，将镜像构建例子改为：

```bash
ECR_REGISTRY=356029564744.dkr.ecr.us-east-1.amazonaws.com \
TARGET_PLATFORM=linux/amd64 \
PUSH_IMAGES=1 \
./scripts/build-kubernetes-images.sh
```

说明：部署 Prometheus/Grafana 不需要重新构建已有 Gateway；此命令只用于下一个应用版本发布。

- [ ] **1.6 本地验证**

```bash
bash -n scripts/build-kubernetes-images.sh
bash -n scripts/deploy-kubernetes-application.sh
bash -n scripts/kubernetes-monitoring-preflight.sh
git diff --check
./scripts/kubernetes-monitoring-preflight.sh
```

预期：前三条退出码是 `0`，`git diff --check` 无输出，preflight 所有检查通过。

- [ ] **1.7 第一次 commit 与 push**

```bash
git add scripts/build-kubernetes-images.sh \
  scripts/deploy-kubernetes-application.sh \
  scripts/kubernetes-monitoring-preflight.sh \
  deploy/monitoring/README.md
git commit -m "fix(deploy): align monitoring automation with new EKS account"
git push origin feat/tan
```

**此处必须 push。** 这些是可复用的源码变更，而且已经完成本地验证。不要把运行时输出、截图或 Secret 一起提交。

---

## 任务 2：安装并验证 metrics-server

**仓库文件：** 正常安装成功时不需要修改任何文件。metrics-server 是运行中的集群组件，不是代码提交。

- [ ] **2.1 刷新 Learner Lab 凭证并确认目标集群**

```bash
aws sts get-caller-identity
aws eks --region us-east-1 update-kubeconfig --name G3EKS
kubectl config current-context
kubectl get nodes
```

预期：账号为 `356029564744`，context 末尾含 `cluster/G3EKS`，两个节点均为 Ready。

- [ ] **2.2 记录安装前状态**

```bash
kubectl get deployment metrics-server -n kube-system
kubectl get apiservice v1beta1.metrics.k8s.io
```

预期：安装前其中一个或两个资源为 NotFound。

- [ ] **2.3 优先安装 EKS Community Add-on**

```bash
aws eks create-addon \
  --region us-east-1 \
  --cluster-name G3EKS \
  --addon-name metrics-server \
  --resolve-conflicts OVERWRITE
```

如果 Learner Lab 返回 `AccessDeniedException`，只使用一次官方 manifest 后备方案：

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

不要同时安装两种方式。

- [ ] **2.4 验证 Metrics API**

```bash
kubectl rollout status deployment/metrics-server \
  --namespace kube-system \
  --timeout=180s
kubectl get apiservice v1beta1.metrics.k8s.io
kubectl top nodes
kubectl top pods --namespace default
```

预期：APIService 为 Available，`kubectl top` 显示节点和 Pod 的 CPU/内存。

- [ ] **2.5 若不可用，先采集证据再修改**

```bash
kubectl describe deployment metrics-server -n kube-system
kubectl logs deployment/metrics-server -n kube-system --tail=100
kubectl get events -n kube-system --sort-by=.lastTimestamp
```

只有日志明确显示 kubelet 证书校验失败时，才讨论添加 `--kubelet-insecure-tls`；不能凭猜测添加。

- [ ] **2.6 Git 决策**

标准安装成功时，不 commit、不 push。

---

## 任务 3：部署并压测 HPA

**现有文件：** `deploy/hpa.yaml`。

- [ ] **3.1 部署 HPA 并等待 CPU 指标出现**

```bash
kubectl apply -f deploy/hpa.yaml
kubectl get hpa gateway-hpa
kubectl describe hpa gateway-hpa
```

约一分钟后，`TARGETS` 应显示 CPU 百分比而不是 `<unknown>`。

- [ ] **3.2 为压测建立本地 tunnel**

终端 A：

```bash
kubectl port-forward service/gateway 18000:80
```

保持运行。这样压测不依赖节点公网 IP，也不会受安全组影响。

- [ ] **3.3 观察 HPA 和 Gateway Pod**

终端 B：

```bash
kubectl get hpa,pods -w
```

- [ ] **3.4 产生不命中缓存、且不花真实 API 费用的负载**

终端 C：

```bash
for batch in {1..100}; do
  for item in {1..20}; do
    curl -sS -o /dev/null \
      -X POST http://localhost:18000/v1/chat/completions \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"mock-b\",\"messages\":[{\"role\":\"user\",\"content\":\"hpa-load-${batch}-${item}-${RANDOM}\"}],\"polygate\":{\"quality\":\"cheap\",\"privacy\":\"standard\",\"max_cost_usd\":0.01}}" &
  done
  wait
done
```

每条请求都有唯一内容，不能命中 Redis；同时强制调用 `mock-b`，不会调用付费 DeepSeek。

- [ ] **3.5 记录扩缩容证据**

```bash
kubectl get hpa gateway-hpa
kubectl get deployment gateway
kubectl get pods -l app=gateway -o wide
```

预期：负载期间 desired replicas 大于 2；停止负载并等待 HPA 稳定窗口后，副本回到 2。

- [ ] **3.6 未扩容时的唯一调整顺序**

先把压测的内层循环由 `{1..20}` 改为 `{1..40}` 再运行一次。只有 `kubectl top pods` 已证明 CPU 仍无法达到 60% 时，才考虑调整 `deploy/hpa.yaml` 的 `averageUtilization`。

- [ ] **3.7 Git 决策**

若 `deploy/hpa.yaml` 没改，不 commit、不 push。若修改了阈值或新增了可复用压测脚本，先运行对应语法检查和扩缩容验收，再提交并 push。

---

## 任务 4：部署私有 Kubernetes 监控栈

**已有资源，不需重写：**

- `deploy/monitoring/rbac.yaml`
- `deploy/monitoring/kube-state-metrics.yaml`
- `deploy/monitoring/prometheus.yaml`
- `deploy/monitoring/grafana.yaml`
- `deploy/monitoring/kustomization.yaml`
- `monitoring/prometheus/prometheus-kubernetes.yml`
- `monitoring/grafana/dashboards/polygate-overview.json`
- `monitoring/grafana/provisioning/datasources/prometheus.yml`
- `monitoring/grafana/provisioning/dashboards/polygate.yml`

**数据路径：** Gateway Pod 的 `/metrics`、kubelet cAdvisor CPU/内存、kube-state-metrics 的 Deployment/HPA 状态都会被 Prometheus 收集；Grafana 直接查询 Prometheus。

- [ ] **4.1 先做本地 preflight**

```bash
./scripts/kubernetes-monitoring-preflight.sh
```

只要有 schema、Prometheus 配置、dashboard 或 image-anchor 检查失败，就不要部署到 EKS。

- [ ] **4.2 交互式创建 Grafana 管理员 Secret**

```bash
read -s GRAFANA_ADMIN_PASSWORD
kubectl create secret generic polygate-grafana-admin \
  --namespace default \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="$GRAFANA_ADMIN_PASSWORD" \
  --dry-run=client \
  --output=yaml \
  | kubectl apply --filename=-
unset GRAFANA_ADMIN_PASSWORD
```

`read -s` 输入时不会显示字符；输完密码按 Enter。不得粘贴 Secret 输出或密码到聊天与 Git。

- [ ] **4.3 部署**

```bash
./scripts/deploy-kubernetes-monitoring.sh
```

预期：`kube-state-metrics`、`prometheus`、`grafana` 均 rollout 成功，Service 类型仍是 ClusterIP。

- [ ] **4.4 验证 Workload、Service 与 RBAC**

```bash
kubectl get deployment gateway prometheus grafana kube-state-metrics
kubectl get service prometheus grafana kube-state-metrics
kubectl get hpa gateway-hpa
kubectl auth can-i get nodes/proxy \
  --as=system:serviceaccount:default:polygate-prometheus
```

预期：所有 Deployment Available；三个 Service 均为 ClusterIP；HPA 存在；RBAC 命令输出 `yes`。

- [ ] **4.5 管理员私有访问**

终端 A：

```bash
kubectl port-forward service/prometheus 9090:9090
```

终端 B：

```bash
kubectl port-forward service/grafana 3000:3000
```

浏览器打开：

- `http://localhost:9090/targets`
- `http://localhost:3000`

Grafana 用户名为 `admin`，密码为 4.2 中输入的值。

- [ ] **4.6 运行完整云端监控 smoke test**

终端 C：

```bash
read -s GRAFANA_PASSWORD
export GRAFANA_PASSWORD
./scripts/kubernetes-monitoring-smoke-test.sh
unset GRAFANA_PASSWORD
```

预期：Prometheus ready、每个 Gateway target UP、kube-state-metrics UP、所有 cAdvisor node target UP、Deployment/HPA 指标存在、Gateway CPU/内存指标存在、Grafana ready、受保护 dashboard 完整，全部通过。

- [ ] **4.7 截取答辩证据**

保存以下截图：

```text
Prometheus Targets：Gateway、kube-state-metrics、cAdvisor 全部 UP
Grafana：请求率、错误率、P95、缓存命中、成本、CPU、内存、HPA 面板
kubectl get hpa gateway-hpa：CPU 与 desired replicas
kubectl get pods：扩容时 Gateway 的副本数
```

- [ ] **4.8 Git 决策**

已有清单若原样成功，无需 push。只有真实集群暴露并修复了清单/配置/脚本问题时，重新跑 preflight 和 smoke test 后才提交。例如：

```bash
git add deploy/monitoring monitoring scripts
git diff --cached
git commit -m "fix(monitoring): validate EKS monitoring deployment"
git push origin feat/tan
```

提交前必须检查暂存区，不能把队友的无关改动一起提交。

---

## 任务 5：团队交接与演示冻结

- [ ] **5.1 发送以下对接文案到团队群**

```text
C 线 P1 云端监控已部署到 G3EKS：metrics-server、gateway HPA、Prometheus、kube-state-metrics、Grafana 均已完成验证。

访问边界：Prometheus/Grafana 都是 ClusterIP，不对公网开放；管理演示由 C 使用 kubectl port-forward。D 不需要 AWS 凭证或 kubeconfig，本地仍可用 docker compose 验证页面。

请 A 在演示冻结前不要改 /metrics 的指标名和 provider/outcome/result 标签；如必须修改请先通知 C，因为 Grafana 查询依赖这些名称。

请 B 在 HPA/监控验收期间不要执行 mock-b 故障注入；压测只使用唯一请求并强制路由到 mock-b，不调用付费 DeepSeek。

D 可以继续完成用户前端与同源 API 配置。用户单入口 LoadBalancer 会在功能冻结后由 C 部署，监控页面不会挂到用户公网入口。
```

- [ ] **5.2 答辩演示顺序**

```text
1. 打开 PolyGate 用户页并提交请求。
2. 展示决策卡片，再次提交展示缓存命中。
3. 通过 port-forward 打开私有 Grafana，展示请求、成本、延迟指标。
4. 发起 Mock-only 压测，展示 HPA 副本增加。
5. 停止负载，展示副本回到 2。
6. 说明 Prometheus/Grafana 是私有管理员面，不对用户公网开放。
```

- [ ] **5.3 最终 Git 检查**

```bash
git status --short
git log --oneline -5
```

预期：没有未提交的源码变更。课程若要求上传截图，先遮盖账号、IP、密码和 token。

---

## 延后任务：用户单网址入口

只在 A/B/D 功能冻结、P1 监控完成后实施：

```text
Internet -> LoadBalancer -> web/Nginx
                         -> / 返回静态前端
                         -> /v1 与 /providers 反代到 gateway:80
```

D 负责前端的同源 API 逻辑和 `web/Dockerfile`；C 负责 web ECR 镜像、Nginx 代理配置、Kubernetes Deployment 和 LoadBalancer Service。Prometheus、Grafana、`/metrics` 与 Mock 管理接口都不能被加入公网路由。
