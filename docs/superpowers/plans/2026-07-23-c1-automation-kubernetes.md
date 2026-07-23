# PolyGate C1 Automation Kubernetes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, locally validated Kubernetes deployment path for the standalone Automation API without changing the currently deployed EKS application.

**Architecture:** Add one `automation` Deployment and ClusterIP Service. Extend the existing image and application deployment scripts behind `INCLUDE_AUTOMATION=0`, so the stable Gateway/Web deployment remains unchanged unless C explicitly enables Automation after A/B integration.

**Tech Stack:** Bash, Docker BuildKit, Kubernetes 1.34 manifests, kubeconform, FastAPI health probes, AWS ECR image naming.

## Global Constraints

- Do not execute AWS writes or `kubectl apply` against EKS during C1.
- Keep Automation private as a ClusterIP service on port `8020`.
- Keep Automation at one replica while Preview and Job state are process-local.
- Default `INCLUDE_AUTOMATION` to `0`; accept only `0` or `1`.
- Use `GATEWAY_URL=http://gateway:8000` and `AUTOMATION_REDIS_URL=redis://redis:6379/1`.
- Build cloud images for `linux/amd64`.
- Do not add Agent, Worker, HPA, Prometheus, Grafana, NodePort, LoadBalancer, or Secrets in C1.
- Do not stage the unrelated untracked 2026-07-20 plan files.

---

### Task 1: Freeze and Implement the Automation Manifest Contract

**Files:**
- Create: `deploy/automation.yaml`
- Modify: `scripts/tests/test-deployment-automation.sh`
- Modify: `scripts/kubernetes-monitoring-preflight.sh`

**Interfaces:**
- Consumes: Automation `GET /health`, port `8020`, `GATEWAY_URL`, and `AUTOMATION_REDIS_URL`.
- Produces: Kubernetes DNS endpoint `http://automation:8020` and image anchor `356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-automation:v1`.

- [x] **Step 1: Add failing manifest assertions**

Append before the test script's final success message:

```bash
require_text "$ROOT_DIR/deploy/automation.yaml" \
  "$REGISTRY/polygate-automation:v1"
require_text "$ROOT_DIR/deploy/automation.yaml" "replicas: 1"
require_text "$ROOT_DIR/deploy/automation.yaml" "type: ClusterIP"
require_text "$ROOT_DIR/deploy/automation.yaml" "automountServiceAccountToken: false"
require_text "$ROOT_DIR/deploy/automation.yaml" "runAsUser: 10001"
require_text "$ROOT_DIR/deploy/automation.yaml" 'value: "http://gateway:8000"'
require_text "$ROOT_DIR/deploy/automation.yaml" 'value: "redis://redis:6379/1"'
require_text "$ROOT_DIR/scripts/kubernetes-monitoring-preflight.sh" \
  '"$ROOT_DIR/deploy/automation.yaml"'
```

- [x] **Step 2: Verify RED**

Run `bash scripts/tests/test-deployment-automation.sh`.

Expected: non-zero exit because `deploy/automation.yaml` does not exist.

- [x] **Step 3: Add the Deployment and Service**

Create `deploy/automation.yaml`:

```yaml
# Private Automation control plane. Opt-in during C1; no public Service.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: automation
  labels: { app: automation }
spec:
  replicas: 1
  selector: { matchLabels: { app: automation } }
  template:
    metadata:
      labels: { app: automation }
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: automation
          image: 356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-automation:v1
          ports: [{ name: http, containerPort: 8020 }]
          env:
            - { name: GATEWAY_URL, value: "http://gateway:8000" }
            - { name: AUTOMATION_REDIS_URL, value: "redis://redis:6379/1" }
          resources:
            requests: { cpu: "50m", memory: "64Mi" }
            limits: { cpu: "300m", memory: "256Mi" }
          startupProbe:
            httpGet: { path: /health, port: http }
            periodSeconds: 5
            failureThreshold: 12
          readinessProbe:
            httpGet: { path: /health, port: http }
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /health, port: http }
            periodSeconds: 10
            failureThreshold: 3
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: { drop: ["ALL"] }
            readOnlyRootFilesystem: true
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
      volumes:
        - name: tmp
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: automation
spec:
  selector: { app: automation }
  ports: [{ name: http, port: 8020, targetPort: http }]
  type: ClusterIP
```

