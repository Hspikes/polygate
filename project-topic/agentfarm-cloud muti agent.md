# AgentFarm：云端 AI Agent 团队平台（讨论版）

## 1. 一句话介绍

AgentFarm 是一个运行在云端的 AI Agent 团队平台。

用户提交一个较复杂的任务后，系统会自动创建多个分工不同的 Agent。每个 Agent 都拥有独立的运行环境、工具权限和任务状态，它们可以并行工作、互相交接，并在中途失败后恢复。

最适合的首个应用场景是：

> 用户上传一个代码仓库并描述目标，平台自动组建一支 AI 软件工程团队，完成代码分析、修改、测试、审查和文档更新。

---

## 2. 用户实际会看到什么

用户上传一个 GitHub 仓库，并输入：

> 修复项目中的测试错误，补充缺失文档，并生成一份修改报告。

平台随后创建一支 Agent 团队：

- **Manager Agent**：理解目标、拆分任务、安排执行顺序；
- **Developer Agent**：阅读和修改代码；
- **Test Agent**：运行测试、定位错误、验证修改；
- **Reviewer Agent**：审查代码，决定是否接受修改；
- **Documentation Agent**：更新 README 和最终报告。

界面不只是展示聊天记录，而是展示整个团队的真实工作状态：

```text
Manager Agent
├── Developer Agent      正在修改 auth.py
├── Test Agent           27 / 31 tests passed
├── Reviewer Agent       等待新的代码补丁
└── Documentation Agent  正在更新 README
```

用户可以查看：

- 每个 Agent 正在做什么；
- Agent 调用了哪些工具；
- 生成了哪些文件和代码修改；
- 当前测试结果；
- Agent 之间如何交接任务；
- 已消耗的时间、资源和 API 费用；
- 哪些操作需要人工批准。

最终产出不是一段聊天内容，而是代码 diff、测试结果、修改后的仓库、更新后的文档和一份任务报告。

---

## 3. 它与普通多 Agent Demo 有什么区别

很多多 Agent 项目，本质上只是一个 Python 程序里写了几个不同角色的 Prompt：

```text
Planner 说一句
→ Coder 说一句
→ Reviewer 再说一句
```

这些 Agent 往往共用同一个运行环境，只能生成文本，程序退出后状态消失，某个 Agent 出错后也很难恢复。

AgentFarm 希望把 Agent 从“聊天角色”变成真正可以被云平台管理的工作单元：

| 普通多 Agent Demo | AgentFarm |
|---|---|
| 多个 Prompt 角色 | 多个独立 Agent 运行环境 |
| 共用一个 Python 进程 | 每个 Agent 使用独立容器或 Pod |
| 状态保存在内存 | 状态、产物和工作区持久化 |
| 失败后重新开始 | 从 checkpoint 恢复 |
| 权限基本相同 | 每个 Agent 有独立工具权限 |
| 无资源管理 | 可以限制 CPU、内存、时间和预算 |
| 只能查看对话 | 可以查看任务图、日志、工具调用和产物 |
| 单机脚本 | 多用户云端平台 |

---

## 4. 为什么这个项目需要云计算

这个项目不是先做好一个 Agent 应用，再随便部署到 Kubernetes。Agent 的工作方式本身就会产生真实的云计算需求。

### 4.1 独立和安全的执行环境

Developer Agent 和 Test Agent 可能需要运行：

```bash
pip install
npm install
pytest
git diff
python script.py
```

如果所有 Agent 直接在同一台机器上运行，一个 Agent 的代码可能影响其他用户和任务。

因此，系统可以为每个 Agent 创建独立的容器或 Kubernetes Pod，并设置：

- 独立文件系统；
- CPU 和内存限制；
- 网络访问限制；
- 可使用的工具；
- 最长运行时间；
- 允许和禁止执行的命令。

### 4.2 动态创建和释放 Agent

Agent 的负载并不稳定。一个任务可能临时需要五个 Agent 并行工作，任务完成后这些 Agent 就不再需要占用资源。

```text
任务到达
→ 创建 Agent Pod

任务拆分增加
→ 自动增加 Worker

Agent 等待审批
→ 暂停或释放计算资源

任务结束
→ 回收 Pod
```

这自然涉及 Kubernetes 调度、自动扩缩容和 Serverless / scale-to-zero 思想。

### 4.3 长任务的状态保存与恢复

一个 Agent 任务可能持续较长时间，也可能遇到 Pod 被删除、节点故障、API 暂时不可用或用户暂停任务。

系统需要保存：

- 当前任务计划；
- 已完成步骤；
- 工作区文件；
- Agent 之间的交接内容；
- 工具调用结果；
- Git commit 或 patch；
- 当前预算使用情况。

Agent 重启后可以从最近的 checkpoint 继续，而不是从头再做。

### 4.4 多 Agent 的任务协调

Agent 之间存在真实依赖关系：

```text
Test Agent 发现错误
        ↓
Developer Agent 修改代码
        ↓
Reviewer Agent 审查
        ↓
Test Agent 重新测试
        ↓
Documentation Agent 更新文档
```

因此需要任务队列、状态数据库和事件系统来协调，而不只是让几个 Agent 随机聊天。

---

## 5. 系统大致架构

