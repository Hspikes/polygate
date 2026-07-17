# PolyGate P0 启动清单（v2：轻量化版）

> 原则：能不开会决定的，就不开会——这份清单里能定的都直接定了，只留下真正需要团队本人决定的几件事。安全相关的部分不因为"轻量化"而放松。

---

## 一、代码仓库

**直接这么做，不用讨论：**

- 用 GitHub，建一个仓库，别拆成四个
- 默认分支 `main`，每人在自己的分支上开发（`feat/gateway-routing` 这种命名），改完直接合并到 `main`，**不设强制 PR review**——十天工期，互相 review 的成本比价值高
- 仓库结构：

  ```
  polygate/
  ├── gateway/
  ├── providers/
  ├── k8s/
  ├── frontend/
  ├── docs/
  └── README.md
  ```

- `.gitignore` 里第一行就加 `.env`（下面会讲为什么）

**只需要团队确认一件事：**
- [ ] 谁负责建仓库、拉另外三人进来（建议：这次汇报的主讲人顺手建了就行）

---

## 二、接口约定

**决策卡片 JSON 格式，直接用这个，不用再开会定：**

```json
{
  "answer": "string",
  "provider": "string",
  "reason": "string",
  "cache_hit": false,
  "latency_ms": 0,
  "estimated_cost": 0.0,
  "failover_occurred": false
}
```

**内部接口约定：**
- B 给 A 的调用统一走 HTTP（不用直接函数调用），路径统一用 `/v1/xxx` 前缀
- 所有内部接口出错时统一返回 `{"error": "错误描述"}`，配 4xx/5xx 状态码
- Provider 健康检查统一暴露成 `GET /health`，返回 `{"healthy": true/false}`

**只需要团队确认一件事：**
- [ ] 这份接口约定写进 `docs/interfaces.md`，四个人在第 1 天各自看一眼、有异议当场提，没有异议就按这个定稿——**不需要专门开会，群里发一下确认就行**

---

## 三、环境与工具（选择题已经帮你们选好了）

### 本地开发环境
- 用 `docker-compose.yml` 在本地跑一套最小链路（网关 + 一个 mock provider + Redis），C 负责写这份文件，第 1 天交付
- 语言/框架版本：以 A（网关负责人）现在最熟悉的技术栈为准，其他人跟着对齐——不需要为了"统一"专门挑一个新技术栈来学

### K8s 集群
- 用哪个集群（Learner Lab / Minikube / 自建）——这个必须团队自己定，取决于你们实际拿到的资源
- 每人先在自己的 namespace 里独立开发测试（`polygate-dev-a`、`polygate-dev-b`……），第 4 天 P0 冻结时统一合并部署到一个 `polygate` namespace 里

**只需要团队确认一件事：**
- [ ] 集群资源从哪来（Learner Lab / Minikube / 云账号），这个决定其他都不用讨论了

### 监控
- **直接用 Helm 装 Prometheus + Grafana**（`kube-prometheus-stack` 这个 Helm chart），别手写 YAML——Helm 装好之后改几个 values 配置就行，比自己拼装 20 个 YAML 文件省一整天时间，C 负责，其他人不需要关心细节，只需要知道自己的服务要在哪个端口暴露 `/metrics`

---

## 四、密钥与预算管理（这部分不因为"轻量化"而简化，必须做对）

**直接照做，不用讨论：**

1. 真实 API Key **绝不写进代码或提交到 Git**：
   - 本地开发：用 `.env` 文件读取，`.env` 已经加进 `.gitignore`
   - K8s 部署：用 `kubectl create secret` 存成 Secret，Pod 通过环境变量注入
2. 只有一台机器需要配真实 Key（负责 Provider 适配和联调验证的那台），其他人开发阶段全部对着 Mock Provider 跑，不需要拿到真实 Key
3. 定一个"每日虚拟费用上限"（比如 1 美元），写死在配置里，超过就自动降级到 Mock Provider，防止联调时手滑刷爆额度
4. 每次 `git push` 前养成习惯扫一眼 diff，确认没有把 `.env` 或密钥字符串带进去（十天项目没必要上专门的密钥扫描工具，人肉检查够用）

**只需要团队确认两件事：**
- [ ] 谁去注册真实 Provider（比如 DeepSeek）账号、用谁的名义充值
- [ ] 那台"持有真实 Key"的机器是谁的

---

## 五、第 1 天结束前，实际要做完的事（不是要讨论的事，是要做完的事）

- [ ] 仓库建好，四人能 push
- [ ] `docs/interfaces.md` 按上面给的格式写完，群里发一下，没人反对就定稿
- [ ] `docker-compose.yml` 能跑通最基础的一条链路
- [ ] 集群连通性确认（`kubectl get pods` 四人都试一遍）
- [ ] 真实 API Key 已经用 Secret/`.env` 方式管理好，**没有出现在任何一次 Git 提交里**
- [ ] Helm 装好 Prometheus + Grafana（哪怕暂时还没有数据可看，装好就算完成）

---

真正需要团队坐下来讨论的，其实只剩三件事：**集群资源从哪来、谁建仓库、谁去申请真实 API Key**。其余的都已经替你们定好了，直接照着做就行。
