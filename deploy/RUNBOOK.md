# deploy/RUNBOOK.md — 每次 Start Lab 之后照做一遍

> 谁用：成员 C。什么时候用：session 过期(4 小时到期 / 合盖太久 / 页面卡住重刷)之后，
> 或者任何一次重新打开电脑准备干活之前。
> 目标：5 分钟内确认环境状态、刷新凭证，回到能继续干活的状态。

## 0. 背景（为什么每次都要做这些）

- Learner Lab 的临时凭证（access key / secret / session token）**每 4 小时强制过期**，
  跟你电脑开不开、合不合盖无关。过期后任何 `aws` / `kubectl` 命令都会报权限错误。
- EKS 集群本身、Redis、network 配置等 K8s 层面的东西**不会因为 session 过期而消失**——
  它们是持续运行的云资源，跟你本地终端的登录状态是两回事。
- 已知例外：EC2 节点会随 session 短暂重启一次，Pod 可能有 1-2 分钟的抖动，一般自愈。

## 1. 刷新凭证（必做，每次都要）

1. 打开 Learner Lab 页面，如果显示过期/卡住，刷新页面，重新点 **Start Lab**，
   等右上角圆点变绿。
2. 点 **AWS Details**，复制新的三件套。
3. 本地跑：
   ```bash
   aws configure
   # 依次粘贴：Access Key ID / Secret Access Key / region 填 us-east-1 / output 留空回车
   ```
4. 有些 CLI 版本 `aws configure` 不会主动问 session token，手动补一句：
   ```bash
   aws configure set aws_session_token <粘贴新的 session token>
   ```

## 2. 确认能连上集群

```bash
aws eks --region us-east-1 update-kubeconfig --name G3EKS
kubectl get nodes
```
- 预期：2 个节点，STATUS = `Ready`。
- 如果是 `NotReady`：等 1-2 分钟（节点刚重启，kubelet 需要时间上报），再跑一次。
  超过 5 分钟还没好，把 `kubectl describe node <name>` 贴出来排查。

## 3. 确认之前部署的东西还活着

```bash
kubectl get pods -A | grep -v Running
```
理论上这条命令**应该没有任何输出**（所有 Pod 都 Running）。如果看到：
- `redis-xxx` 不是 Running → `kubectl get pods -l app=redis -w` 盯着看，一般会自愈。
- 有 Pod 一直 `Pending`/`CrashLoopBackOff` → 贴 `kubectl describe pod <name>` 出来看。

## 4. 已知状态存档（别重新踩一遍这些坑）

- **存储用的是 `deploy/redis.yaml`（emptyDir）。**
  原因：这个账号的 EBS CSI Driver 插件因 IAM 权限（Pod Identity 角色关联失败）
  跑不起来，`CrashLoopBackOff` 排查后判定修复成本过高，已改用 emptyDir。
  PVC 参考版本在 `deploy/reference/redis-pvc.yaml`，当前不要应用它。
- **Node group 扩缩容范围已经改过**：min=2 / desired=2 / max=4
  （控制台默认建出来的不一定带这个范围，已经手动 `update-nodegroup-config` 改过，
  不用重复操作，除非发现被重置回默认值）。
- **EBS CSI Driver 插件建议已移除**（控制台 Add-ons 页点过 Remove）。
  如果发现它又出现在 Add-ons 列表里且在 `CrashLoopBackOff`，直接再移除一次，
  不用花时间修，跟当前架构（emptyDir）无关。

## 5. Web NodePort 对外入口

新 session 里 EC2 网络接口(ENI)的关联信息可能会变，之前手动挂的放行安全组
要重新确认安全组规则还在。只放行 Web 的 TCP `30080`，来源尽量限定为演示者 IP；
不要再公开 Gateway 或 Mock Provider，也不要长期使用 `All TCP / 0.0.0.0/0`。

不确定还通不通，直接测：
```bash
curl -s http://<节点公网IP>:30080/healthz
```

## 6. 一键自检（收尾）

```bash
kubectl port-forward service/mock-b 18082:8080
```

保持上面的端口转发运行，在另一个终端执行：

```bash
WEB_BASE=http://<节点公网IP>:30080 ./scripts/web-smoke-test.sh
```
全绿就说明页面、Nginx `/api` 代理、Gateway 和 Provider 的多轮链路正常。
如需独立验证故障注入，可另行 port-forward Gateway 后运行原有 `scripts/smoke-test.sh`。

监控部署后再确认：

```bash
kubectl get deployment prometheus grafana kube-state-metrics
kubectl port-forward service/grafana 3000:3000
```

打开 <http://localhost:3000>，确认 Gateway target、CPU、内存和 HPA 面板有数据。

## 7. 顺手看一眼预算

Learner Lab 页面上 "Used $X of $100"，每天开工扫一眼记下来。
EKS 控制面即使 `End Lab` 也持续计费（约 $0.10/小时），这是固定支出，
如果哪天涨幅明显超出预期，检查是不是有资源没释放（比如忘记删的测试 Pod/PVC/LoadBalancer）。

## 8. Policy Control Plane 检查

Policy Store 是 ConfigMap 中的持久配置，不依赖 Redis。重新部署或重启 Automation
前先确认资源存在，且不要手工用默认文件覆盖它：

```bash
kubectl get configmap polygate-routing-policy
kubectl get serviceaccount,role,rolebinding polygate-policy-controller
kubectl get secret polygate-policy-admin \
  -o go-template='{{range $k,$v := .data}}{{printf "%s\n" $k}}{{end}}'
```

最后一条预期只显示 key 名 `admin-key`，不要打印 Secret 值。验证最小权限：

```bash
kubectl auth can-i get configmap/polygate-routing-policy \
  --as=system:serviceaccount:default:polygate-policy-controller
kubectl auth can-i update configmap/polygate-routing-policy \
  --as=system:serviceaccount:default:polygate-policy-controller
kubectl auth can-i list configmaps \
  --as=system:serviceaccount:default:polygate-policy-controller
kubectl auth can-i get secrets \
  --as=system:serviceaccount:default:polygate-policy-controller
```

预期依次为 `yes / yes / no / no`。如果 Automation 或 Redis 重启，Policy active
version 与历史应保持不变；若 ConfigMap 意外缺失，应先确认没有可恢复的版本历史，
再由部署脚本重新创建初始 store。
