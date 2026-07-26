# providers/  —— 成员 B（Provider 与可靠性）

## 你的验收物（来自项目书）
> 人为使 A（当前 Provider）故障后，下一请求可切换到 B。
> （P0 先把三个 Provider 跑通并返回统一格式；故障切换是 P1，但故障开关 P0 就要能用）

## 本地运行两个 Mock
```bash
cd providers/mock
pip install -r requirements.txt
MOCK_NAME=mock-a uvicorn app.main:app --port 8081
MOCK_NAME=mock-b uvicorn app.main:app --port 8082   # 另开一个终端
```

## 故障注入（契约 #3，D 的演示控制台会调它）
```bash
curl -X POST localhost:8081/admin/config -H 'Content-Type: application/json' \
     -d '{"fail_rate": 1.0, "extra_latency_ms": 2000}'   # 让 mock-a 全挂 + 变慢
curl -X POST localhost:8081/admin/reset                   # 恢复正常
```

## 你负责的文件 / 任务
- `providers/mock/app/main.py`  两个 Mock（同一镜像，靠 MOCK_NAME 区分）
- 真实 Adapter：真实 API 的鉴权/格式差异，在 `gateway/app/adapters.py` 里和 A 对齐
- Redis 缓存的连通性与 key 规范化，和 A 在 `gateway/app/cache.py` 共同维护
- **强烈建议接 2 个便宜的真实 Provider**（如 DeepSeek/Qwen），让至少一次真实跨供应商切换是真的

## P0 完成判据
- mock-a、mock-b、deepseek-flash、deepseek-pro 都能返回**带 usage 的 OpenAI 格式**响应（契约 #5）
- `/admin/config` 能一键让某个 Mock 报错或变慢

## 契约 #5 提醒
Mock 必须伪造 `usage` 字段，否则 A 无法统一算钱。已在 main.py 实现，改动别删掉它。
