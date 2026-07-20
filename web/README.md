# web/  —— 成员 D（前端、测试与叙事）

## 你的验收物（来自项目书）
> 五分钟演示每一步都有清楚画面与备用录屏。

## 零后端起步（第 1 天就能做）
在 `web/` 目录启动本地静态服务器并打开页面，点「Demo response」，
它会读 `fixtures/decision-card.example.json` 渲染答案和路由决策卡片。
你不用等 A 的网关跑起来。

```bash
cd web && python -m http.server 8080
# 打开 http://localhost:8080
```

## 接真网关（A 就绪后）
本地起个静态服务器避免 CORS/file:// 问题：
```bash
cd web && python -m http.server 8080
# 打开 http://localhost:8080 ，确保网关在 http://localhost:8000
```
如需改网关地址：浏览器控制台执行 `localStorage.setItem('pg_gateway','http://...')`

## 你负责的东西
- `index.html`               用户工作台 UI + 决策卡片渲染
- 演示控制台                 调 Mock 的 `/admin/config` 做故障/延迟注入（契约 #3）
- 负载生成器                 给 HPA 演示制造请求突发（可用 `hey`/`k6`，或一段 fetch 循环）
- Dashboard 面板             展示 C 的 Grafana + 每个 Provider 的价格/延迟/健康（读网关 `/providers`）
- 截图、录屏、答辩材料

## P0 完成判据
- UI 能提交请求、显示答案 + 决策卡片
- 再次提交相同请求，卡片显示 cache HIT、cost $0