- [x] **Step 4: Add the manifest to offline validation**

Insert into the application manifest loop in `scripts/kubernetes-monitoring-preflight.sh`:

```bash
  "$ROOT_DIR/deploy/automation.yaml" \
```

Do not add it to Prometheus or the monitoring Kustomize resources.

Also extend the final image-anchor condition in the preflight script with:

```bash
  && grep -Fq \
    "356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-automation:v1" \
    "$ROOT_DIR/deploy/automation.yaml"
```

- [x] **Step 5: Verify GREEN and the schema**

```bash
bash scripts/tests/test-deployment-automation.sh
docker run --rm --interactive ghcr.io/yannh/kubeconform:v0.7.0 \
  -strict -summary -kubernetes-version 1.34.0 \
  < deploy/automation.yaml
```

Expected: deployment test passes and kubeconform reports two valid resources.

---

### Task 2: Add an Opt-In Automation Image Build

**Files:**
- Modify: `scripts/tests/test-deployment-automation.sh`
- Modify: `scripts/build-kubernetes-images.sh`

**Interfaces:**
- Consumes: root-context `automation/Dockerfile`, `IMAGE_TAG`, `ECR_REGISTRY`, `TARGET_PLATFORM`, and `PUSH_IMAGES`.
- Produces: `$ECR_REGISTRY/polygate-automation:$IMAGE_TAG` only when `INCLUDE_AUTOMATION=1`.

- [x] **Step 1: Add failing build-script assertions**

```bash
require_text "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  'INCLUDE_AUTOMATION="${INCLUDE_AUTOMATION:-0}"'
require_text "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  'AUTOMATION_IMAGE="$ECR_REGISTRY/polygate-automation:$IMAGE_TAG"'
require_text "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  '--file "$ROOT_DIR/automation/Dockerfile"'
require_text "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  'if [ "$INCLUDE_AUTOMATION" = "1" ]; then'
```

- [x] **Step 2: Verify RED**

Run `bash scripts/tests/test-deployment-automation.sh`.

Expected: missing `INCLUDE_AUTOMATION` setting.

- [x] **Step 3: Implement strict opt-in validation**

Add near the existing settings:

```bash
INCLUDE_AUTOMATION="${INCLUDE_AUTOMATION:-0}"

if [ "$INCLUDE_AUTOMATION" != "0" ] && [ "$INCLUDE_AUTOMATION" != "1" ]; then
  echo "INCLUDE_AUTOMATION must be 0 or 1." >&2
  exit 1
fi
```

After resolving `IMAGE_TAG`, define:

```bash
AUTOMATION_IMAGE="$ECR_REGISTRY/polygate-automation:$IMAGE_TAG"
```

After the Web build, add:

```bash
if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  docker build \
    --platform "$TARGET_PLATFORM" \
    --pull \
    --file "$ROOT_DIR/automation/Dockerfile" \
    --tag "$AUTOMATION_IMAGE" \
    "$ROOT_DIR"
fi
```

Inside the push block, conditionally run `docker push "$AUTOMATION_IMAGE"`. Print the
Automation image only when enabled; keep the existing three image lines unchanged.

- [x] **Step 4: Verify GREEN and rejection of bad input**

```bash
bash -n scripts/build-kubernetes-images.sh
bash scripts/tests/test-deployment-automation.sh
IMAGE_TAG=c1-test INCLUDE_AUTOMATION=invalid \
  ./scripts/build-kubernetes-images.sh
```

