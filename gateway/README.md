# gateway/  —— 成员 A（网关与策略）

## 你的验收物（来自项目书）
> 同一请求能解释为何选择某 Provider —— 决策卡片里的 `reason` 必须是人话。

## 本地开发（不依赖 B 的 Mock）
```bash
cd gateway
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PROVIDERS_FILE=../contracts/providers.yaml
FAKE_ADAPTER=1 uvicorn app.main:app --reload   # 用假 Adapter，返回罐头数据
```
访问 http://localhost:8000/providers 看注册表；POST /v1/chat/completions 测路由。

## 接入 B 的 Mock（第 3 天集成）
去掉 `FAKE_ADAPTER=1`，用 `docker compose up` 起全套即可。

## 你负责的文件
- `app/main.py`     入口、缓存查、组装决策卡片、request_id（**已种下，别删**）
- `app/router.py`   ⭐ 路由核心，P0 的规则都在这，扩展策略只改这里
- `app/adapters.py` Adapter 归一化（真实 API 的鉴权细节和 B 一起弄）
- `app/cache.py`    精确/规范化缓存（normalize 规则是和 B 的共享契约）
- `app/cost.py`     成本估算
- `app/registry.py` 读 providers.yaml

## P0 完成判据
- `/v1/chat/completions` 端到端返回「答案 + 决策卡片」
- 卡片包含 chosen_provider / reason / cache_hit / cost / latency / tokens / request_id
- 改约束（quality/privacy/max_cost）能看到路由结果和 reason 相应变化

## 留给 P1 的 TODO（别在 P0 做）
- main.py 里 provider 调用失败处 → 加重试 + 熔断 + failover（B 主导）
- HEALTH 静态表 → 换成 B 的实时健康探测
