# PolyGate Agent 自动化并行开发对接清单

## 开始开发前

1. 先合并本次 Skeleton PR，确保 `contracts/automation-*.schema.json` 成为唯一真相来源。
2. 所有人执行 `git fetch origin`，从 Skeleton 合并后的 `origin/main` 创建自己的分支。
3. 不允许单独修改冻结契约；需要修改时在群里同时通知 A/B/C/D。
4. 不使用 `git add .`，不提交 API Key、Token、Secret YAML 或真实员工 Prompt。

## 并行写入范围

| Member | 主要写入范围 | 不应直接修改 |
|---|---|---|
| A | `automation/app/models.py`、`templates.py`、compiler/API、Gateway client | B 的 Redis/Worker、D 的 Agent/Web、C 的 deploy |
| B | 新增 Redis store、scheduler、worker 及对应测试 | Gateway 路由、Pi Extension、Grafana JSON |
| D | `agent/`、`web/` | Automation 政策、Redis、Kubernetes 清单 |
| C | `deploy/`、`monitoring/`、部署与 smoke scripts | Pi 对话逻辑、模板算法、Gateway 路由 |

共享组合文件（`docker-compose.yml`、Automation composition root、Prometheus
配置）由 C 在各功能 PR 合并后统一接线，其他成员通过小型 PR 提供所需环境变量和端口说明。

## 固定服务与端口

```text
web/nginx      public entry (final LoadBalancer)
agent          8030 (planned, ClusterIP)
automation     8020 (ClusterIP)
gateway        8000 container / service port 80
redis          6379; cache DB 0, Automation DB 1 or dedicated key prefix
prometheus     9090 (private)
grafana        3000 (private)
```

## Day 1 联调门槛

```bash
python3 scripts/tests/test-automation-contracts.py
docker compose config
docker compose up --build automation
curl http://localhost:8020/health
curl http://localhost:8020/v1/templates
```

每条开发线在 PR 描述中声明：消费了哪个契约、提供了哪个接口、运行了哪些测试、是否改变了指标名或环境变量。

## PR 顺序

```text
1. contracts + runnable Automation skeleton
2. A: compiler/API + Gateway client
3. B: Redis repository + Scheduler Worker
4. D: Pi Agent + Chat Web
5. C: Compose/EKS/Nginx + Prometheus/Grafana + cloud smoke tests
```

后续 PR 必须先 rebase/merge 最新 `main`，再执行契约测试和自己负责的测试。