```text
用户
  │
  ▼
Web Project Room
  │
  │ 提交仓库与任务
  ▼
Agent Orchestrator
  ├── 拆分任务
  ├── 创建 Agent 团队
  ├── 管理任务依赖
  ├── 控制预算和权限
  └── 保存 checkpoint
  │
  ▼
Task Queue / State Store
  │
  ▼
Kubernetes Cluster
  ├── Manager Agent Pod
  ├── Developer Agent Pod
  ├── Test Agent Pod
  ├── Reviewer Agent Pod
  └── Documentation Agent Pod
  │
  ▼
工具层
  ├── Git
  ├── 文件系统
  ├── 测试命令
  ├── 搜索工具
  └── 外部 AI API
```

其中：

- **前端**负责展示团队和任务状态；
- **Orchestrator**负责拆分任务和协调 Agent；
- **Kubernetes**负责创建、隔离、调度和恢复 Agent；
- **消息队列**负责分发任务；
- **数据库和对象存储**负责保存状态与产物；
- **外部模型 API**负责 Agent 的语言理解和决策。

因此不需要自己部署大型 GPU 模型。

---

## 6. 可以结合的热门方向

### AI Agent 与 Multi-Agent Collaboration

多个 Agent 分工协作，共同执行一个长期任务，而不是完成一次简单问答。

### Agent Sandbox

Agent 可能执行陌生代码和命令，因此必须运行在受限制的隔离环境中。

### MCP 与工具调用

可以通过统一的工具接口，让 Agent 使用 Git、文件、测试、搜索和数据库等能力。

### Serverless Agent

Agent 只有在执行任务时占用计算资源，等待期间可以暂停，任务结束后释放资源。

### Human-in-the-loop

危险操作不能由 Agent 直接执行，例如推送代码、删除文件、部署应用或访问敏感数据。系统应当暂停任务并等待用户批准。

### Agent Observability

平台需要记录并可视化：

- Agent 执行路径；
- Agent 之间的消息；
- 工具调用；
- 失败和重试；
- 每个步骤耗时；
- API token 和费用；
- CPU 和内存使用。

这使整个 Agent 工作过程可以被观察、审查和回放。

---

## 7. 最适合的现场演示

### 第一阶段：创建 AI 团队

用户上传一个带有 Bug、测试和不完整 README 的小型代码仓库。

点击“Build Agent Team”后，界面出现多个 Agent，并展示它们的启动过程。

### 第二阶段：并行工作

- Test Agent 运行测试并发现错误；
- Developer Agent 根据错误修改代码；
- Reviewer Agent 拒绝第一次修改；
- Developer Agent 重新调整；
- Test Agent 验证修改；
- Documentation Agent 更新 README。

前端实时展示整个任务图和事件时间线。

### 第三阶段：展示云端弹性

同时提交多个项目：

```text
任务数量：1 → 10
Agent Pod：2 → 8
```

任务完成后，Agent Pod 数量自动下降。

### 第四阶段：展示故障恢复

现场删除正在工作的 Developer Agent Pod。

Kubernetes 重新创建 Agent，系统从 checkpoint 恢复任务并继续执行。

### 第五阶段：展示权限审批

Agent 请求执行：

```text
git push
```

界面弹出：

```text
Developer Agent requests permission:
Push branch agent/fix-auth

[Approve] [Reject]
```

这可以同时展示 Agent、安全控制和用户参与。

---

## 8. 项目最重要的卖点

这个项目的重点不是“我们做了多个 AI 角色”，而是：

> 我们把多个能执行真实任务的 AI Agent 变成了可以被云平台创建、隔离、调度、暂停、恢复、观察和控制的工作单元。

应用层故事很直观：

> 用户得到一支可以工作的云端 AI 软件工程团队。

云计算层也不是装饰：

- 没有容器隔离，Agent 不安全；
- 没有持久化，任务不能恢复；
- 没有调度和扩缩容，大量 Agent 无法有效运行；
- 没有任务队列，多 Agent 无法可靠协作；
- 没有观测，用户无法知道 Agent 做了什么；
- 没有权限控制，Agent 可能执行危险操作。

---

## 9. 主要风险

### 风险一：Agent 只是在演戏

如果 Agent 只互相发送文本，没有真正执行代码、测试和文件操作，项目会显得很空。

因此必须确保至少有：

- 独立执行环境；
- 真实工具调用；
- 真实文件产物；
- 可验证的测试结果。

### 风险二：项目范围太大

不应该一开始就做通用 Agent 平台。建议第一版只支持：

> 小型软件仓库的代码修复、测试、审查和文档更新。

### 风险三：Agent 结果不稳定

可以通过以下方式降低演示风险：

- 使用预先准备的小型仓库；
- 设计明确的测试用例；
- 限制 Agent 可执行的任务范围；
- 对部分步骤使用固定工作流；
- 准备 Mock Agent 作为备用；
- 保留人工审批和人工接管能力。

### 风险四：最后变成纯前端展示

必须现场证明至少几个真实云能力：

- Agent Pod 动态创建；
- 多任务自动扩容；
- 删除 Pod 后恢复；
- 工作区和状态持久化；
- 资源限制和权限拒绝；
- 任务完成后回收资源。

---

## 10. 当前建议

AgentFarm 值得继续深入，但应当先把目标锁定为：

> **面向软件工程任务的云端 AI Agent 团队平台。**

它既有容易理解的上层应用，也能自然展示 Kubernetes、容器隔离、任务调度、自动扩缩容、持久化、容错、可观测性和安全控制。

下一步需要进一步确定：

1. 用户提交任务后的标准工作流程；
2. 第一版包含哪些 Agent；
3. Agent 之间如何共享和交接工作；
4. 哪些操作需要人工审批；
5. 演示仓库与故障场景；
6. 最低可行版本与可选扩展功能。
