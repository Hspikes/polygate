# PolyGate — P0 骨架

面向多模型 AI 应用的云原生智能网关。这是 **P0（第 1-4 天）** 的可运行骨架：
一个统一 OpenAI 兼容网关，在一个真实 Provider + 两个 Mock 之间按价格/健康/预算/隐私规则路由，
带精确缓存和「答案 + 决策卡片」界面。

## 30 秒跑起来（本地全套）
```bash
cp .env.example .env          # 可留空；填了 REAL_A_API_KEY 才启用真实 Provider
docker compose up --build     # 起 redis + mock-a + mock-b + gateway
cd web && python -m http.server 8080   # 另开终端，打开 http://localhost:8080
```
在页面里提交问题 → 看答案 + 决策卡片；再提交一次相同问题 → 看到缓存命中、成本 $0。

本地 Compose 还会启动完整的监控链路：

- 指标查询：<http://localhost:9090>
- 采集目标：<http://localhost:9090/targets>
- 自动检查：`./scripts/prometheus-smoke-test.sh`
- 监控后端：<http://localhost:8010/api/monitoring/overview>
- 后端检查：`./scripts/monitoring-api-smoke-test.sh`
- Grafana 仪表盘：<http://localhost:3000/d/polygate-overview/polygate-overview>
- 前后端联通检查：`./scripts/grafana-smoke-test.sh`
- 云端监控部署前检查：`./scripts/kubernetes-monitoring-preflight.sh`

详细说明见 [`monitoring/README.md`](./monitoring/README.md)。

## 目录 = 分工（各写各的，互不阻塞）
| 目录 | 主责 | 一句话 | 独立开发方式 |
|---|---|---|---|
| `gateway/`   | A | 统一网关、路由、成本、决策卡片 | `FAKE_ADAPTER=1` 用假数据先跑 |
| `providers/` | B | 两个 Mock + 真实 Adapter + 缓存 | Mock 是纯服务，独立起 |
| `web/`       | D | 极简 UI、演示控制台、负载器 | 「Load fixture」零后端起步 |
| `deploy/`    | C | K8s 清单、探针、HPA、可观测性 | 只有 C 碰 EKS，其余人用 compose |
| `contracts/` | 全体 | ⭐ 5 份冻结契约，唯一真相来源 | 改动需全员同意 |

## 开工顺序（重要）
1. **头 2 小时四人一起**：过一遍 `contracts/`，把 5 份契约和技术栈钉死。
2. 之后各自进自己的目录，对着契约/桩独立开发（见各目录 README 的验收判据）。
3. 第 3 天 A↔B 在本地 compose 集成；第 4 天 C 部署到 EKS 并冻结 P0 范围。

## 一条铁律
**P0 期间 A/B/D 只用本地 docker-compose 开发，只有 C 碰 EKS。**
这样 Learner Lab 的 4 小时 session 过期、凭证重配都不会拖垮全队。

## 范围边界
P0 只做规则路由 + 精确/规范化缓存。语义缓存、KEDA、Provider CRD、租户配额都是 P2/P3，
骨架里已用 TODO 标出接入点，**别在 P0 提前做**。
