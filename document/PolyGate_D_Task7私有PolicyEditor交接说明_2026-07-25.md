# 【Task 7 交接】私有 Policy Editor — 依赖已全部就位,可以开工

@D Task 7 的所有上游依赖已经合并进 `main`(当前 `def45de`)。Task 7 现在是**整条策略中心线路的唯一关键路径卡点**:Task 10(本地集成回归)只等它,而 Task 10 又挡着 Task 11(EKS 部署与演示证据)。

先同步进度:T1/T2/T3/T4/T5/T6/T8 已全部合并。Gateway 侧热加载(A)和 Worker 侧热加载(C,#44)都已上线,也就是说**你发布策略后,Gateway 和 Worker 会在 5 秒内自动生效**——演示主线上只差你这个界面。

计划原文:`docs/superpowers/plans/2026-07-24-policy-management.md` 第 1323-1454 行(Task 7 Step 1-8)。下面是我(C)对照已合并代码核实过的实际接口现状,以及几个**从计划文档里看不出来、但会让你踩坑**的点。

---

## 一、你要建的文件(目前都不存在,全新)

```
automation/admin/index.html
automation/admin/policy-admin.js
automation/admin/policy-admin.css
automation/tests/test_policy_admin_ui.py
```

要改:`automation/app/main.py`(挂载静态资源)、`automation/Dockerfile`、`automation/README.md`。

`automation/Dockerfile` 目前是 `COPY automation /app/automation`,所以 `automation/admin/` 会**自动**进镜像;Step 7 要你重新 build 是为了验证,不是因为要改 COPY 规则。

`automation/app/main.py` 目前**没有**任何 `StaticFiles` 或 `/admin` 挂载——那是你 Step 2 的活。已有的 `/v1/admin/policies*` 是 API 路由(见下),不要和 UI 路径混淆。

---

## 二、可用的 API(已合并,我实测过)

鉴权:全部 `/v1/admin/*` 需要 `Authorization: Bearer <key>`。缺 key 或 key 错都是 **401**。本地 Compose 的 key 是 `local-policy-admin-development`。

| 操作 | 端点 | 成功码 |
|---|---|---|
| 读当前生效策略 | `GET /v1/policies/active` | 200(**无需鉴权**) |
| 版本历史 | `GET /v1/admin/policies` | 200,返回数组 |
| 单个版本 | `GET /v1/admin/policies/{version}` | 200 / 404 |
| 校验 | `POST /v1/admin/policies/validate` | 200 |
| 影响预览 | `POST /v1/admin/policies/preview` | 200 |
| 发布 | `POST /v1/admin/policies/publish` | **201** |
| 回滚 | `POST /v1/admin/policies/{version}/rollback` | **201** |

请求体:

```
publish   { base_version: int>=1, change_note: str(1..500), policy: <完整 draft> }
rollback  { base_version: int>=1, change_note: str(1..500) }          // 目标版本在 URL 里
validate  <完整 draft>                                                // 直接是 draft,没有包装
preview   { policy: <draft>, gateway_cases: [...], priority_cases: [...] }
```

---

## 三、六个会让你踩坑的点(计划文档里没写)

### 1. `validate` 失败不是 `{valid:false}`,而是 HTTP 422

成功时返回 `200 {"valid": true, "warnings": [...]}`。**校验失败时直接是 422**,body 里没有 `valid` 字段。所以别写 `if (res.valid)` —— 要先判 `res.ok`。护栏违规(比如把 `finance_summary` 的 privacy 改成 standard)走的也是 422。

### 2. 422 的错误信息里**不含出错的值和边界**

服务端刻意把 `input` 和 `ctx` 从校验错误里剥掉了([main.py:337-341](automation/app/main.py:337)),只剩 `loc`/`msg`/`type`:

```json
{"detail":[{"loc":["body","gateway","assumed_output_tokens"],"msg":"...","type":"..."}]}
```

这是防止把策略值和 change note 泄漏到错误响应里(有专门测试守着)。**对你的影响**:你没法靠后端告诉用户"必须 ≥1"。表单的 min/max 提示得你自己在前端写死。取值范围见下方第四节。

### 3. `preview` 的 `gateway_cases[]` 必须带 `messages`,少了就 422

我第一次调就栽在这:

```json
{
  "messages": [{"role": "user", "content": "任意示例内容"}],   // 必填,min 长度 1
  "polygate": {"quality": "high", "privacy": "standard"}
}
```

`priority_cases[]` 则是完整的 `AutomationIntent`,六个字段全都必填:`employee`、`department`、`scenario`、`urgency`、`prompt`、`preferences{quality,privacy,max_cost_usd,latency_target_ms}`。

### 4. `preview` 响应结构(渲染 Impact preview 用)

```
{
  base_version: int,
  diff: [ {path, before, after}, ... ],
  warnings: [...],
  simulations: {
    routing:  [ {case_id, before, after}, ... ],
    priority: [ {case_id, before_score, after_score}, ... ],
    queue:    { before_order: [case_id...], after_order: [case_id...] }
  }
}
```

`case_id` 在一次 preview 内**保证唯一**(重复的 case 会带 `-2`、`-3` 后缀),所以你可以放心用它做 before/after 的索引键。这一点是我在 Task 3 复核时修的(#41 里的 `disambiguate_case_id`)——修之前 routing 的 case_id 会重复,按它索引会让表格行互相覆盖。

### 5. ETag:`GET /v1/policies/active` 支持 304

响应头带 `ETag: "policy-v{version}"`。带 `If-None-Match` 重复请求会拿到 **304 且无 body**,`fetch` 下你得单独处理这个分支,别把它当失败。轮询当前版本时用它能省流量。

### 6. 并发冲突是 409,文案计划里已规定

`publish` 时若 `base_version` 已不是当前 active,返回 **409**。计划 Step 5 指定了固定文案:

> The active policy changed while you were editing. Reload the latest version and preview again.

---

## 四、表单控件的取值范围(直接抄)

**gateway**(5 个控件,Step 6 要求全部存在)

| 字段 | 类型/范围 |
|---|---|
| `assumed_output_tokens` | int 1..32768 |
| `balanced_price_tolerance` | float 0..2 |
| `budget_mode` | `soft` \| `hard` |
| `latency_mode` | `soft` \| `hard` |
| `high_quality_strategy` | `prefer_real` \| `lowest_cost` |

**automation.urgency_scores**(4 个,各 int 0..1000)
额外硬约束:必须严格递减 `critical > high > normal > low`,否则 422。建议前端即时校验,别等提交。

**automation.queue**(5 个)

| 字段 | 范围 |
|---|---|
| `waiting_bonus_interval_seconds` | 1..3600 |
| `waiting_bonus_points` | 0..100 |
| `waiting_bonus_cap` | 0..1000(单位是**分数**,不是区间数) |
| `starvation_streak_threshold` | 1..100 |
| `starvation_wait_seconds` | 1..86400 |

**automation.scenarios**(4 个固定 key)
`production_incident`、`customer_escalation`、`finance_summary`、`marketing_batch`。
每个含 `weight`(int 0..500)+ `defaults{quality, privacy, max_cost_usd 0..10, latency_target_ms 1..120000}`。

**锁死的护栏**:`finance_summary.defaults.privacy` 必须恒为 `high`,计划要求渲染成 **disabled 的 `high` 控件**,不是可选项。

顺带一句 Task 4 带来的行为,值得在 queue 区域加一句提示:**调整 queue 参数会追溯影响已在排队中的任务**(等待奖励按真实等待时长实时重算),不只影响新任务。

---

## 五、安全红线(Step 1/6 会被测试卡)

- admin key **只存 JS 模块变量**。不得进 URL、localStorage、sessionStorage、cookie、console、DOM 属性或错误信息。
- 生成的 HTML 里不得出现 `POLICY_ADMIN_KEY`、`localStorage`、`sessionStorage` 这些字符串——回归测试会直接 grep。
- 不用 iframe、不走公网 Web NodePort、不碰 Grafana 凭据。
- 任何编辑动作后必须**作废**上一次的 validate/preview 结果;Publish 按钮在当前草稿拿到成功的 validate + preview 之前保持 disabled。

---

## 六、本地怎么跑(有个坑)

```bash
docker compose up -d --build automation
curl -s localhost:8020/v1/policies/active | python3 -m json.tool
```

**注意**:Compose 下 Automation API 用的是内存仓库(`POLICY_ALLOW_ENV_ADMIN_KEY=true` 决定的),所以你 publish 出来的新版本**不会写回** `/config/policy-store.json`,重启容器就回到 v4。这是本地开发的预期行为,不是 bug——K8s 下才是真正的 ConfigMap 持久化。你调 UI 时不用担心把本地数据搞脏。

跑测试要注意两点:仓库没有 `pytest.ini`/`conftest.py`,automation 的测试 import `automation.app.*`,所以**必须从仓库根目录用 `python -m pytest`**;另外宿主机 Python 是 3.14 而项目目标 3.12,`requirements.txt` 里也没有 pytest,建议在 `python:3.12-slim` 容器里跑:

```bash
docker run --rm -v "$PWD":/src -w /src polygate-automation:policy-ui sh -c 'pip install -q pytest; python -m pytest automation/tests/ -q'
```

当前 automation 套件基线是 **142 passed**,你的改动不应让它下降。

---

## 七、完成后

按计划 Step 8 提交,PR 里请注明第 1、2 点(validate 的 422 语义、错误信息不含值)是如何在 UI 上处理的——这两点最容易做成"用户看不懂哪里错了"。

Task 7 合并后我(C)会接 Task 10 的本地集成回归和 Task 11 的 EKS 演练。**如果你排期上有困难或需要我先把某部分(比如静态资源挂载那段 main.py 改动)垫一下,尽早说** —— 目前整条线在等这一个任务。

有任何接口对不上的地方直接找我,API 侧我在 Task 3 复核时逐个实测过。
