# PolyGate Providers

## Design goal

Provider adapter 统一模型接口与 usage 口径；可控 Mock 支持延迟和故障注入，
用于验证 Gateway 的重试、熔断与故障切换行为。

## 本地运行两个 Mock
```bash
cd providers/mock
pip install -r requirements.txt
MOCK_NAME=mock-a uvicorn app.main:app --port 8081
MOCK_NAME=mock-b uvicorn app.main:app --port 8082   # 另开一个终端
```

## 故障注入（契约 #3）
```bash
curl -X POST localhost:8081/admin/config -H 'Content-Type: application/json' \
     -d '{"fail_rate": 1.0, "extra_latency_ms": 2000}'   # 让 mock-a 全挂 + 变慢
curl -X POST localhost:8081/admin/reset                   # 恢复正常
```

## Module layout

- `providers/mock/app/main.py`  两个 Mock（同一镜像，靠 MOCK_NAME 区分）
- `gateway/app/adapters.py` 真实 Provider 的鉴权与格式归一化
- `gateway/app/cache.py` Redis 连通性与缓存键规范化

## Core behavior

- mock-a、mock-b、deepseek-flash、deepseek-pro 都能返回**带 usage 的 OpenAI 格式**响应（契约 #5）
- `/admin/config` 能一键让某个 Mock 报错或变慢

## 契约 #5 提醒
Mock 必须生成 `usage` 字段，否则 Gateway 无法统一估算成本。
