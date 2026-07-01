# Argo — 开发规范

## 项目定位

Argo 是一个终端 AI 编程助手（TUI coding agent），目标是达到 Claude Code 水准。用户在项目目录下运行 `argo`，与 AI 结对编程：读写文件、执行命令、搜索代码、联网查文档，全程在终端内完成。

## 最终目标能力

按优先级排列，尚未完成的用 ❌ 标注：

| 能力 | 状态 |
|------|------|
| 文件读写（read/write/patch） | ✅ |
| Shell 执行（持久 bash session） | ✅ |
| 代码搜索（grep/find_symbol/list_dir） | ✅ |
| Git 集成 | ✅ |
| 测试运行 | ✅ |
| Todo 管理 | ✅ |
| 流式输出 | ✅ |
| 权限网关 | ✅ |
| 项目上下文注入（README + git log） | ✅ |
| Context 自动压缩 | ✅ |
| Checkpoint（写前快照） | ✅ |
| Web Fetch（抓页面/文档） | ❌ Sub-project 3 |
| Web Search（联网搜索） | ❌ Sub-project 3 |
| MCP 客户端（连接外部工具服务器） | ❌ Sub-project 4 |
| 持久化记忆（ARGO.md + .argo/memory.md） | ❌ Sub-project 5 |
| Context 压缩升级（工具输出预剪枝） | ❌ Sub-project 6 |
| 子 Agent 调度（delegate_task） | ❌ Sub-project 7 |
| LSP 诊断（编辑后自动检查类型错误） | ❌ Sub-project 8 |
| Skills 系统（markdown 可复用技能） | ❌ Sub-project 9 |

## 架构：领域驱动设计（DDD）

```
argo/                          # TUI 入口层
  main.py                      # 启动、streaming 回调、session 选择
  app.py                       # REPL 主循环
  renderer.py                  # 事件 → 终端文本渲染
  session.py                   # ~/.argo/sessions/ JSON 持久化
  permissions.py               # 权限网关
  adapters/                    # TUI 适配器（InMemory repos/uow）

api/app/
  domain/                      # 纯业务逻辑，不依赖任何框架或 IO
    models/                    # 数据模型（Pydantic）
    external/                  # 外部能力协议接口（Protocol/ABC）
    repositories/              # 仓储接口（Protocol/ABC）
    services/
      agents/                  # Agent 主体（base.py, coding_agent.py）
      runtime/                 # 运行时组件（context_engine, memory, turn, checkpoint...）
      tools/                   # 工具实现（BaseTool 子类）
      prompts/                 # 系统提示词

  infrastructure/              # 具体实现，依赖外部库
    external/
      llm/                     # OpenAI-compatible LLM 客户端
      json_parser/             # JSON 修复解析器
    repositories/              # 仓储实现（当前只有 InMemory，将来可加 SQLite）
```

**分层规则（绝对禁止违反）：**
- `domain/` 只能 import `domain/` 内部的东西，不得 import `infrastructure/` 或 `argo/`
- `infrastructure/` 可以 import `domain/`，不得 import `argo/`
- `argo/` 可以 import 任何层
- 新增外部能力（LLM、HTTP、MCP…）先在 `domain/external/` 定义 Protocol，再在 `infrastructure/` 实现

## 工具开发规范

新增工具的标准步骤：

1. 在 `domain/services/tools/` 创建工具类，继承 `BaseTool`，用 `@tool(name=..., ...)` 装饰器注册方法
2. 在 `domain/services/runtime/tool_executor.py` 的 frozenset 里分类（`_SEARCH_TOOLS` / `_TERMINAL_TOOLS` / `_FILE_MUTATION_TOOLS`）
3. 在 `domain/services/agents/coding_agent.py` 的 `tools` 列表里加入实例
4. 写测试，放 `api/tests/domain/services/tools/`

## 开发规范

- **TDD**：先写测试，看到红，再写实现，看到绿
- **YAGNI**：不为假设的未来需求写代码；三行类似代码才考虑抽象
- **不写注释**：除非原因非常不直观（隐藏约束、workaround、会让人意外的不变量）
- **不写文档字符串**：好的命名本身就是文档
- **错误处理**：只在系统边界（工具执行、外部 API）处理，内部逻辑信任自己的代码
- **复用优先**：参考 hermes-agent 和 MiniCode-Python 已有实现，能直接搬来改造的不重新发明

## 安全规范

- `api/config.yaml` 和 `.env` 已在 `.gitignore`，**永远不提交**
- API Key 只从 `api/config.yaml` 读取，不硬编码，不出现在任何测试文件里
- Web Fetch 工具必须做 SSRF 防护（屏蔽 localhost、127.0.0.1、10.x、172.16-31.x、192.168.x）
- MCP 工具命令白名单：`["node", "npx", "python3", "uv", "deno", "ruby"]`

## Git 规范

**commit message 用中文**，格式：

```
<类型>: <简短描述>

[可选正文]
```

类型：
- `新增` — 全新功能
- `修复` — bug 修复
- `重构` — 不改变行为的代码改动
- `清理` — 删除死代码、整理结构
- `测试` — 只涉及测试文件
- `文档` — 文档、注释

示例：
```
新增: web_fetch 工具支持 SSRF 防护和自动去标签

修复: 流式输出中工具调用名称重复拼接的 bug
```

每个 Sub-project 完成后单独一个 commit，不要把多个不相关的改动堆在一起。

## 技术栈

- Python 3.12+，`asyncio` 全异步
- Pydantic v2（models）
- `openai` SDK（LLM 调用，支持任何兼容 OpenAI 格式的模型）
- `prompt_toolkit`（TUI 输入）
- `httpx`（HTTP 请求，将用于 Web Fetch）
- `pytest` + `pytest-asyncio`（测试）
- DeepSeek API（当前 LLM，`api/config.yaml` 配置）

## 参考项目

| 项目 | 路径 | 参考重点 |
|------|------|---------|
| hermes-agent | `../hermes-agent/` | MCP 实现、LSP 集成、记忆系统、context 压缩 |
| MiniCode-Python | `../MiniCode-Python/` | Web fetch/search、三层记忆、context 压缩链路、子 agent |
