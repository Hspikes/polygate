# PolyGate C-Line P1 Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the C-line P1 deliverables on EKS: metrics-server, CPU-based HPA scaling evidence, private Prometheus/Grafana monitoring, and a clean Git handoff, while deferring the public single-entry LoadBalancer until the application is feature-complete.

**Architecture:** The Gateway remains public through its existing NodePort during P1. Metrics Server supplies the Kubernetes Resource Metrics API used by HPA. Prometheus, kube-state-metrics, and Grafana run as private ClusterIP services in the `default` namespace; the administrator accesses Prometheus and Grafana through `kubectl port-forward`. Prometheus stores two days of data on `emptyDir`, matching the Learner Lab storage constraint.

**Tech Stack:** Amazon EKS 1.34, Kubernetes Metrics Server, Kubernetes HPA `autoscaling/v2`, Prometheus 3.13.1, kube-state-metrics 2.19.1, Grafana 12.4.0, Bash, Docker Buildx, ECR.

## Global Constraints

- AWS account: `356029564744`.
- AWS region: `us-east-1`.
- EKS cluster: `G3EKS`.
- Worker architecture: `x86_64`; every application image pushed from Apple Silicon must target `linux/amd64`.
- Application namespace: `default`.
- Redis, Prometheus, and Grafana use `emptyDir`; no EBS CSI dependency.
- Prometheus, Grafana, and kube-state-metrics remain ClusterIP-only.
- The Monitoring API remains local and is not deployed to EKS in P1.
- Never commit AWS credentials, kubeconfig, DeepSeek keys, Grafana passwords, or Kubernetes Secret YAML containing credential data.
- Do not create the public frontend LoadBalancer until P1 monitoring and HPA evidence are complete.

---

### Task 1: Repair the Deployment Automation for the New AWS Account

**Files:**
- Modify: `scripts/build-kubernetes-images.sh`
- Modify: `scripts/deploy-kubernetes-application.sh`
- Modify: `scripts/kubernetes-monitoring-preflight.sh`
- Modify: `deploy/monitoring/README.md`

**Interfaces:**
- Consumes: existing `gateway/Dockerfile`, `providers/mock/Dockerfile`, and application manifests.
- Produces: scripts that work with account `356029564744`, current manifest anchors, EKS 1.34, and `linux/amd64` builds.

- [ ] **Step 1: Capture the current failure before editing**

Run:

```bash
./scripts/kubernetes-monitoring-preflight.sh
```

Expected before the fix: the final image-anchor check fails because it still requires account `896133844534` and Gateway tag `v1`.

- [ ] **Step 2: Update the build script defaults and platform**

In `scripts/build-kubernetes-images.sh`, replace the registry declaration with:

```bash
ECR_REGISTRY="${ECR_REGISTRY:-356029564744.dkr.ecr.us-east-1.amazonaws.com}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
PUSH_IMAGES="${PUSH_IMAGES:-0}"
```

Add `--platform "$TARGET_PLATFORM"` to both `docker build` commands:

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

- [ ] **Step 3: Update deployment-script image anchors**

In `scripts/deploy-kubernetes-application.sh`, use:

```bash
ECR_REGISTRY="${ECR_REGISTRY:-356029564744.dkr.ecr.us-east-1.amazonaws.com}"
PINNED_GATEWAY_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-gateway:v2"
PINNED_MOCK_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-mock:v1"
```

Keep the script's temporary `sed` substitution behavior: it deploys an immutable Git-derived tag without rewriting the checked-in manifest.

- [ ] **Step 4: Update preflight validation for the current manifests**

In `scripts/kubernetes-monitoring-preflight.sh`, change both kubeconform invocations from:

```bash
-kubernetes-version 1.35.0
```

to:

```bash
-kubernetes-version 1.34.0
```

Replace the old account anchor test at the end with:

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

- [ ] **Step 5: Update the monitoring deployment example**

In `deploy/monitoring/README.md`, replace the image build example with:

