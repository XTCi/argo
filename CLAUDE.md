# Argo — 开发规范

## 项目定位

Argo 是一个运行在本地终端的 AI 编程助手，目标是达到 Claude Code 水准——能真正写代码、写项目、带来生产力提升。用户在项目目录下运行 `argo`，与 AI 结对编程：读写文件、执行命令、搜索代码、联网查文档、调用外部工具，全程在终端内完成，无需云端沙箱。

参考项目（均在 `../` 目录下，开发前先读代码）：
- `../hermes-agent/` — MCP、权限、技能、context 压缩、loop 检测、threat 扫描
- `../MiniCode-Python/` — Web 工具、三层记忆、turn-scoped 权限、工具输出持久化、hooks

---

## 最终目标能力

| 能力 | 状态 | Sub-project |
|------|------|-------------|
| 文件读写（read/write/patch + 模糊匹配） | ✅ | — |
| Shell 执行（持久 bash session） | ✅ | — |
| 代码搜索（grep/find_symbol/list_dir/read_file_range） | ✅ | — |
| Git 集成 | ✅ | — |
| 测试运行（pytest 结构化结果） | ✅ | — |
| Todo 管理 | ✅ | — |
| 流式输出 | ✅ | — |
| 权限网关（基础版） | ✅ | — |
| 项目上下文注入（README + git log + 目录结构） | ✅ | — |
| Context 自动压缩（LLM 摘要） | ✅ | — |
| Checkpoint（写前快照） | ✅ | — |
| **Web Fetch**（抓页面/文档，SSRF 防护） | ❌ | 3 |
| **Web Search**（联网搜索，无需 API key） | ❌ | 3 |
| **MCP 客户端**（stdio，动态工具注册） | ❌ | 4 |
| **持久化记忆**（ARGO.md + .argo/memory.md，跨 session） | ❌ | 5 |
| **Context 压缩升级**（两阶段：工具输出预剪枝 + LLM 摘要） | ❌ | 6 |
| **工具输出磁盘持久化**（大输出写磁盘，context 里放预览桩） | ❌ | 6 |
| **Loop 检测**（重复失败 / 无进展自动中断） | ❌ | 6 |
| **子 Agent 调度**（delegate_task，串行，禁止递归） | ❌ | 7 |
| **Turn-scoped 权限**（allow_once/allow_turn/deny_with_feedback） | ❌ | 8 |
| **危险命令 / 威胁扫描**（regex 模式库） | ❌ | 8 |
| **LSP 诊断**（写文件后自动拿 pyright 类型错误） | ❌ | 9 |
| **Hooks 系统**（PRE/POST_TOOL_USE，外部脚本订阅） | ❌ | 10 |
| **Skills 系统**（markdown + frontmatter，渐进式加载） | ❌ | 11 |
| **Cost 追踪**（按 token 计 USD，含 cache-read 定价） | ❌ | 12 |

---

## 架构：领域驱动设计（DDD）

```
argo/                          # TUI 入口层
  main.py                      # 启动、streaming 回调、session 选择
  app.py                       # REPL 主循环
  renderer.py                  # 事件 → 终端文本渲染
  session.py                   # ~/.argo/sessions/ JSON 持久化
  permissions.py               # 权限网关（将扩展为 turn-scoped）
  adapters/                    # TUI 适配器（InMemory repos/uow）

api/app/
  domain/                      # 纯业务逻辑，禁止依赖任何 IO 框架
    models/                    # 数据模型（Pydantic）
    external/                  # 外部能力协议（Protocol/ABC 定义接口）
    repositories/              # 仓储接口（Protocol/ABC）
    services/
      agents/                  # Agent 主体（base.py, coding_agent.py）
      runtime/                 # 运行时（context_engine, memory, turn, checkpoint...）
      tools/                   # 工具实现（BaseTool 子类）
      prompts/                 # 系统提示词

  infrastructure/              # 具体实现，可依赖外部库
    external/
      llm/                     # OpenAI-compatible LLM 客户端
      json_parser/             # JSON 修复解析器
    repositories/              # 仓储实现（InMemory；将来可加 SQLite）
```

**分层规则（禁止违反）：**
- `domain/` 只能 import `domain/` 内部，禁止 import `infrastructure/` 或 `argo/`
- `infrastructure/` 可以 import `domain/`，禁止 import `argo/`
- `argo/` 可以 import 任何层
- 新增外部能力先在 `domain/external/` 定义 Protocol，再在 `infrastructure/` 实现

---

## 工具开发规范

新增工具的标准流程：

1. 在 `domain/services/tools/` 创建工具类，继承 `BaseTool`，用 `@tool(name=..., description=..., parameters=..., required=[...])` 装饰器注册方法
2. 在 `domain/services/runtime/tool_executor.py` 的 frozenset 里分类（`_SEARCH_TOOLS` / `_TERMINAL_TOOLS` / `_FILE_MUTATION_TOOLS`）
3. 在 `domain/services/agents/coding_agent.py` 的 `tools` 列表里加入实例
4. 先写测试，放 `api/tests/domain/services/tools/`

---

## 权限与安全规范

**危险命令模式**（参考 MiniCode `permissions.py` + hermes `threat_patterns.py`）：
- 硬拦截：`rm -rf /`、`curl | bash`、`dd if=`、pipe-to-interpreter、写 `~/.ssh/authorized_keys`
- 需确认：`git reset --hard`、`git push --force`、`npm publish`、`chmod 777`、解释器直接执行（`python -c`、`bash -c`）