Expected: first two commands pass; the last exits before Docker with
`INCLUDE_AUTOMATION must be 0 or 1.`

- [x] **Step 5: Build the target-architecture Automation image without pushing**

```bash
docker build --platform linux/amd64 \
  --file automation/Dockerfile \
  --tag polygate-automation:c1-local .
```

Expected: exit `0`.

---

### Task 3: Add an Opt-In Automation Deployment Path

**Files:**
- Modify: `scripts/tests/test-deployment-automation.sh`
- Modify: `scripts/deploy-kubernetes-application.sh`

**Interfaces:**
- Consumes: the Task 1 image anchor and Task 2 image name.
- Produces: optional manifest rendering, apply, and rollout wait when `INCLUDE_AUTOMATION=1`.

- [x] **Step 1: Add failing deployment-script assertions**

```bash
require_text "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  'INCLUDE_AUTOMATION="${INCLUDE_AUTOMATION:-0}"'
require_text "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  "PINNED_AUTOMATION_IMAGE=\"$REGISTRY/polygate-automation:v1\""
require_text "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  'AUTOMATION_IMAGE="$ECR_REGISTRY/polygate-automation:$IMAGE_TAG"'
require_text "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  '"$ROOT_DIR/deploy/automation.yaml"'
require_text "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  'deployments+=(automation)'
```

- [x] **Step 2: Verify RED**

Run `bash scripts/tests/test-deployment-automation.sh`.

Expected: missing Automation deployment wiring.

- [x] **Step 3: Implement optional render/apply/rollout**

Add the same `INCLUDE_AUTOMATION` validation as Task 2, then define:

```bash
AUTOMATION_IMAGE="$ECR_REGISTRY/polygate-automation:$IMAGE_TAG"
PINNED_AUTOMATION_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-automation:v1"
```

Check the Automation anchor only when enabled. After Web apply, add:

```bash
if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  echo "Deploying Automation image: $AUTOMATION_IMAGE"
  sed "s#$PINNED_AUTOMATION_IMAGE#$AUTOMATION_IMAGE#g" \
    "$ROOT_DIR/deploy/automation.yaml" \
    | kubectl apply --namespace "$NAMESPACE" --filename=-
fi
```

Replace the fixed rollout loop with:

```bash
deployments=(redis mock-a mock-b gateway web)
if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  deployments+=(automation)
fi

for deployment in "${deployments[@]}"; do
  kubectl rollout status \
    "deployment/$deployment" \
    --namespace "$NAMESPACE" \
    --timeout=180s
done
```

- [x] **Step 4: Verify GREEN without contacting EKS**

```bash
bash -n scripts/deploy-kubernetes-application.sh
bash scripts/tests/test-deployment-automation.sh
IMAGE_TAG=c1-test INCLUDE_AUTOMATION=invalid \
  ./scripts/deploy-kubernetes-application.sh
```

Expected: syntax and regression tests pass; invalid input exits before `kubectl`.

---

### Task 4: Correct the Runbook and Document the C1 Boundary

**Files:**
- Modify: `scripts/tests/test-deployment-automation.sh`
- Modify: `deploy/RUNBOOK.md`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: active cluster name `G3EKS` and the Tasks 2–3 opt-in switch.
- Produces: exact future commands that cannot silently enable Automation.

- [x] **Step 1: Add failing documentation assertions**

```bash
require_text "$ROOT_DIR/deploy/RUNBOOK.md" \
  'update-kubeconfig --name G3EKS'
require_text "$ROOT_DIR/deploy/README.md" 'INCLUDE_AUTOMATION=1'
require_text "$ROOT_DIR/deploy/README.md" 'C1 不立即部署到 EKS'
```

- [x] **Step 2: Verify RED**

Run `bash scripts/tests/test-deployment-automation.sh`.

Expected: the old Runbook cluster command fails the assertion.

- [x] **Step 3: Update documentation**

Set the Runbook command to:

