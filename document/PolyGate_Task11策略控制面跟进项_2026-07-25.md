下面两条是 Task 3 复核(PR #41)时记下的跟进项,当时判定不阻塞 Task 3,因为两者都需要真实 K8s 环境才能演练,正好落在 Task 11 的恢复演练里。行号已对照当前 `main`(`def45de`)核实。

---

# Issue 1

## 标题

Automation readiness 一旦置否就永不自愈,且 liveness 不会重启它

## 标签建议

`bug` `automation` `task-11` `kubernetes`

## 严重度

中。触发条件不常见(需要 repository 在一次 publish 期间既写失败、随后读也失败),但一旦发生就需要人工介入才能恢复,且现象具有误导性。

## 行为

`PolicyManager._commit()` 在 reconciliation 彻底失败时把 `_ready` 置为 `False`([automation/app/policy_manager.py:316](automation/app/policy_manager.py#L316)):

```python
except RepositoryUnavailable:
    try:
        persisted = self._repository.load()
    except RepositoryUnavailable:
        self._ready = False          # ← 只在这里置否
        raise RepositoryUnavailable(...) from None
```

而 `_ready = True` 的**唯一两处**赋值(第 321、337 行)都在 `_commit()` 内部。也就是说:**只有再次发起 publish/rollback 才可能把它复位。**

`/ready` 直接反映这个标志([automation/app/main.py:348](automation/app/main.py#L348)):

```python
@app.get("/ready")
def ready() -> dict[str, str]:
    if policy_manager is not None and not policy_manager.ready:
        raise HTTPException(status_code=503, detail="policy manager unavailable")
```

## 为什么会卡死

`deploy/automation.yaml` 的探针配置是关键:

```yaml
readinessProbe:
  httpGet: { path: /ready, port: http }
  periodSeconds: 5
  failureThreshold: 3
livenessProbe:
  httpGet: { path: /health, port: http }   # ← 注意不是 /ready
  periodSeconds: 10
  failureThreshold: 3
```

于是形成一个闭环死锁:

1. `/ready` 开始返回 503,约 15 秒后 Pod 被摘出 Service endpoints。
2. 管理员经 Service 访问 Policy API → 打不通,**而唯一能复位 `_ready` 的操作就是 publish**。
3. `livenessProbe` 走的是 `/health`,它不看 `_ready`,所以**探针一直通过,Pod 不会被自动重启**。

结果:ConfigMap 恢复可读之后,Pod 依然活着、依然 not ready、依然在 Service 外面,而且没有任何自动机制能把它救回来。恢复必须靠人工 `kubectl rollout restart deployment/automation`。

## 现有测试覆盖

测试断言了置否状态,但**没有任何测试断言恢复**:

- `automation/tests/test_policy_manager.py:238` — `assert manager.ready is False`
- `automation/tests/test_policy_api.py` 里 `/ready` → 503

## 复现思路(Task 11 演练)

本地 Compose 无法复现:Compose 走 `InMemoryPolicyRepository`,它不抛 `RepositoryUnavailable`。需要在 EKS 上制造"ConfigMap 写失败且随后读也失败"的窗口,例如临时用 RBAC 收回 `polygate-policy-writer` 的权限、在此期间发起一次 publish、然后把权限还回去,观察 `/ready` 是否自行恢复。

**说明:这一条是代码审阅得出的结论,尚未实测复现。** Task 11 应当先确认它真的会发生,再决定修法。

## 建议修法

在成功读取 repository 时重新置位,而不是只在 `_commit()` 里。最小改动是在 `active` / `history` 这类读路径成功后复位,或让 `/ready` 直接做一次轻量的 repository 探测而不是读缓存标志。另外值得讨论 `livenessProbe` 是否也该看 `/ready`——但要小心别把"策略暂时不可用"变成"反复重启"。

---

# Issue 2

## 标题

Kubernetes policy repository 把所有 ≥400 都归为 RepositoryUnavailable,永久性错误被报成"暂时不可用"

## 标签建议

`enhancement` `observability` `automation` `task-11` `kubernetes`

## 严重度

低到中。不影响正确性,但会显著拖慢排障——尤其在与 Issue 1 叠加时。

## 行为

[automation/app/kubernetes_policy_repository.py:127](automation/app/kubernetes_policy_repository.py#L127):

```python
if response.status_code == 409:
    raise RepositoryConflict("Kubernetes policy ConfigMap revision conflict")
if response.status_code >= 400:
    raise RepositoryUnavailable("Kubernetes policy ConfigMap is unavailable")
```

除 409 之外,一切 4xx/5xx 都变成同一个异常和同一句话。但这里面混着性质完全不同的情况:

| 状态码 | 真实含义 | 是否会自愈 |
|---|---|---|
| 401 / 403 | ServiceAccount token 失效,或 RBAC 配错 | **不会**,要改配置 |
| 404 | ConfigMap 不存在(bootstrap 没跑,或名字/命名空间写错) | **不会** |
| 422 | 请求体被 API server 拒绝 | **不会** |
| 5xx | API server 真的不可用 | 会 |

管理员看到的只有 `503 {"detail": "policy repository unavailable"}`。

## 与 Issue 1 叠加后的效果

RBAC 配错(403)会走上面这条路径 → `RepositoryUnavailable` → 触发 Issue 1 的 reconciliation 失败分支 → `_ready = False` → Pod 永久 not ready。最终现象是:**"Pod 活着但永远 not ready,只给一句通用的 503"**,而真实原因是一行 RBAC 配置写错了。Task 11 的 RBAC 演练会正面撞上这个。

另外 `RepositoryCorrupt` 是 `RepositoryUnavailable` 的子类([policy_repository.py:33](automation/app/policy_repository.py#L33)),所以 `_commit()` 的 `except RepositoryUnavailable` 也会吞掉它——一份**永久损坏**的 ConfigMap 同样被当作"暂时不可用"处理。这个行为本身合理(损坏时确实该让 readiness 失败),但同样会丢失"这是永久性问题"的信息。

## 建议修法

区分状态码类别,至少让永久性错误带上可识别的信息:

- 401/403 → 单独的异常类型(例如 `RepositoryForbidden`),日志明确写出是鉴权/RBAC 问题;
- 404 → `RepositoryMissing`,提示 bootstrap 未执行或名称配置错误;
- 5xx 与传输层错误 → 保持 `RepositoryUnavailable`。

注意保持现有的安全约束:异常信息**不得**回显策略内容或 token,现有代码统一用 `from None` 断开异常链就是为此,修改时要延续。

对外仍然可以统一映射为 503(避免向调用方泄漏集群内部细节),关键是**日志和指标里要能区分**,让 `reason` 标签或日志事件足以定位。

---

## 两条的共同背景

它们都源自 `f733fe1`(Task 3 最后一个提交)引入的 reconciliation 逻辑。该逻辑本身是正确的加固——它解决的是"写请求结果未知"这个真实难题,并且有专门测试覆盖。这两条是它的**边界行为**,不是设计错误。

Task 3 合并时的判断是:两者都需要真实 K8s 才能演练和验证,放到 Task 11 的恢复演练里一并处理,不阻塞 Task 3。