```bash
ECR_REGISTRY=356029564744.dkr.ecr.us-east-1.amazonaws.com \
TARGET_PLATFORM=linux/amd64 \
PUSH_IMAGES=1 \
./scripts/build-kubernetes-images.sh
```

Do not rebuild the application merely to deploy monitoring. This command is for the next application release.

- [ ] **Step 6: Run syntax and local preflight checks**

Run:

```bash
bash -n scripts/build-kubernetes-images.sh
bash -n scripts/deploy-kubernetes-application.sh
bash -n scripts/kubernetes-monitoring-preflight.sh
git diff --check
./scripts/kubernetes-monitoring-preflight.sh
```

Expected: shell syntax checks exit `0`, `git diff --check` is silent, and the preflight reports all checks passed.

- [ ] **Step 7: Commit and push checkpoint 1**

Run:

```bash
git add scripts/build-kubernetes-images.sh \
  scripts/deploy-kubernetes-application.sh \
  scripts/kubernetes-monitoring-preflight.sh \
  deploy/monitoring/README.md
git commit -m "fix(deploy): align monitoring automation with new EKS account"
git push origin feat/tan
```

Push now because these are reusable source changes and the local preflight is green. Do not include runtime evidence or secrets in this commit.

---

### Task 2: Install and Verify Metrics Server

**Files:**
- No repository files change unless installation troubleshooting reveals a reproducible configuration requirement.

**Interfaces:**
- Consumes: EKS cluster `G3EKS` and worker kubelet port `10250` connectivity.
- Produces: `metrics.k8s.io/v1beta1`, `kubectl top`, and CPU data for HPA.

- [ ] **Step 1: Refresh credentials and verify the target cluster**

Run:

```bash
aws sts get-caller-identity
aws eks --region us-east-1 update-kubeconfig --name G3EKS
kubectl config current-context
kubectl get nodes
```

Expected: account `356029564744`, context ending in `cluster/G3EKS`, and two Ready nodes.

- [ ] **Step 2: Confirm Metrics API is absent before installation**

Run:

```bash
kubectl get deployment metrics-server -n kube-system
kubectl get apiservice v1beta1.metrics.k8s.io
```

Expected before installation: one or both resources are NotFound.

- [ ] **Step 3: Install the EKS community add-on**

Run:

```bash
aws eks create-addon \
  --region us-east-1 \
  --cluster-name G3EKS \
  --addon-name metrics-server \
  --resolve-conflicts OVERWRITE
```

If Learner Lab returns `AccessDeniedException`, use the AWS-documented manifest fallback exactly once:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

Do not install both methods.

- [ ] **Step 4: Wait for Metrics Server and validate the API**

Run:

```bash
kubectl rollout status deployment/metrics-server \
  --namespace kube-system \
  --timeout=180s
kubectl get apiservice v1beta1.metrics.k8s.io
kubectl top nodes
kubectl top pods --namespace default
```

Expected: the APIService is Available and CPU/memory values are shown for nodes and Pods.

- [ ] **Step 5: Diagnose only if metrics remain unavailable**

Run:

```bash
kubectl describe deployment metrics-server -n kube-system
kubectl logs deployment/metrics-server -n kube-system --tail=100
kubectl get events -n kube-system --sort-by=.lastTimestamp
```

Do not add `--kubelet-insecure-tls` unless the logs specifically show kubelet certificate validation failures.

- [ ] **Step 6: Git checkpoint decision**

Do not commit or push anything if the standard add-on/manifest works unchanged. Runtime installation is cluster state, not repository source.

---

### Task 3: Deploy and Load-Test the HPA

**Files:**
- Existing: `deploy/hpa.yaml`
- Create only if the repeated load command is worth preserving: `scripts/hpa-load-test.sh`

**Interfaces:**
- Consumes: Metrics Server Resource Metrics API and Gateway CPU request `100m`.
- Produces: evidence that `gateway-hpa` scales above two replicas at 60% average CPU and returns to two replicas after load.

- [ ] **Step 1: Apply HPA and wait for CPU readings**

Run:

```bash
kubectl apply -f deploy/hpa.yaml
kubectl get hpa gateway-hpa
kubectl describe hpa gateway-hpa
```

