# PolyGate

PolyGate 是面向团队和多模型 AI 应用的云原生 **AI API 网关与策略控制系统**。
客户端通过统一的 OpenAI 兼容接口或 Web 界面提交请求，不需要在业务代码里硬编码
具体 Provider。系统根据质量、隐私、预算、延迟、能力和健康状态选择 Provider，
并返回可解释的决策卡片：选了谁、为什么、是否命中缓存、花费多少、耗时多久；
同时通过缓存、重试、熔断、故障切换和 Kubernetes 弹性扩缩容改善成本、可靠性
和容量。

项目使用 Python、FastAPI、Redis、React、Prometheus、Grafana 和 Kubernetes，
云端部署目标为 AWS EKS（`us-east-1`）。

**本 README 的事实口径**：以下内容按 2026-07-26 完成的
[《PolyGate 项目全貌与 30 分钟汇报方向总纲》](./document/PolyGate_项目全貌与30分钟汇报方向总纲_2026-07-26.md)
校准过一次——该文档发现本文件早期版本仍把 Redis 优先队列、Worker 和 Policy
控制面写成"待完成"，但这些能力在代码里其实早已实现。为避免同样的问题，本次
更新沿用该文档的四级事实标注：

- **代码已实现**：仓库中存在对应实现和测试；
- **已有部署清单/脚本**：具备本地或 EKS 部署与验证路径，**但不等于你阅读本文
  时云集群一定在线**——是否仍在运行取决于是否已手动销毁，请勿假设 EKS 端点
  可访问；
- **部分接通**：各组件分别存在，但端到端的用户链路尚未完全打通；
- **规划或后续方向**：明确不是已完成能力，不应被当作交付物。

## 当前状态

| 能力 | 状态 | 说明 |
|---|---|---|
| Gateway 核心路由 | 代码已实现 | OpenAI 兼容接口、真实/Mock Provider、质量·隐私·预算·延迟路由、精确缓存、Decision Card、Decision Record |
| 可靠性 | 代码已实现 | 重试、熔断、故障切换、请求级时间预算 |
| Automation 任务调度 | 代码已实现 | Intent/Preview/Job API、Redis 优先队列、Worker（租约、重试、公平性防饥饿） |
| Policy Control Plane | 代码已实现 | 版本化策略、发布前 validate/simulate、热加载、Last Known Good、回滚、私有 Policy Editor |
| 可观测性 | 代码已实现 | Prometheus 指标、Grafana 大屏（含 Policy 版本漂移面板）、Monitoring API |
| Kubernetes / EKS 部署 | 已有部署清单/脚本 | 完整 manifest、RBAC、Secret、ConfigMap 持久化、HPA、preflight 与 smoke 脚本均已验证通过，但当前是否仍在集群中运行未知 |
| Web ↔ Automation/Pi 端到端链路 | 部分接通 | Web Chat 直连 Gateway 已打通；Web 提交企业任务卡片、Pi Agent 调用 Automation 的完整用户链路尚未完全接通，详见总纲第 9.3 节 |
| 语义缓存 / KEDA / 多租户计费 | 规划方向 | 明确不在本轮范围内 |

当前稳定链路：

```text
Browser / OpenAI Client -> Web or Gateway -> Router -> Redis cache -> Provider
                                      \-> Decision Card + Decision Record + Prometheus metrics
```

企业任务调度链路：

```text
Requirement Card -> Automation API -> Priority Queue + Worker -> Gateway -> Provider
                         \-> Preview（可注入 Policy 草稿模拟路由影响）
```

策略变更链路：

```text
Policy Editor -> Automation Policy API（validate/simulate/publish）-> ConfigMap
                         \-> Gateway / Worker 每 5 秒轮询热加载 -> Last Known Good 兜底
```

Automation 与 Policy Control Plane 是独立于 Gateway 的控制面，不侵入
`/v1/chat/completions` 既有契约。

## 本地启动

```bash
cp .env.example .env          # 可留空；填写 REAL_A_API_KEY 后启用真实 DeepSeek Provider
docker compose up --build -d
docker compose ps
```

预期 `redis`、`mock-a`、`mock-b`、`gateway`、`automation`、`automation-worker`、
`web`、`prometheus`、`grafana` 全部健康。

常用入口：

