# Argo — Terminal AI Coding Agent

Argo 是一个运行在本地终端的 AI 编程助手，无需 Docker、数据库或 Web 服务，直接在你的文件系统上工作。

## 项目结构

```
argo/
├── api/              # 领域层（Agent 核心逻辑、工具、LLM 调用）
├── argo/             # Argo TUI（终端界面，基于 prompt_toolkit）
│   ├── app.py        # 主界面与 REPL 循环
│   ├── main.py       # 启动入口
│   ├── renderer.py   # 事件渲染（颜色主题）
│   ├── session.py    # 会话持久化（~/.argo/sessions/）
│   ├── config.py     # 配置加载
│   └── adapters/     # 内存适配器（无需数据库）
├── docs/             # 设计文档与规格说明
└── api/config.yaml   # LLM 配置
```

## 快速开始

### 1. 配置模型

编辑 `api/config.yaml`：

```yaml
llm_config:
  base_url: https://api.deepseek.com/
  api_key: your_api_key_here
  model_name: deepseek-chat
```

### 2. 安装依赖

```bash
cd api
pip install -r requirements.txt
```

### 3. 启动 Argo

```bash
python -m argo
```

## 功能特性

- **纯终端 TUI**：Morandi 色调界面，`argo›` 彩色提示符，按键提示
- **会话持久化**：对话保存在 `~/.argo/sessions/`，重启后可恢复
- **工具调用可视化**：实时显示工具调用状态（⟳ 调用中 / ✓ 成功 / ✗ 失败）
- **无外部依赖**：无需 Docker、PostgreSQL、Redis

## 内置命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清屏（保留会话） |
| `/exit` | 退出 |