Expected after approximately one minute: `TARGETS` contains a CPU percentage rather than `<unknown>`.

- [ ] **Step 2: Start a safe local tunnel to the cluster Gateway**

In terminal A, run:

```bash
kubectl port-forward service/gateway 18000:80
```

Leave it running. This avoids depending on the worker public IP during load testing.

- [ ] **Step 3: Watch the HPA and Gateway Pods**

In terminal B, run:

```bash
kubectl get hpa,pods -w
```

- [ ] **Step 4: Generate unique Mock-only requests**

In terminal C, run:

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

Every request is unique and forced to `mock-b`, so Redis does not suppress CPU load and DeepSeek receives no paid traffic.

- [ ] **Step 5: Capture scale-up and scale-down evidence**

Run:

```bash
kubectl get hpa gateway-hpa
kubectl get deployment gateway
kubectl get pods -l app=gateway -o wide
```

Expected during/after sustained load: desired replicas exceed `2`. After the HPA stabilization window and load cessation, replicas return to `2`.

- [ ] **Step 6: Adjust only if evidence shows no scale-up**

If CPU remains below 60%, repeat the load with 40 concurrent requests by changing `{1..20}` to `{1..40}`. Do not lower `averageUtilization` until the higher load has been tried and `kubectl top pods` confirms CPU remains too low.

- [ ] **Step 7: Git checkpoint decision**

If `deploy/hpa.yaml` is unchanged, do not commit. If the threshold or load-test script changes, run `bash -n scripts/hpa-load-test.sh`, commit the exact tested files, and push only after scale-up and scale-down both succeed.

---

### Task 4: Deploy the Private Kubernetes Monitoring Stack

**Files:**
- Existing: `deploy/monitoring/rbac.yaml`
- Existing: `deploy/monitoring/kube-state-metrics.yaml`
- Existing: `deploy/monitoring/prometheus.yaml`
- Existing: `deploy/monitoring/grafana.yaml`
- Existing: `deploy/monitoring/kustomization.yaml`
- Existing: `monitoring/prometheus/prometheus-kubernetes.yml`
- Existing: `monitoring/grafana/dashboards/polygate-overview.json`
- Existing: `monitoring/grafana/provisioning/datasources/prometheus.yml`
- Existing: `monitoring/grafana/provisioning/dashboards/polygate.yml`

**Interfaces:**
- Consumes: Gateway `/metrics`, kubelet cAdvisor through the Kubernetes API proxy, and HPA/Deployment state from kube-state-metrics.
- Produces: private Prometheus and authenticated Grafana ClusterIP services.

- [ ] **Step 1: Run local preflight before touching EKS**

Run:

```bash
./scripts/kubernetes-monitoring-preflight.sh
```

Expected: all local checks pass. Stop here if any schema, Prometheus configuration, dashboard, or anchor check fails.

- [ ] **Step 2: Create the Grafana admin Secret interactively**

Run:

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

No characters appear while typing the password; press Enter once. Do not paste the resulting Secret or password into chat or Git.

- [ ] **Step 3: Deploy the monitoring stack**

Run:

```bash
./scripts/deploy-kubernetes-monitoring.sh
```

Expected: `kube-state-metrics`, `prometheus`, and `grafana` successfully roll out. Services remain `ClusterIP`.

- [ ] **Step 4: Verify workload, service, and RBAC state**

Run:

```bash
kubectl get deployment gateway prometheus grafana kube-state-metrics
kubectl get service prometheus grafana kube-state-metrics
kubectl get hpa gateway-hpa
kubectl auth can-i get nodes/proxy \
  --as=system:serviceaccount:default:polygate-prometheus
```

Expected: all Deployments are Available, services show `ClusterIP`, the HPA exists, and the RBAC command prints `yes`.

- [ ] **Step 5: Open private admin access**

In terminal A:

```bash
kubectl port-forward service/prometheus 9090:9090
```

In terminal B:

```bash
kubectl port-forward service/grafana 3000:3000
```