| 服务 | 地址 | 用途 |
|---|---|---|
| Web | <http://localhost:8080> | 多轮 Chat、决策卡片、企业需求卡片 |
| Gateway | <http://localhost:8000> | OpenAI 兼容 API |
| Automation | <http://localhost:8020/docs> | 需求模板、Preview、Job API |
| Policy Editor | <http://localhost:8020/admin/policies> | 策略编辑、校验、模拟、发布、回滚（私有，不经 Web Nginx 暴露） |
| Monitoring API | <http://localhost:8010/api/monitoring/overview> | 固定 Prometheus 查询的 JSON 总览 |
| Prometheus | <http://localhost:9090/targets> | 指标采集状态与查询 |
| Grafana | <http://localhost:3000/d/polygate-overview/polygate-overview> | 业务、Kubernetes 资源与 Policy 版本大屏 |

Pi Agent 可把 PolyGate 注册为 `polygate/auto` 模型 Provider，通过 SSE 完成
本地工具循环。`/login polygate` 的网关验证受五秒预算约束；工具循环结束后可用
`/route-last` 查看最终 Provider、Gateway 实际成本估算、tokens、重试、failover 和
request ID，且这些决策数据不会进入模型 Prompt。无真实模型、无费用的 Mock 验证
方式见 [`gateway/README.md`](./gateway/README.md#pi-agent-可运行闭环)，扩展安装与使用
说明见 [`.pi/extensions/polygate-routing/README.md`](./.pi/extensions/polygate-routing/README.md)。

## EKS 部署

部署清单和自动化脚本位于 `deploy/`，已通过完整 preflight 与 smoke test 验证
（见下方"本地集成闸门"第 3、4 步）。云端公开入口是 Web 的 Kubernetes
NodePort（`http://<节点公网IP>:30080`），而不是固定域名的 LoadBalancer；
Gateway、Automation、Worker、Redis、Prometheus、Grafana 和 Policy Editor 均
保持集群内部访问，仅管理员通过 `kubectl port-forward` 触达。

**是否有正在运行的集群、具体访问地址是什么，请向组内确认**——本文档不假设
读者阅读时集群仍然在线，详见 `deploy/README.md` 与 `deploy/RUNBOOK.md`。

## 本地集成闸门（部署前必须全绿）

**0. 起完整栈**

```bash
docker compose up --build -d && docker compose ps
```

**1. 后端测试**（需在 Python 3.12 容器/环境下跑）

Worker 测试需要真实 Redis（`AUTOMATION_TEST_REDIS_URL`，用 db 15）；Gateway 套件
也需要 Redis，否则若干缓存用例会**静默跳过**而不是失败——只看 `0 failed` 会
误判，要同时确认 skip 数为 0。

```bash
python -m pytest automation/tests -q     # 152 passed
python -m pytest tests -q                # 在 gateway/ 下，86 passed / 0 skipped
```

**2. Web 测试**

```bash
cd web && npm test && npm run lint && npm run build && cd ..
```

⚠️ **需要 Node 22。** 在 Node 25 上 57 个测试会全部失败，报
`TypeError: localStorage.clear is not a function`——Node 内置的实验性
`localStorage` 会顶掉 jsdom 的那个。这不是代码回归：同一份代码在 Node 22 下
57 passed / lint 0 / build 0。仓库目前没有 `engines` 或 `.nvmrc` 来固定版本。

**3. 契约与部署回归**

```bash
python3 scripts/tests/test-automation-contracts.py
python3 scripts/tests/test-policy-contracts.py      # 需要 pip install jsonschema
bash scripts/tests/test-deployment-automation.sh
bash scripts/tests/test-deployment-policy.sh
./scripts/kubernetes-monitoring-preflight.sh        # 12 项
```

**4. 行为冒烟**

```bash
./scripts/web-smoke-test.sh
./scripts/kubernetes-automation-smoke-test.sh
POLICY_ADMIN_KEY=local-policy-admin-development \
  PROMETHEUS_URL=http://localhost:9090 \
  ./scripts/kubernetes-policy-smoke-test.sh
./scripts/automation-peak-test.sh
```

`automation-peak-test.sh` 提交顺序刻意与优先级相反（low 先提交），所以看到
critical 最先被执行才说明调度按 `effective_priority` 生效，而不是碰巧。

配置了真实 DeepSeek Key 时，再跑 Flash/Pro 质量路由闸门。它会产生少量真实
API 费用，并验证强制 Flash/Pro、高质量选 Pro、紧预算回落 Flash、Web SSE、
Decision Record 以及 Policy Editor Preview：

```bash
set -a; source .env; set +a
POLYGATE_API_KEY="${POLYGATE_API_KEYS%%,*}" \
POLICY_ADMIN_KEY=local-policy-admin-development \
  ./scripts/deepseek-v4-routing-smoke-test.sh
```

**5. 安全不变量**

| 不变量 | 怎么验 |
|---|---|
| Policy Editor 不经 Web Nginx 暴露 | `curl :8080/admin/policies` 返回的必须是 Chat 的 SPA 兜底页，不是编辑器——**只看状态码会得到假阳性**，200 是 SPA 兜底 |
| admin key 不进日志 | `docker compose logs \| grep -F "<key>"` 无命中 |
| `privacy=high` 不落到 DeepSeek | 发一个 `privacy=high` 请求，看 `polygate.provider` |
| finance privacy 不可降级 | 改 `finance_summary.defaults.privacy` 后 validate/publish 均应 422 |
| `/internal/routing/simulate` 不外露 | 不在 Gateway OpenAPI 中；**经 `:8080/api/` 也必须不可达** |
| 已存在的 ConfigMap 不被覆盖 | `test-deployment-policy.sh` 已用 fake deploy 断言 |

**本地 Compose 的一个预期差异**：Automation API 用的是内存仓库
（`POLICY_ALLOW_ENV_ADMIN_KEY=true`），所以 publish/rollback **不写回**
`policy-store.json`，重启容器即回到初始版本。本地冒烟验的是 API 语义与热
加载，ConfigMap 持久化只能在 EKS 上验证。

## 系统设计与团队分工

四人并行开发、契约先行的协作方式，是这个项目在工程组织上的一部分成果，
详细过程记录在下方"详细文档"。这里只记录最终的领域划分（历史任务分配，
项目已收尾，不再是进行中的安排）：

| 成员 | 负责领域 | 交付内容 |
|---|---|---|
| A | `gateway/`、Automation 编译/API、Policy Runtime | 路由决策引擎、Preview 编译与代码生成、Policy Schema、Gateway 侧策略热加载与路由模拟 |
| B | `providers/`、Automation 调度执行、Policy 控制面 | Redis Store、优先队列与 Worker、Policy 生命周期（validate/simulate/publish/rollback） |
| C | `deploy/`、`monitoring/`、部署脚本 | Kubernetes/EKS 接线、RBAC 与 Secret、Prometheus/Grafana、集成测试闸门 |
| D | `web/`、`agent/`、`.pi/extensions/`、Policy Editor | Chat UI、需求卡片、Pi Extension、策略编辑器前端 |
| 全体 | `contracts/` | 评审和冻结跨服务接口 |

`contracts/` 是跨服务接口的唯一真相来源；改动历史上要求全体确认，这条规则
在整个开发周期内被严格执行。

## 详细文档

- **[PolyGate 项目全貌与 30 分钟汇报方向总纲](./document/PolyGate_项目全貌与30分钟汇报方向总纲_2026-07-26.md)** ——
  最完整、最新的系统说明，涵盖架构、三条关键链路、Policy 生命周期、可观测性、
  Kubernetes 与安全设计；本 README 与其冲突时，以总纲为准
- [Automation Service](./automation/README.md)
- [Agent / Pi 接口边界](./agent/README.md)
- [Kubernetes 部署说明](./deploy/README.md) · [运行手册](./deploy/RUNBOOK.md)
- [本地监控说明](./monitoring/README.md) · [Kubernetes 监控部署说明](./deploy/monitoring/README.md)
- [Project Poster (PDF)](./PolyGate_Poster_final.pdf)

## 契约与协作规则（历史约定，供参考）

- `contracts/` 是跨服务接口的唯一真相来源，改动需全体确认。
- 不提交 `.env`、AWS 凭证、Provider API Key、Grafana 密码或真实员工 Prompt。
- Apple Silicon 本地开发可使用原生镜像，但推送到 x86_64 EKS 节点的镜像必须
  使用 `--platform linux/amd64`。
- 监控页面与 Policy Editor 是管理员内部工具，不通过公网 NodePort 或
  LoadBalancer 直接暴露。

## 范围边界

课程项目优先保证可解释、可演示和可复现。以下明确不在本项目范围内，属于
后续扩展方向，不是验收前置条件：语义缓存、KEDA、Provider CRD、复杂多租户
计费、生产级身份系统。