```bash
aws eks --region us-east-1 update-kubeconfig --name G3EKS
```

Document these future commands without running them:

```bash
C1_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"

IMAGE_TAG="$C1_IMAGE_TAG" INCLUDE_AUTOMATION=1 PUSH_IMAGES=1 \
  ./scripts/build-kubernetes-images.sh

IMAGE_TAG="$C1_IMAGE_TAG" INCLUDE_AUTOMATION=1 \
  ./scripts/deploy-kubernetes-application.sh
```

State exactly `C1 不立即部署到 EKS` and explain that Redis Worker integration must pass first.

- [x] **Step 4: Verify GREEN**

```bash
bash scripts/tests/test-deployment-automation.sh
git diff --check
```

Expected: both exit `0`.

---

### Task 5: Full Local Verification and C1 Handoff

**Files:**
- Verify all Task 1–4 files.
- Update this plan's checkboxes only after evidence exists.

**Interfaces:**
- Consumes: the complete C1 working tree.
- Produces: one reviewable implementation commit and an A/B/D handoff.

- [x] **Step 1: Run offline deployment gates**

```bash
bash scripts/tests/test-deployment-automation.sh
./scripts/kubernetes-monitoring-preflight.sh
docker compose config --quiet
bash -n scripts/build-kubernetes-images.sh
bash -n scripts/deploy-kubernetes-application.sh
git diff --check
```

Expected: all exit `0`; no AWS or EKS write occurs.

- [x] **Step 2: Verify the container security assumptions**

```bash
docker run --rm -d \
  --name polygate-automation-c1-test \
  --user 10001:10001 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -p 18020:8020 \
  polygate-automation:c1-local

AUTOMATION_URL=http://localhost:18020 \
  ./scripts/automation-skeleton-smoke-test.sh

docker stop polygate-automation-c1-test
```

Expected: smoke test `5 passed, 0 failed`; the temporary container is removed.

- [x] **Step 3: Run application regression tests**

```bash
python3 scripts/tests/test-automation-contracts.py
docker run --rm \
  -v "$PWD:/workspace" -w /workspace \
  python:3.12-slim \
  sh -c 'pip install --quiet -r automation/requirements.txt && python -m unittest automation.tests.test_api -v'
```

Expected: contract tests `4/4`, Automation API tests `7/7`.

- [x] **Step 4: Review scope before staging**

```bash
git status --short
git diff -- \
  deploy/automation.yaml deploy/README.md deploy/RUNBOOK.md \
  scripts/build-kubernetes-images.sh \
  scripts/deploy-kubernetes-application.sh \
  scripts/kubernetes-monitoring-preflight.sh \
  scripts/tests/test-deployment-automation.sh
```

Expected: no Gateway, Provider, Web, Agent, Automation application, Prometheus, or Grafana source changes.

- [x] **Step 5: Commit and push the implementation**

```bash
git add \
  deploy/automation.yaml deploy/README.md deploy/RUNBOOK.md \
  scripts/build-kubernetes-images.sh \
  scripts/deploy-kubernetes-application.sh \
  scripts/kubernetes-monitoring-preflight.sh \
  scripts/tests/test-deployment-automation.sh \
  docs/superpowers/plans/2026-07-23-c1-automation-kubernetes.md

git commit -m "feat(deploy): add opt-in Automation Kubernetes skeleton"
git push origin feat/tan
```

- [x] **Step 6: Send the handoff**

```text
C1 的 Automation Kubernetes 骨架已完成：集群内地址固定为
http://automation:8020，环境变量为 GATEWAY_URL=http://gateway:8000 和
AUTOMATION_REDIS_URL=redis://redis:6379/1。当前仍为单副本，默认
INCLUDE_AUTOMATION=0，不会部署到 EKS。请 A/B 确认端口、/health、Redis
状态迁移方案；Worker、Agent 和 /metrics 完成后按交接模板 @C，我再做 C2/C3 接线。
```