Open `http://localhost:9090/targets` and `http://localhost:3000`. Log in to Grafana with user `admin` and the password entered in Step 2.

- [ ] **Step 6: Run the complete Kubernetes monitoring smoke test**

In terminal C:

```bash
read -s GRAFANA_PASSWORD
export GRAFANA_PASSWORD
./scripts/kubernetes-monitoring-smoke-test.sh
unset GRAFANA_PASSWORD
```

Expected: Prometheus, all Gateway targets, kube-state-metrics, all cAdvisor node targets, HPA/Deployment metrics, Gateway CPU/memory metrics, Grafana health, and protected dashboard checks all pass.

- [ ] **Step 7: Capture presentation evidence**

Capture screenshots of:

```text
Prometheus Targets: all Gateway, kube-state-metrics, and cAdvisor targets UP
Grafana dashboard: request rate, errors, P95 latency, cache, cost, CPU, memory, and HPA panels
kubectl get hpa gateway-hpa: desired replicas and current CPU
kubectl get pods: Gateway replica count during scale-up
```

- [ ] **Step 8: Commit and push checkpoint 2 only when source changed**

If deployment succeeds with the already-versioned manifests, no source commit is needed. If real-cluster validation requires a manifest/config/script fix, run preflight and smoke tests again, then commit only those tested source files:

```bash
git add deploy/monitoring monitoring scripts
git commit -m "fix(monitoring): validate EKS monitoring deployment"
git push origin feat/tan
```

Inspect `git diff --cached` before committing so unrelated D/A/B work is not included.

---

### Task 5: Team Handoff and Demo Freeze

**Files:**
- Modify only if the team wants evidence in the repository: `deploy/monitoring/README.md`

**Interfaces:**
- Consumes: successful HPA and monitoring evidence.
- Produces: a clear ownership boundary and demo runbook for A, B, and D.

- [ ] **Step 1: Send the monitoring handoff message**

Send to the team:

```text
C 线 P1 云端监控已部署到 G3EKS：metrics-server、gateway HPA、Prometheus、kube-state-metrics、Grafana 均已完成验证。

访问边界：Prometheus/Grafana 都是 ClusterIP，不对公网开放；管理演示由 C 使用 kubectl port-forward。D 不需要 AWS 凭证或 kubeconfig，本地仍可用 docker compose 验证页面。

请 A 在演示冻结前不要改 /metrics 的指标名和 provider/outcome/result 标签；如必须修改请先通知 C，因为 Grafana 查询依赖这些名称。

请 B 在 HPA/监控验收期间不要执行 mock-b 故障注入；压测只使用唯一请求并强制路由到 mock-b，不调用付费 DeepSeek。

D 可以继续完成用户前端与同源 API 配置。用户单入口 LoadBalancer 会在功能冻结后由 C 部署，监控页面不会挂到用户公网入口。
```

- [ ] **Step 2: Record the final presentation order**

Use this sequence during the demo:

```text
1. Open the PolyGate user page and submit a request.
2. Show the decision card and repeat request cache hit.
3. Open private Grafana via port-forward and show request/cost/latency panels.
4. Start Mock-only load and show HPA replicas increasing.
5. Stop load and show replicas returning to two.
6. Explain that Prometheus/Grafana are private admin-plane services.
```

- [ ] **Step 3: Final Git check**

Run:

```bash
git status --short
git log --oneline -5
```

Expected: no uncommitted source changes. Do not push screenshots containing AWS account details, public IPs, passwords, or tokens unless the course explicitly requires them and they have been redacted.

---

### Deferred Task: Public Single-Entry User URL

Do this only after A/B/D feature freeze and P1 monitoring completion:

```text
Internet -> LoadBalancer -> web/Nginx
                         -> / returns static frontend
                         -> /v1 and /providers proxy to gateway:80
```

D owns the production-safe same-origin frontend behavior and `web/Dockerfile`. C owns the web ECR image, Nginx proxy configuration, Kubernetes Deployment, and LoadBalancer Service. Prometheus, Grafana, `/metrics`, and the Mock admin endpoint remain excluded from the public route.