**Turn-scoped 权限**（目标状态，参考 MiniCode）：
- `allow_once` — 本次允许
- `allow_turn` — 本轮对话允许
- `allow_always` — 永久允许（写入 config）
- `deny_once` — 本次拒绝
- `deny_with_feedback` — 拒绝并将用户反馈文本发回给模型

**SSRF 防护**（Web Fetch 必须实现）：
- 屏蔽：`localhost`、`127.*`、`10.*`、`172.16-31.*`、`192.168.*`、`0.0.0.0`、`::1`、`fe80:`

**MCP 命令白名单**：`node`, `npx`, `python3`, `uv`, `deno`, `bun`, `cargo`, `go`, `java`，禁止 shell 特殊字符 `|&;$(){}<>\n`

**永不提交**：`api/config.yaml`、`.env`、任何含 API Key 的文件

---

## Context 压缩设计（目标状态）

参考 hermes `context_compressor.py`，两阶段：

**阶段一（无 LLM，廉价）：**
- 工具输出去重：MD5 hash，重复输出替换为 `[重复输出 — 与最近一次相同]`
- 工具输出截断：超过 4KB 的 tool result 写入 `.argo/tool-results/<hash>.txt`，context 里放预览桩（前8行 + 后3行 + 总大小 + 磁盘路径）
- 大 tool_call arguments 的 JSON-safe 缩减

**阶段二（LLM 摘要）：**
- 结构化摘要模板：已完成操作（带工具名+结果）/ 当前状态 / 待处理 / 关键决策 / 相关文件
- 摘要前缀明确告知模型：「以下是历史摘要，不要重新执行已完成的任务」
- 第 N 次压缩时，合并上次 summary 而非从头摘要（防信息丢失）

**Loop 检测**（参考 hermes `tool_guardrails.py`）：
- 相同工具+相同参数失败 5 次 → 中断并报错
- 同一工具失败 8 次 → 中断
- 无文件变更进展轮数 > 5 → 警告

---

## 记忆系统（目标状态）

两层持久化，参考 hermes `memory_tool.py`：

- **项目记忆** `ARGO.md`（项目根，可 commit）— 用户手写 + agent 可 append，每轮注入 system prompt 头部
- **本地记忆** `.argo/memory.md`（不 commit）— agent 运行中自动写入代码库发现、用户偏好、已知约束

触发写入：每轮结束后，agent 判断本轮有无值得记住的发现（新的架构决策、用户明确偏好、踩坑信息）；有则调用 `memory_write` 工具追加。

写入前做 threat 扫描：禁止写入包含 `authorized_keys`、hardcoded secrets、config 覆盖的内容。

---

## Skills 系统（目标状态）

参考 hermes `skills_tool.py` 的渐进式加载：

- **Tier 1**（`skills_list`）：只返回 name + description，token 高效
- **Tier 2**（`skill_view name`）：加载 skill 完整内容
- **Tier 3**（`skill_view name ref/file.md`）：加载 skill 内链接的参考文件

Skill 文件格式（YAML frontmatter + markdown）：
```yaml
---
name: setup-python-project
description: 初始化 Python 项目，配置 pytest + ruff + mypy
platforms: [macos, linux]
prerequisites:
  commands: [python3, uv]
---
## 步骤
...
```

存放位置：`~/.argo/skills/`（用户全局）和 `.argo/skills/`（项目级）

---

## 开发规范

- **TDD**：先写测试，看到红，再写实现，看到绿
- **YAGNI**：不为假设的未来需求写代码；三行类似代码才考虑抽象
- **不写注释**：除非原因非常不直观（隐藏约束、workaround、反直觉的不变量）
- **不写文档字符串**：好的命名本身就是文档
- **错误处理**：只在系统边界（工具执行、外部 API、用户输入）处理
- **复用优先**：开发前先读 hermes 和 MiniCode 对应实现，能搬来改造的不重新发明

---

## Git 规范

格式：`<type>(<scope>): <中文描述>`

- `type` 和 `scope` 用英文，描述用中文
- `scope` 可省略（全局性改动时省略）

**type 列表：**
```
feat      新功能
fix       bug 修复
refactor  重构（不改行为）
chore     清理、构建、配置
test      只改测试
docs      文档
perf      性能优化
```

**示例：**
```
feat(web): 新增 web_fetch 工具，支持 SSRF 防护和自动去标签
fix(streaming): 修复工具调用名称在流式积累中重复拼接的 bug
feat(mcp): 新增 MCP stdio 客户端，支持动态工具注册
refactor(context): 将工具输出截断逻辑提取为独立预剪枝阶段
chore: 删除旧 planner/react agent 系统和 web server 基础设施
test(permissions): 补充 turn-scoped 权限的边界测试用例
```

---

## 技术栈

- Python 3.12+，`asyncio` 全异步
- Pydantic v2（models）
- `openai` SDK（LLM 调用，兼容 OpenAI 格式）
- `httpx`（HTTP 请求，用于 Web Fetch 和 MCP HTTP transport）
- `prompt_toolkit`（TUI 输入）
- `pytest` + `pytest-asyncio 0.24`（测试，fixture 用 `@pytest_asyncio.fixture`）
- DeepSeek API（当前 LLM，`api/config.yaml` 配置，永不提交）
