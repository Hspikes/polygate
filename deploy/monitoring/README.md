# Kubernetes monitoring deployment

This directory prepares the cloud monitoring path:

```text
Gateway Pods /metrics -----------\
kubelet CPU and memory -----------> Prometheus -> Grafana
Deployment and HPA object state --/
```

Monitoring API deliberately remains a local, optional service. It is not part
of these Kubernetes manifests because Grafana queries Prometheus directly.

## What is deployed

- Prometheus discovers and scrapes every Gateway Pod separately.
- Prometheus reads Gateway container CPU and memory through the authenticated
  Kubernetes API proxy to each node's kubelet.
- A restricted kube-state-metrics instance exposes only Pod, Deployment, and
  HPA state from the `default` namespace.
- Grafana loads the version-controlled Prometheus data source and dashboard.

Prometheus and Grafana are ClusterIP services. They are not exposed to the
public internet.

## Local preflight

Run this before opening an EKS session:

```bash
./scripts/kubernetes-monitoring-preflight.sh
```

It does not contact a cluster. It renders the Kustomize resources, validates
all application and monitoring manifests against Kubernetes schemas, checks
the in-cluster Prometheus configuration with `promtool`, validates generated
ConfigMaps, and checks that the dashboard contains the required Kubernetes
queries.

## One-time secret

After connecting to the cluster, create the Grafana credentials without
committing them:

```bash
kubectl create secret generic polygate-grafana-admin \
  --namespace default \
  --from-literal=admin-user=admin \
  --from-literal=admin-password='<choose-a-strong-password>'
```

The deployment script refuses to continue if this Secret is missing.

## Deploy

For the next application release, build, push, and deploy with one immutable
Git-based image tag. The commands below target the current Learner Lab ECR
account and build `linux/amd64` images for the x86_64 EKS nodes:

```bash
ECR_REGISTRY=356029564744.dkr.ecr.us-east-1.amazonaws.com \
TARGET_PLATFORM=linux/amd64 \
PUSH_IMAGES=1 \
./scripts/build-kubernetes-images.sh

# Reuse the IMAGE_TAG printed above.
IMAGE_TAG=<tag printed above> ./scripts/deploy-kubernetes-application.sh
```

Deploying Prometheus/Grafana alone does not require rebuilding the application.
Then deploy monitoring:

```bash
./scripts/deploy-kubernetes-monitoring.sh
```

The script converts the version-controlled Prometheus and Grafana files into
ConfigMaps, applies the resources in this directory, and waits for all three
Deployments to become ready.

Access the private services from the local machine:

```bash
kubectl port-forward service/prometheus 9090:9090
kubectl port-forward service/grafana 3000:3000
```

Open:

- Prometheus targets: <http://localhost:9090/targets>
- Grafana: <http://localhost:3000>

In another terminal, verify the complete deployed path without printing the
Grafana password:

```bash
read -rsp "Grafana password: " GRAFANA_PASSWORD
export GRAFANA_PASSWORD
./scripts/kubernetes-monitoring-smoke-test.sh
unset GRAFANA_PASSWORD
```

Expected Prometheus targets:

- one `polygate-gateway` target per running Gateway Pod;
- one `kube-state-metrics` target;
- one `kubernetes-cadvisor` target per Kubernetes node.

## Verify after deployment

```bash
kubectl get deployment gateway prometheus grafana kube-state-metrics
kubectl get hpa gateway-hpa
kubectl auth can-i get nodes/proxy \
  --as=system:serviceaccount:default:polygate-prometheus
```

Useful PromQL:

```promql
count(up{job="polygate-gateway"})
sum(up{job="polygate-gateway"})
sum(rate(container_cpu_usage_seconds_total{namespace="default",container="gateway"}[5m]))
sum(container_memory_working_set_bytes{namespace="default",container="gateway"})
kube_deployment_status_replicas_available{namespace="default",deployment="gateway"}
kube_horizontalpodautoscaler_status_desired_replicas{namespace="default",horizontalpodautoscaler="gateway-hpa"}
```

## Scope and limitations

- All application resources currently live in `default`; discovery and
  dashboard queries intentionally use that namespace.
- Prometheus and Grafana use `emptyDir`, matching the Learner Lab storage
  workaround. Their local history is lost if their Pods restart.
- Prometheus retains at most two days of data.
- `metrics-server` is still required by the HPA. It is separate from
  kube-state-metrics and is not installed here.
- Final server-side validation and kubelet access can only be verified after
  connecting to the real cluster.
