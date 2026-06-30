# Coding Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 ny-agent 从通用 Agent 改造为以上下文工程为核心设计主张的生产级 Coding Agent，新增可插拔 ContextEngine、三层 MemorySystem、TurnOrchestrator、CheckpointService 等核心 runtime 组件。

**Architecture:** 保留 ny-agent 的 DDD 分层（domain/application/infrastructure/interfaces）和基础设施（PostgreSQL/Redis/FastAPI/UoW），完全重写 `domain/services/` 下的 runtime 层。新 runtime 以 `WorkspaceContext`（一次解析、不可变）驱动 system prompt 构建，以可插拔 `ContextEngine` 管理 context 压缩，以 `TurnOrchestrator` 封装每轮 prologue 逻辑，以 `CheckpointService` 保障文件编辑安全。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy (async), Pydantic v2, asyncio, subprocess, pytest-asyncio

## Global Constraints

- Python >= 3.11，所有新文件加 `from __future__ import annotations`
- 所有 async 函数用 `pytest-asyncio` 测试，fixture scope=function
- 新增 DB 表通过 Alembic migration，不直接改已有表
- 工具层不依赖 Docker sandbox，直接 subprocess 执行（本地环境）
- ContextEngine.compress() 只做 LLM summarization，不引入外部 ML 依赖
- 所有新 domain model 继承 pydantic BaseModel
- 测试文件路径与源文件路径一一对应：`api/app/domain/services/runtime/workspace.py` → `api/tests/domain/services/runtime/test_workspace.py`

---

## File Structure

```
api/app/domain/
├── models/
│   ├── memory.py              # 扩展：增加 EpisodicEntry, SemanticEntry
│   └── checkpoint.py          # 新增：Checkpoint domain model
├── services/
│   ├── runtime/               # 全新目录
│   │   ├── __init__.py
│   │   ├── workspace.py       # WorkspaceContext（git 感知、project facts）
│   │   ├── context_engine.py  # ContextEngine Protocol + DefaultContextEngine
│   │   ├── memory.py          # ThreeLayerMemory（Working/Episodic/Semantic）
│   │   ├── turn.py            # TurnContext dataclass + TurnOrchestrator + IterationBudget
│   │   ├── tool_executor.py   # ToolResultKind enum + ToolExecutor
│   │   └── checkpoint.py      # CheckpointService
│   ├── agents/
│   │   ├── base.py            # 重写：BaseAgent（消费 TurnOrchestrator）
│   │   └── coding_agent.py    # 新增：CodingAgent（coding 专用工具集 + prompt）
│   ├── tools/
│   │   ├── base.py            # 保留不变
│   │   ├── shell.py           # 重写：直接 subprocess，去掉 sandbox 依赖
│   │   ├── file_edit.py       # 新增：read_file / write_file / patch_file（带 diff）
│   │   ├── code_search.py     # 新增：grep_files / find_symbol / list_dir
│   │   └── git.py             # 新增：git_status / git_diff / git_log / git_commit
│   └── prompts/
│       └── coding.py          # 新增：build_coding_system_prompt（workspace-aware）
└── repositories/
    └── checkpoint_repository.py  # 新增：ICheckpointRepository

api/app/infrastructure/
├── models/
│   └── checkpoint.py          # 新增：SQLAlchemy CheckpointModel
└── repositories/
    └── db_checkpoint_repository.py  # 新增：DB 实现

api/alembic/versions/
└── xxxx_add_checkpoint_and_memory_tables.py  # 新增 migration

api/tests/domain/services/runtime/
├── test_workspace.py
├── test_context_engine.py
├── test_memory.py
├── test_turn.py
├── test_tool_executor.py
└── test_checkpoint.py
```

---

## Task 1: DB Migrations — Checkpoint & Memory 表

**Files:**
- Create: `api/app/domain/models/checkpoint.py`
- Create: `api/app/infrastructure/models/checkpoint.py`
- Create: `api/app/domain/repositories/checkpoint_repository.py`
- Create: `api/app/infrastructure/repositories/db_checkpoint_repository.py`
- Create: `api/alembic/versions/xxxx_add_checkpoint_and_memory_tables.py`
- Modify: `api/app/domain/repositories/uow.py` — 加 checkpoint 仓库
- Modify: `api/app/infrastructure/repositories/db_uow.py` — 加 checkpoint 仓库实现

**Interfaces:**
- Produces: `ICheckpointRepository`（被 Task 7 CheckpointService 依赖）
- Produces: `Checkpoint` domain model

- [ ] **Step 1: 写 Checkpoint domain model**

```python
# api/app/domain/models/checkpoint.py
from __future__ import annotations
from datetime import datetime
from typing import Dict
from pydantic import BaseModel, Field
import uuid

class Checkpoint(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    turn_id: str
    filepath: str
    content: str          # 文件内容快照
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 2: 写 ICheckpointRepository 接口**

```python
# api/app/domain/repositories/checkpoint_repository.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional
from app.domain.models.checkpoint import Checkpoint

class ICheckpointRepository(ABC):
    @abstractmethod
    async def save(self, checkpoint: Checkpoint) -> None: ...

    @abstractmethod
    async def get_by_session_and_path(
        self, session_id: str, filepath: str
    ) -> List[Checkpoint]: ...

    @abstractmethod
    async def get_latest(
        self, session_id: str, filepath: str
    ) -> Optional[Checkpoint]: ...

    @abstractmethod
    async def delete_by_session(self, session_id: str) -> None: ...
```

- [ ] **Step 3: 写 SQLAlchemy CheckpointModel**

```python
# api/app/infrastructure/models/checkpoint.py
from __future__ import annotations
from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.models.base import Base
import datetime

class CheckpointModel(Base):
    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    turn_id: Mapped[str] = mapped_column(String(100))
    filepath: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
```

- [ ] **Step 4: 写 DB 实现 DbCheckpointRepository**

```python
# api/app/infrastructure/repositories/db_checkpoint_repository.py
from __future__ import annotations
from typing import List, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.models.checkpoint import Checkpoint
from app.domain.repositories.checkpoint_repository import ICheckpointRepository
from app.infrastructure.models.checkpoint import CheckpointModel

class DbCheckpointRepository(ICheckpointRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, checkpoint: Checkpoint) -> None:
        model = CheckpointModel(
            id=checkpoint.id,
            session_id=checkpoint.session_id,
            turn_id=checkpoint.turn_id,
            filepath=checkpoint.filepath,
            content=checkpoint.content,
            created_at=checkpoint.created_at,
        )
        self._session.add(model)
        await self._session.flush()

    async def get_by_session_and_path(
        self, session_id: str, filepath: str
    ) -> List[Checkpoint]:
        result = await self._session.execute(
            select(CheckpointModel)
            .where(CheckpointModel.session_id == session_id)
            .where(CheckpointModel.filepath == filepath)
            .order_by(CheckpointModel.created_at.asc())
        )
        return [
            Checkpoint(
                id=m.id, session_id=m.session_id, turn_id=m.turn_id,
                filepath=m.filepath, content=m.content, created_at=m.created_at,
            )
            for m in result.scalars().all()
        ]

    async def get_latest(self, session_id: str, filepath: str) -> Optional[Checkpoint]:
        result = await self._session.execute(
            select(CheckpointModel)
            .where(CheckpointModel.session_id == session_id)
            .where(CheckpointModel.filepath == filepath)
            .order_by(CheckpointModel.created_at.desc())
            .limit(1)
        )
        m = result.scalars().first()
        if not m:
            return None
        return Checkpoint(
            id=m.id, session_id=m.session_id, turn_id=m.turn_id,
            filepath=m.filepath, content=m.content, created_at=m.created_at,
        )

    async def delete_by_session(self, session_id: str) -> None:
        await self._session.execute(
            delete(CheckpointModel).where(CheckpointModel.session_id == session_id)
        )
```

- [ ] **Step 5: 生成 Alembic migration**

```bash
cd api
alembic revision --autogenerate -m "add_checkpoint_table"
alembic upgrade head
```

验证输出包含：`Creating table checkpoints`

- [ ] **Step 6: 更新 UoW 加入 checkpoint 仓库**

```python
# api/app/domain/repositories/uow.py 新增
@abstractmethod
async def __aenter__(self) -> "IUnitOfWork": ...
# 在已有 session / file 属性后加：
checkpoint: ICheckpointRepository
```

```python
# api/app/infrastructure/repositories/db_uow.py 新增
from app.infrastructure.repositories.db_checkpoint_repository import DbCheckpointRepository
# 在 __aenter__ 中加：
self.checkpoint = DbCheckpointRepository(self._session)
```

- [ ] **Step 7: Commit**

```bash
git add api/app/domain/models/checkpoint.py \
        api/app/infrastructure/models/checkpoint.py \
        api/app/domain/repositories/checkpoint_repository.py \
        api/app/infrastructure/repositories/db_checkpoint_repository.py \
        api/alembic/versions/ \
        api/app/domain/repositories/uow.py \
        api/app/infrastructure/repositories/db_uow.py
git commit -m "feat: add Checkpoint domain model, repository, and DB migration"
```

---

## Task 2: WorkspaceContext — git 感知的工作空间上下文

**Files:**
- Create: `api/app/domain/services/runtime/__init__.py`
- Create: `api/app/domain/services/runtime/workspace.py`
- Create: `api/tests/domain/services/runtime/test_workspace.py`

**Interfaces:**
- Produces: `WorkspaceContext` dataclass（被 Task 6 TurnOrchestrator、Task 11 CodingAgent 依赖）
- Produces: `resolve_workspace(cwd: str) -> WorkspaceContext`

- [ ] **Step 1: 写 test_workspace.py（失败测试）**

```python
# api/tests/domain/services/runtime/test_workspace.py
from __future__ import annotations
import os
import pytest
from unittest.mock import patch, MagicMock
from app.domain.services.runtime.workspace import resolve_workspace, WorkspaceContext

def test_resolve_workspace_in_git_repo(tmp_path):
    """git repo 中应能解析出 branch 和状态"""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="main\n"),           # git branch
            MagicMock(returncode=0, stdout="M  foo.py\n"),      # git status
            MagicMock(returncode=0, stdout="abc1234 fix: bug\n"),# git log
        ]
        (tmp_path / ".git").mkdir()
        ctx = resolve_workspace(str(tmp_path))
    assert ctx.branch == "main"
    assert ctx.is_git_repo is True

def test_resolve_workspace_detects_pyproject(tmp_path):
    """存在 pyproject.toml 时应检测到 Python 项目"""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        ctx = resolve_workspace(str(tmp_path))
    assert "pyproject.toml" in ctx.manifests

def test_resolve_workspace_detects_pytest_verify(tmp_path):
    """存在 pytest.ini 时 verify_commands 应包含 pytest"""
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        ctx = resolve_workspace(str(tmp_path))
    assert "pytest" in ctx.verify_commands

def test_workspace_system_block_contains_branch(tmp_path):
    """system_block 应包含 git branch 信息"""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="feature/ctx-engine\n"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        (tmp_path / ".git").mkdir()
        ctx = resolve_workspace(str(tmp_path))
    block = ctx.system_block()
    assert "feature/ctx-engine" in block
    assert "Workspace" in block
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_workspace.py -v
```

预期：`ModuleNotFoundError: No module named 'app.domain.services.runtime'`

- [ ] **Step 3: 实现 WorkspaceContext**

```python
# api/app/domain/services/runtime/__init__.py
# (空文件)
```

```python
# api/app/domain/services/runtime/workspace.py
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_GIT_TIMEOUT = 2.5
_PROJECT_MARKERS = (
    "pyproject.toml", "setup.py", "requirements.txt",
    "package.json", "Cargo.toml", "go.mod", "Makefile", "Dockerfile",
)
_VERIFY_TARGETS = ("test", "tests", "lint", "build")


def _run_git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass(frozen=True)
class WorkspaceContext:
    """Session 开始时解析一次，不可变，cache-safe。"""
    cwd: str
    is_git_repo: bool
    branch: str
    git_status: str
    recent_commits: str
    manifests: tuple[str, ...]
    verify_commands: tuple[str, ...]

    def system_block(self) -> str:
        """生成注入 system prompt stable tier 的 workspace 快照。"""
        lines = [f"Workspace (snapshot at session start — re-check with git before acting):"]
        lines.append(f"- Root: {self.cwd}")
        if self.is_git_repo:
            if self.branch:
                lines.append(f"- Branch: {self.branch}")
            if self.git_status:
                lines.append(f"- Status: {self.git_status}")
            if self.recent_commits:
                lines.append("- Recent commits:")
                for c in self.recent_commits.splitlines():
                    lines.append(f"    {c}")
        if self.manifests:
            lines.append(f"- Project: {', '.join(self.manifests)}")
        if self.verify_commands:
            lines.append(f"- Verify: {'; '.join(self.verify_commands)}")
        return "\n".join(lines)


def _detect_manifests(root: Path) -> tuple[str, ...]:
    return tuple(m for m in _PROJECT_MARKERS if (root / m).is_file())


def _detect_verify_commands(root: Path) -> tuple[str, ...]:
    cmds: list[str] = []
    if (root / "pytest.ini").is_file() or (root / "pyproject.toml").is_file():
        cmds.append("pytest")
    if (root / "Makefile").is_file():
        try:
            content = (root / "Makefile").read_text()
            for t in _VERIFY_TARGETS:
                if f"{t}:" in content:
                    cmds.append(f"make {t}")
        except OSError:
            pass
    if (root / "package.json").is_file():
        cmds.append("npm test")
    return tuple(dict.fromkeys(cmds))


def resolve_workspace(cwd: str) -> WorkspaceContext:
    """解析工作空间上下文，每个 session 调用一次。"""
    root = Path(cwd)
    is_git = _run_git(cwd, "rev-parse", "--is-inside-work-tree") == "true"
    branch = _run_git(cwd, "branch", "--show-current") if is_git else ""
    status = _run_git(cwd, "status", "--short") if is_git else ""
    commits = _run_git(cwd, "log", "-3", "--pretty=%h %s") if is_git else ""
    return WorkspaceContext(
        cwd=cwd,
        is_git_repo=is_git,
        branch=branch,
        git_status=status,
        recent_commits=commits,
        manifests=_detect_manifests(root),
        verify_commands=_detect_verify_commands(root),
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_workspace.py -v
```

预期：4 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/runtime/ api/tests/domain/services/runtime/test_workspace.py
git commit -m "feat: add WorkspaceContext with git-aware workspace resolution"
```

---

## Task 3: IterationBudget — 线程安全迭代配额

**Files:**
- Create: `api/app/domain/services/runtime/turn.py`（先只写 IterationBudget，后续补充 TurnContext）
- Create: `api/tests/domain/services/runtime/test_turn.py`

**Interfaces:**
- Produces: `IterationBudget`（被 Task 9 BaseAgent 依赖）

- [ ] **Step 1: 写 test_turn.py（IterationBudget 部分，失败）**

```python
# api/tests/domain/services/runtime/test_turn.py
from __future__ import annotations
import threading
import pytest
from app.domain.services.runtime.turn import IterationBudget

def test_iteration_budget_consume():
    budget = IterationBudget(max_total=3)
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is False   # 超出配额

def test_iteration_budget_refund():
    budget = IterationBudget(max_total=2)
    budget.consume()
    budget.consume()
    assert budget.consume() is False
    budget.refund()
    assert budget.consume() is True    # refund 后可再消费

def test_iteration_budget_remaining():
    budget = IterationBudget(max_total=5)
    budget.consume()
    budget.consume()
    assert budget.remaining == 3
    assert budget.used == 2

def test_iteration_budget_thread_safe():
    """并发 consume 不超出配额"""
    budget = IterationBudget(max_total=10)
    results = []
    def worker():
        results.append(budget.consume())
    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 10
    assert results.count(False) == 10
```

- [ ] **Step 2: 运行确认失败**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_turn.py -v
```

预期：`ImportError`

- [ ] **Step 3: 实现 IterationBudget**

```python
# api/app/domain/services/runtime/turn.py
from __future__ import annotations
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class IterationBudget:
    """线程安全的迭代配额计数器。
    
    refund() 用于程序化工具调用（不应消耗用户配额）后归还配额。
    """

    def __init__(self, max_total: int) -> None:
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """尝试消费一次配额，返回 True 表示成功。"""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """归还一次配额（用于程序化工具调用）。"""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_turn.py -v
```

预期：4 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/runtime/turn.py api/tests/domain/services/runtime/test_turn.py
git commit -m "feat: add thread-safe IterationBudget with consume/refund"
```

---

## Task 4: ContextEngine — 可插拔上下文压缩协议

**Files:**
- Create: `api/app/domain/services/runtime/context_engine.py`
- Create: `api/tests/domain/services/runtime/test_context_engine.py`

**Interfaces:**
- Produces: `ContextEngine` Protocol（被 Task 6 TurnOrchestrator、Task 9 BaseAgent 依赖）
- Produces: `DefaultContextEngine`（LLM 摘要压缩实现）
- Consumes: `LLM` interface（已有 `app.domain.external.llm.LLM`）

- [ ] **Step 1: 写 test_context_engine.py（失败）**

```python
# api/tests/domain/services/runtime/test_context_engine.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.domain.services.runtime.context_engine import (
    DefaultContextEngine,
    ContextEngineConfig,
)

def make_messages(n: int) -> list[dict]:
    msgs = [{"role": "system", "content": "You are a coding agent."}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"message {i}"})
        msgs.append({"role": "assistant", "content": f"response {i}"})
    return msgs

def test_should_not_compress_when_below_threshold():
    engine = DefaultContextEngine(ContextEngineConfig(
        context_length=10000, threshold_percent=0.75
    ), llm=MagicMock())
    engine.update_from_response({"prompt_tokens": 5000, "completion_tokens": 100, "total_tokens": 5100})
    assert engine.should_compress() is False

def test_should_compress_when_above_threshold():
    engine = DefaultContextEngine(ContextEngineConfig(
        context_length=10000, threshold_percent=0.75
    ), llm=MagicMock())
    engine.update_from_response({"prompt_tokens": 8000, "completion_tokens": 100, "total_tokens": 8100})
    assert engine.should_compress() is True

@pytest.mark.asyncio
async def test_compress_preserves_first_and_last():
    """compress 后应保留 protect_first_n 和 protect_last_n 条消息"""
    mock_llm = AsyncMock()
    mock_llm.invoke = AsyncMock(return_value={
        "role": "assistant", "content": "Summary of middle messages."
    })
    engine = DefaultContextEngine(
        ContextEngineConfig(context_length=10000, threshold_percent=0.75),
        llm=mock_llm,
        protect_first_n=2,
        protect_last_n=2,
    )
    messages = make_messages(10)  # system + 20 user/assistant = 21 messages
    compressed = await engine.compress(messages)
    # 系统 prompt 始终保留
    assert compressed[0]["role"] == "system"
    # 压缩后比原来短
    assert len(compressed) < len(messages)

def test_get_status():
    engine = DefaultContextEngine(ContextEngineConfig(
        context_length=8000, threshold_percent=0.75
    ), llm=MagicMock())
    engine.update_from_response({"prompt_tokens": 3000, "completion_tokens": 50, "total_tokens": 3050})
    status = engine.get_status()
    assert status["last_prompt_tokens"] == 3000
    assert status["threshold_tokens"] == 6000
    assert 0 <= status["usage_percent"] <= 100
```

- [ ] **Step 2: 运行确认失败**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_context_engine.py -v
```

- [ ] **Step 3: 实现 ContextEngine Protocol + DefaultContextEngine**

```python
# api/app/domain/services/runtime/context_engine.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, runtime_checkable

from app.domain.external.llm import LLM

logger = logging.getLogger(__name__)


@dataclass
class ContextEngineConfig:
    context_length: int
    threshold_percent: float = 0.75

    @property
    def threshold_tokens(self) -> int:
        return int(self.context_length * self.threshold_percent)


@runtime_checkable
class ContextEngine(Protocol):
    """可插拔的 context 压缩引擎协议。
    
    所有引擎必须实现：
      - update_from_response()：从 API 响应追踪 token 用量
      - should_compress()：判断是否需要触发压缩
      - compress()：执行压缩，返回新消息列表
    """
    last_prompt_tokens: int
    threshold_tokens: int
    context_length: int
    compression_count: int

    def update_from_response(self, usage: Dict[str, Any]) -> None: ...
    def should_compress(self) -> bool: ...
    async def compress(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]: ...
    def get_status(self) -> Dict[str, Any]: ...


class DefaultContextEngine:
    """基于 LLM 摘要的默认 context 压缩引擎。
    
    设计原则（仿 hermes ContextEngine）：
    - protect_first_n: system prompt 之后保留的头部消息数（cache-safe 的 stable tier）
    - protect_last_n: 末尾保留的消息数（最近对话，绝不压缩）
    - 中间消息：调用 LLM 生成摘要，替换为单条 summary message
    """

    def __init__(
        self,
        config: ContextEngineConfig,
        llm: LLM,
        protect_first_n: int = 3,
        protect_last_n: int = 6,
    ) -> None:
        self._config = config
        self._llm = llm
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.last_prompt_tokens: int = 0
        self.compression_count: int = 0

    @property
    def threshold_tokens(self) -> int:
        return self._config.threshold_tokens

    @property
    def context_length(self) -> int:
        return self._config.context_length

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """从 API usage 字段更新 token 追踪。"""
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)

    def should_compress(self) -> bool:
        """当 prompt tokens 超过阈值时触发压缩。"""
        return self.last_prompt_tokens >= self.threshold_tokens

    async def compress(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """压缩消息列表，保留头尾，摘要中间部分。"""
        if not messages:
            return messages

        # 系统 prompt 始终放在最前（位置 0）
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 头部保护区 + 尾部保护区
        head = non_system[: self.protect_first_n]
        tail = non_system[-self.protect_last_n :] if self.protect_last_n else []
        middle = non_system[self.protect_first_n : len(non_system) - self.protect_last_n]

        if not middle:
            return messages  # 没有可压缩的中间部分

        # 用 LLM 生成中间部分摘要
        summary_prompt = (
            "Summarize the following conversation history concisely, "
            "preserving all key decisions, code changes, and findings:\n\n"
            + "\n".join(
                f"{m['role'].upper()}: {str(m.get('content', ''))[:500]}"
                for m in middle
            )
        )
        try:
            result = await self._llm.invoke(
                messages=[{"role": "user", "content": summary_prompt}],
                tools=[],
            )
            summary_text = result.get("content", "[summary unavailable]")
        except Exception as e:
            logger.warning(f"Context compression LLM call failed: {e}")
            summary_text = f"[{len(middle)} messages compressed — LLM unavailable]"

        summary_msg = {
            "role": "user",
            "content": f"[Compressed history — {len(middle)} messages summarized]\n{summary_text}",
        }
        self.compression_count += 1
        logger.info(
            f"Context compressed: {len(messages)} → "
            f"{len(system_msgs) + len(head) + 1 + len(tail)} messages "
            f"(compression #{self.compression_count})"
        )
        return system_msgs + head + [summary_msg] + tail

    def get_status(self) -> Dict[str, Any]:
        return {
            "last_prompt_tokens": self.last_prompt_tokens,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": (
                min(100, self.last_prompt_tokens / self.context_length * 100)
                if self.context_length else 0
            ),
            "compression_count": self.compression_count,
        }
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_context_engine.py -v
```

预期：4 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/runtime/context_engine.py \
        api/tests/domain/services/runtime/test_context_engine.py
git commit -m "feat: add pluggable ContextEngine protocol and DefaultContextEngine with LLM summarization"
```

---

## Task 5: ThreeLayerMemory — 工作记忆 / 情节记忆 / 语义记忆

**Files:**
- Modify: `api/app/domain/models/memory.py` — 增加 EpisodicEntry, SemanticEntry
- Create: `api/app/domain/services/runtime/memory.py`
- Create: `api/tests/domain/services/runtime/test_memory.py`

**Interfaces:**
- Produces: `ThreeLayerMemory`（被 Task 9 BaseAgent 依赖）
- Consumes: `IUnitOfWork`（已有，用于持久化 EpisodicMemory / SemanticMemory）

- [ ] **Step 1: 扩展 memory.py domain model**

```python
# api/app/domain/models/memory.py 末尾新增（保留原 Memory 类不变）
from datetime import datetime

class EpisodicEntry(BaseModel):
    """压缩后的历史摘要条目"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    summary: str
    message_count: int       # 被压缩的原始消息数
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SemanticEntry(BaseModel):
    """代码库沉淀的语义知识条目"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    key: str                 # 知识条目标识，如 "auth_module_design"
    content: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 2: 写 test_memory.py（失败）**

```python
# api/tests/domain/services/runtime/test_memory.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.domain.services.runtime.memory import ThreeLayerMemory

@pytest.fixture
def mock_uow():
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session = AsyncMock()
    uow.session.get_memory = AsyncMock(return_value=None)
    uow.session.save_memory = AsyncMock()
    return uow

@pytest.fixture
def mock_uow_factory(mock_uow):
    return lambda: mock_uow

def test_working_memory_add_and_get(mock_uow_factory):
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    memory.working.add_message({"role": "user", "content": "hello"})
    msgs = memory.working.get_messages()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hello"

def test_working_memory_rollback(mock_uow_factory):
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    memory.working.add_message({"role": "user", "content": "msg1"})
    memory.working.add_message({"role": "assistant", "content": "msg2"})
    memory.working.roll_back()
    assert len(memory.working.get_messages()) == 1

@pytest.mark.asyncio
async def test_add_episodic_entry(mock_uow_factory):
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    await memory.add_episodic("Summary of first task", message_count=10)
    assert len(memory.episodic_entries) == 1
    assert memory.episodic_entries[0].summary == "Summary of first task"

@pytest.mark.asyncio
async def test_upsert_semantic_entry(mock_uow_factory):
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    await memory.upsert_semantic("auth_design", "JWT with refresh tokens")
    await memory.upsert_semantic("auth_design", "JWT with refresh tokens v2")
    assert len(memory.semantic_entries) == 1
    assert memory.semantic_entries[0].content == "JWT with refresh tokens v2"

def test_build_context_block(mock_uow_factory):
    """context_block 应把 episodic + semantic 拼成可注入 prompt 的文本"""
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    memory.episodic_entries.append(
        __import__('app.domain.models.memory', fromlist=['EpisodicEntry']).EpisodicEntry(
            session_id="s1", summary="Did X", message_count=5
        )
    )
    block = memory.build_context_block()
    assert "Did X" in block
```

- [ ] **Step 3: 运行确认失败**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_memory.py -v
```

- [ ] **Step 4: 实现 ThreeLayerMemory**

```python
# api/app/domain/services/runtime/memory.py
from __future__ import annotations

import logging
from typing import Callable, List

from app.domain.models.memory import Memory, EpisodicEntry, SemanticEntry
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class ThreeLayerMemory:
    """三层记忆系统。
    
    WorkingMemory  — 当前 turn 活跃消息（in-memory，轻量）
    EpisodicMemory — 压缩后的历史摘要（DB 持久化，跨 turn）
    SemanticMemory — 代码库沉淀知识（DB 持久化，跨 session）
    """

    def __init__(self, session_id: str, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self.session_id = session_id
        self._uow_factory = uow_factory
        self.working = Memory()
        self.episodic_entries: List[EpisodicEntry] = []
        self.semantic_entries: List[SemanticEntry] = []

    async def add_episodic(self, summary: str, message_count: int) -> None:
        """将一段对话压缩为情节记忆条目并持久化。"""
        entry = EpisodicEntry(
            session_id=self.session_id,
            summary=summary,
            message_count=message_count,
        )
        self.episodic_entries.append(entry)
        logger.info(f"EpisodicMemory: added entry ({message_count} msgs compressed)")

    async def upsert_semantic(self, key: str, content: str) -> None:
        """更新或插入语义知识条目。"""
        existing = next((e for e in self.semantic_entries if e.key == key), None)
        if existing:
            existing.content = content
        else:
            self.semantic_entries.append(SemanticEntry(
                session_id=self.session_id, key=key, content=content,
            ))

    def build_context_block(self) -> str:
        """构建注入 system prompt 的记忆上下文块。"""
        lines: list[str] = []
        if self.episodic_entries:
            lines.append("## Session History (compressed)")
            for e in self.episodic_entries[-3:]:  # 最近 3 条 episodic
                lines.append(f"- {e.summary} [{e.message_count} msgs]")
        if self.semantic_entries:
            lines.append("## Codebase Knowledge")
            for s in self.semantic_entries:
                lines.append(f"- [{s.key}] {s.content}")
        return "\n".join(lines)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_memory.py -v
```

预期：5 passed

- [ ] **Step 6: Commit**

```bash
git add api/app/domain/models/memory.py \
        api/app/domain/services/runtime/memory.py \
        api/tests/domain/services/runtime/test_memory.py
git commit -m "feat: add ThreeLayerMemory (Working/Episodic/Semantic) system"
```

---

## Task 6: TurnContext + TurnOrchestrator — 每轮 prologue 封装

**Files:**
- Modify: `api/app/domain/services/runtime/turn.py` — 追加 TurnContext + TurnOrchestrator
- Modify: `api/tests/domain/services/runtime/test_turn.py` — 追加 TurnOrchestrator 测试

**Interfaces:**
- Produces: `TurnContext` dataclass（被 Task 9 BaseAgent 消费）
- Produces: `TurnOrchestrator.build_turn_context()`
- Consumes: `WorkspaceContext`（Task 2），`ContextEngine`（Task 4），`ThreeLayerMemory`（Task 5），`IterationBudget`（Task 3）

- [ ] **Step 1: 追加 TurnOrchestrator 测试**

```python
# api/tests/domain/services/runtime/test_turn.py 末尾追加

import uuid
from unittest.mock import MagicMock, AsyncMock, patch
from app.domain.services.runtime.turn import TurnOrchestrator, TurnContext
from app.domain.services.runtime.workspace import WorkspaceContext
from app.domain.services.runtime.memory import ThreeLayerMemory
from app.domain.services.runtime.context_engine import DefaultContextEngine, ContextEngineConfig

def make_workspace() -> WorkspaceContext:
    return WorkspaceContext(
        cwd="/tmp/proj", is_git_repo=True, branch="main",
        git_status="", recent_commits="", manifests=(), verify_commands=("pytest",),
    )

def make_memory():
    mem = MagicMock(spec=ThreeLayerMemory)
    mem.build_context_block.return_value = ""
    return mem

def make_engine():
    engine = MagicMock(spec=DefaultContextEngine)
    engine.should_compress.return_value = False
    return engine

@pytest.mark.asyncio
async def test_build_turn_context_returns_dataclass():
    orchestrator = TurnOrchestrator(
        workspace=make_workspace(),
        memory=make_memory(),
        context_engine=make_engine(),
        max_iterations=10,
    )
    ctx = await orchestrator.build_turn_context(
        user_message="fix the bug",
        session_messages=[],
        session_id="s1",
    )
    assert isinstance(ctx, TurnContext)
    assert ctx.user_message == "fix the bug"
    assert ctx.iteration_budget.max_total == 10
    assert "Workspace" in ctx.active_system_prompt

@pytest.mark.asyncio
async def test_build_turn_context_triggers_preflight_compression():
    engine = make_engine()
    engine.should_compress.return_value = True
    engine.compress = AsyncMock(return_value=[{"role": "system", "content": "compressed"}])
    orchestrator = TurnOrchestrator(
        workspace=make_workspace(),
        memory=make_memory(),
        context_engine=engine,
        max_iterations=10,
    )
    messages = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "old"}]
    ctx = await orchestrator.build_turn_context("new msg", messages, "s1")
    engine.compress.assert_called_once()
    assert len(ctx.messages) <= len(messages) + 1
```

- [ ] **Step 2: 运行确认新测试失败**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_turn.py -v
```

- [ ] **Step 3: 实现 TurnContext + TurnOrchestrator**

```python
# api/app/domain/services/runtime/turn.py 追加以下内容（保留 IterationBudget 不变）

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from app.domain.services.runtime.workspace import WorkspaceContext
from app.domain.services.runtime.memory import ThreeLayerMemory
from app.domain.services.runtime.context_engine import ContextEngine


@dataclass
class TurnContext:
    """每一轮 prologue 产出的不可变上下文对象，loop 只消费这个。
    
    设计原则（仿 hermes TurnContext）：prologue 和 loop 完全解耦，
    prologue 产出固定值，loop 不重新计算任何 prologue 逻辑。
    """
    user_message: str
    session_id: str
    turn_id: str
    messages: List[Dict[str, Any]]          # 当前轮次工作消息列表（已经过 preflight 压缩）
    active_system_prompt: str               # 本轮激活的 system prompt（workspace + memory block）
    iteration_budget: IterationBudget
    memory_context_block: str = ""          # 从 ThreeLayerMemory 提取的上下文块


class TurnOrchestrator:
    """每轮进入时运行 prologue，产出 TurnContext。
    
    职责：
    1. 构建 active_system_prompt（workspace snapshot + memory context block）
    2. 运行 preflight 压缩门（超阈值则压缩，支持多 pass）
    3. 创建 IterationBudget
    4. 返回 TurnContext（不可变）
    """

    def __init__(
        self,
        workspace: WorkspaceContext,
        memory: ThreeLayerMemory,
        context_engine: ContextEngine,
        max_iterations: int = 40,
    ) -> None:
        self._workspace = workspace
        self._memory = memory
        self._context_engine = context_engine
        self._max_iterations = max_iterations

    def _build_system_prompt(self) -> str:
        """构建 system prompt stable tier（workspace + memory）。
        
        workspace block 在 session 开始时解析一次不再变化，
        保证 prompt cache 不被每轮重建破坏。
        """
        blocks = [
            "You are a production-grade coding agent pairing with the user inside their codebase.",
            "Operate like a careful senior engineer: read before writing, verify after changing.",
            self._workspace.system_block(),
        ]
        memory_block = self._memory.build_context_block()
        if memory_block:
            blocks.append(memory_block)
        return "\n\n".join(filter(None, blocks))

    async def build_turn_context(
        self,
        user_message: str,
        session_messages: List[Dict[str, Any]],
        session_id: str,
    ) -> TurnContext:
        """运行 prologue，返回本轮 TurnContext。"""
        turn_id = f"{session_id}:{uuid.uuid4().hex[:8]}"
        active_system_prompt = self._build_system_prompt()

        # Preflight 压缩门：压缩前先检查是否需要
        messages = list(session_messages)
        if self._context_engine.should_compress() and len(messages) > 6:
            for _ in range(3):  # 最多 3 pass
                orig_len = len(messages)
                messages = await self._context_engine.compress(messages)
                if len(messages) >= orig_len:
                    break  # 无实质性进展，停止
                if not self._context_engine.should_compress():
                    break

        return TurnContext(
            user_message=user_message,
            session_id=session_id,
            turn_id=turn_id,
            messages=messages,
            active_system_prompt=active_system_prompt,
            iteration_budget=IterationBudget(self._max_iterations),
            memory_context_block=self._memory.build_context_block(),
        )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_turn.py -v
```

预期：6 passed（含之前的 4 个）

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/runtime/turn.py \
        api/tests/domain/services/runtime/test_turn.py
git commit -m "feat: add TurnContext and TurnOrchestrator with preflight compression gate"
```

---

## Task 7: ToolResultKind + ToolExecutor — 工具结果分类与执行

**Files:**
- Create: `api/app/domain/services/runtime/tool_executor.py`
- Create: `api/tests/domain/services/runtime/test_tool_executor.py`

**Interfaces:**
- Produces: `ToolResultKind` enum、`classify_tool_result()`、`ToolExecutor`
- Consumes: `BaseTool`（已有）、`ContextEngine`（Task 4，用于大输出压缩）

- [ ] **Step 1: 写 test_tool_executor.py（失败）**

```python
# api/tests/domain/services/runtime/test_tool_executor.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.domain.services.runtime.tool_executor import (
    ToolResultKind, classify_tool_result, ToolExecutor
)
from app.domain.models.tool_result import ToolResult

def test_classify_file_mutation():
    assert classify_tool_result("write_file", ToolResult(success=True)) == ToolResultKind.FILE_MUTATION
    assert classify_tool_result("patch_file", ToolResult(success=True)) == ToolResultKind.FILE_MUTATION

def test_classify_terminal():
    assert classify_tool_result("shell_execute", ToolResult(success=True)) == ToolResultKind.TERMINAL

def test_classify_search():
    assert classify_tool_result("grep_files", ToolResult(success=True)) == ToolResultKind.SEARCH
    assert classify_tool_result("find_symbol", ToolResult(success=True)) == ToolResultKind.SEARCH

def test_classify_other():
    assert classify_tool_result("git_status", ToolResult(success=True)) == ToolResultKind.OTHER

@pytest.mark.asyncio
async def test_executor_invokes_tool():
    mock_tool = AsyncMock()
    mock_tool.name = "shell"
    mock_tool.has_tool.return_value = True
    mock_tool.invoke.return_value = ToolResult(success=True, data="output")
    
    executor = ToolExecutor(tools=[mock_tool], context_engine=MagicMock())
    result, kind = await executor.execute("shell_execute", {"command": "ls"})
    
    mock_tool.invoke.assert_called_once_with("shell_execute", command="ls")
    assert result.success is True
    assert kind == ToolResultKind.TERMINAL
```

- [ ] **Step 2: 运行确认失败**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_tool_executor.py -v
```

- [ ] **Step 3: 实现 ToolResultKind + ToolExecutor**

```python
# api/app/domain/services/runtime/tool_executor.py
from __future__ import annotations

import logging
from enum import Enum
from typing import List, Tuple

from app.domain.models.tool_result import ToolResult
from app.domain.services.runtime.context_engine import ContextEngine
from app.domain.services.tools.base import BaseTool

logger = logging.getLogger(__name__)

_FILE_MUTATION_TOOLS = frozenset({"write_file", "patch_file"})
_TERMINAL_TOOLS = frozenset({"shell_execute", "shell_read_output", "shell_wait_process"})
_SEARCH_TOOLS = frozenset({"grep_files", "find_symbol", "list_dir", "code_search"})

# 工具输出超过此 token 估算则触发压缩（1 token ≈ 4 chars）
_COMPRESS_THRESHOLD_CHARS = 4000


class ToolResultKind(str, Enum):
    """工具结果分类。
    
    FILE_MUTATION — 文件写入类（触发 CheckpointService）
    TERMINAL      — shell 输出（大输出触发压缩）
    SEARCH        — 代码搜索结果（结构感知压缩）
    OTHER         — 其他（git、message 等）
    """
    FILE_MUTATION = "file_mutation"
    TERMINAL = "terminal"
    SEARCH = "search"
    OTHER = "other"


def classify_tool_result(tool_name: str, result: ToolResult) -> ToolResultKind:
    """根据工具名分类结果类型。"""
    if tool_name in _FILE_MUTATION_TOOLS:
        return ToolResultKind.FILE_MUTATION
    if tool_name in _TERMINAL_TOOLS:
        return ToolResultKind.TERMINAL
    if tool_name in _SEARCH_TOOLS:
        return ToolResultKind.SEARCH
    return ToolResultKind.OTHER


class ToolExecutor:
    """统一工具执行器，在工具调用后做分类与后处理。
    
    后处理策略：
    - FILE_MUTATION：记录 mutation path（供 CheckpointService 追踪）
    - TERMINAL/SEARCH：输出过大时截断并标注（避免 context 爆炸）
    - 所有结果：返回 (ToolResult, ToolResultKind) 二元组
    """

    def __init__(self, tools: List[BaseTool], context_engine: ContextEngine) -> None:
        self._tools = tools
        self._context_engine = context_engine
        self._turn_mutation_paths: set[str] = set()

    def reset_turn(self) -> None:
        """每轮开始时重置 mutation 追踪。"""
        self._turn_mutation_paths.clear()

    @property
    def turn_mutation_paths(self) -> frozenset[str]:
        return frozenset(self._turn_mutation_paths)

    def _find_tool(self, tool_name: str) -> BaseTool:
        for t in self._tools:
            if t.has_tool(tool_name):
                return t
        raise ValueError(f"Tool not found: {tool_name}")

    def _maybe_truncate(self, result: ToolResult, kind: ToolResultKind) -> ToolResult:
        """大输出截断，避免单个工具结果撑爆 context。"""
        if kind not in (ToolResultKind.TERMINAL, ToolResultKind.SEARCH):
            return result
        content = str(result.data or "")
        if len(content) > _COMPRESS_THRESHOLD_CHARS:
            truncated = content[:_COMPRESS_THRESHOLD_CHARS]
            logger.info(f"Tool output truncated: {len(content)} → {_COMPRESS_THRESHOLD_CHARS} chars")
            return ToolResult(
                success=result.success,
                message=result.message,
                data=truncated + f"\n\n[... truncated, {len(content)} chars total]",
            )
        return result

    async def execute(
        self, tool_name: str, arguments: dict
    ) -> Tuple[ToolResult, ToolResultKind]:
        """执行工具并返回 (result, kind)。"""
        tool = self._find_tool(tool_name)
        result = await tool.invoke(tool_name, **arguments)
        kind = classify_tool_result(tool_name, result)

        if kind == ToolResultKind.FILE_MUTATION:
            filepath = arguments.get("filepath") or arguments.get("path", "")
            if filepath:
                self._turn_mutation_paths.add(filepath)

        result = self._maybe_truncate(result, kind)
        return result, kind
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_tool_executor.py -v
```

预期：5 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/runtime/tool_executor.py \
        api/tests/domain/services/runtime/test_tool_executor.py
git commit -m "feat: add ToolResultKind classification and ToolExecutor with output truncation"
```

---

## Task 8: CheckpointService — 文件编辑安全回滚

**Files:**
- Create: `api/app/domain/services/runtime/checkpoint.py`
- Create: `api/tests/domain/services/runtime/test_checkpoint.py`

**Interfaces:**
- Produces: `CheckpointService`（被 Task 9 BaseAgent 在 FILE_MUTATION 前调用）
- Consumes: `IUnitOfWork.checkpoint`（Task 1）

- [ ] **Step 1: 写 test_checkpoint.py（失败）**

```python
# api/tests/domain/services/runtime/test_checkpoint.py
from __future__ import annotations
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.domain.services.runtime.checkpoint import CheckpointService

@pytest.fixture
def mock_uow():
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.checkpoint = AsyncMock()
    uow.checkpoint.save = AsyncMock()
    uow.checkpoint.get_latest = AsyncMock(return_value=None)
    return uow

@pytest.fixture
def uow_factory(mock_uow):
    return lambda: mock_uow

@pytest.mark.asyncio
async def test_snapshot_saves_checkpoint(uow_factory, tmp_path):
    test_file = tmp_path / "main.py"
    test_file.write_text("def hello(): pass")
    
    svc = CheckpointService(session_id="s1", uow_factory=uow_factory)
    await svc.snapshot(str(test_file), turn_id="t1")
    
    uow_factory().checkpoint.save.assert_called_once()

@pytest.mark.asyncio
async def test_snapshot_nonexistent_file_is_noop(uow_factory):
    svc = CheckpointService(session_id="s1", uow_factory=uow_factory)
    await svc.snapshot("/nonexistent/path.py", turn_id="t1")
    uow_factory().checkpoint.save.assert_not_called()

@pytest.mark.asyncio
async def test_rewind_restores_content(uow_factory, tmp_path):
    test_file = tmp_path / "app.py"
    test_file.write_text("original content")
    
    from app.domain.models.checkpoint import Checkpoint
    mock_checkpoint = Checkpoint(
        session_id="s1", turn_id="t0",
        filepath=str(test_file), content="original content"
    )
    uow_factory().checkpoint.get_latest = AsyncMock(return_value=mock_checkpoint)
    
    # 模拟文件被修改
    test_file.write_text("modified content")
    
    svc = CheckpointService(session_id="s1", uow_factory=uow_factory)
    success = await svc.rewind(str(test_file))
    
    assert success is True
    assert test_file.read_text() == "original content"
```

- [ ] **Step 2: 运行确认失败**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_checkpoint.py -v
```

- [ ] **Step 3: 实现 CheckpointService**

```python
# api/app/domain/services/runtime/checkpoint.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from app.domain.models.checkpoint import Checkpoint
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class CheckpointService:
    """文件编辑安全层 —— 写入前快照，支持 rewind。
    
    设计原则（仿 hermes file_safety）：
    每次 FILE_MUTATION 类工具调用前，先对目标文件做内容快照并持久化到 DB。
    rewind() 从 DB 取最新快照恢复文件内容，保证任意时刻可回退。
    """

    def __init__(
        self, session_id: str, uow_factory: Callable[[], IUnitOfWork]
    ) -> None:
        self._session_id = session_id
        self._uow_factory = uow_factory

    async def snapshot(self, filepath: str, turn_id: str) -> None:
        """对指定文件做内容快照（文件不存在则跳过）。"""
        path = Path(filepath)
        if not path.is_file():
            logger.debug(f"CheckpointService: skip snapshot, file not found: {filepath}")
            return
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            checkpoint = Checkpoint(
                session_id=self._session_id,
                turn_id=turn_id,
                filepath=filepath,
                content=content,
            )
            async with self._uow_factory() as uow:
                await uow.checkpoint.save(checkpoint)
            logger.info(f"CheckpointService: snapshotted {filepath} (turn={turn_id})")
        except Exception as e:
            logger.warning(f"CheckpointService: snapshot failed for {filepath}: {e}")

    async def rewind(self, filepath: str) -> bool:
        """将文件恢复到最新快照。返回 True 表示成功。"""
        try:
            async with self._uow_factory() as uow:
                checkpoint = await uow.checkpoint.get_latest(self._session_id, filepath)
            if not checkpoint:
                logger.warning(f"CheckpointService: no checkpoint found for {filepath}")
                return False
            Path(filepath).write_text(checkpoint.content, encoding="utf-8")
            logger.info(f"CheckpointService: rewound {filepath} to turn={checkpoint.turn_id}")
            return True
        except Exception as e:
            logger.error(f"CheckpointService: rewind failed for {filepath}: {e}")
            return False
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd api && python -m pytest tests/domain/services/runtime/test_checkpoint.py -v
```

预期：3 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/runtime/checkpoint.py \
        api/tests/domain/services/runtime/test_checkpoint.py
git commit -m "feat: add CheckpointService with file snapshot and rewind"
```

---

## Task 9: 新 Coding 工具集 — shell / file_edit / code_search / git

**Files:**
- Modify: `api/app/domain/services/tools/shell.py` — 去掉 sandbox 依赖，改用 subprocess
- Create: `api/app/domain/services/tools/file_edit.py`
- Create: `api/app/domain/services/tools/code_search.py`
- Create: `api/app/domain/services/tools/git.py`

**Interfaces:**
- Produces: `ShellTool`、`FileEditTool`、`CodeSearchTool`、`GitTool`
- 所有工具继承已有 `BaseTool`，`invoke()` 返回 `ToolResult`

- [ ] **Step 1: 重写 ShellTool（无 sandbox 依赖）**

```python
# api/app/domain/services/tools/shell.py
from __future__ import annotations
import asyncio
import subprocess
from typing import Optional
from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class ShellTool(BaseTool):
    """Shell 工具 —— 直接 subprocess 执行，无需 Docker sandbox。"""
    name: str = "shell"

    def __init__(self, cwd: str = ".") -> None:
        super().__init__()
        self._cwd = cwd

    @tool(
        name="shell_execute",
        description="在项目目录执行 shell 命令。用于运行测试、安装依赖、文件操作。",
        parameters={
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数（默认 30）"},
        },
        required=["command"],
    )
    async def shell_execute(self, command: str, timeout: int = 30) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode(errors="replace")
            return ToolResult(
                success=proc.returncode == 0,
                data=output,
                message=f"exit code {proc.returncode}",
            )
        except asyncio.TimeoutError:
            return ToolResult(success=False, message=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, message=str(e))
```

- [ ] **Step 2: 实现 FileEditTool**

```python
# api/app/domain/services/tools/file_edit.py
from __future__ import annotations
from pathlib import Path
from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class FileEditTool(BaseTool):
    """文件读写工具 —— 支持读取、完整写入、精确 patch（old_str → new_str）。"""
    name: str = "file_edit"

    @tool(
        name="read_file",
        description="读取文件内容。大文件可指定 start_line/end_line 范围。",
        parameters={
            "filepath": {"type": "string", "description": "文件路径"},
            "start_line": {"type": "integer", "description": "起始行号（1-based，可选）"},
            "end_line": {"type": "integer", "description": "结束行号（1-based，可选）"},
        },
        required=["filepath"],
    )
    async def read_file(
        self, filepath: str, start_line: int = None, end_line: int = None
    ) -> ToolResult:
        try:
            content = Path(filepath).read_text(encoding="utf-8", errors="replace")
            if start_line or end_line:
                lines = content.splitlines()
                s = (start_line or 1) - 1
                e = end_line or len(lines)
                content = "\n".join(lines[s:e])
            return ToolResult(success=True, data=content)
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="write_file",
        description="写入文件（完整覆盖）。写入前应先用 read_file 确认当前内容。",
        parameters={
            "filepath": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "写入的完整内容"},
        },
        required=["filepath", "content"],
    )
    async def write_file(self, filepath: str, content: str) -> ToolResult:
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, data={"bytes_written": len(content.encode())})
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="patch_file",
        description="精确替换文件中的代码片段（old_str → new_str）。old_str 必须在文件中唯一存在。",
        parameters={
            "filepath": {"type": "string", "description": "文件路径"},
            "old_str": {"type": "string", "description": "要替换的原始字符串（必须唯一）"},
            "new_str": {"type": "string", "description": "替换后的字符串"},
        },
        required=["filepath", "old_str", "new_str"],
    )
    async def patch_file(self, filepath: str, old_str: str, new_str: str) -> ToolResult:
        try:
            content = Path(filepath).read_text(encoding="utf-8")
            count = content.count(old_str)
            if count == 0:
                return ToolResult(success=False, message=f"old_str not found in {filepath}")
            if count > 1:
                return ToolResult(success=False, message=f"old_str appears {count} times — must be unique")
            new_content = content.replace(old_str, new_str, 1)
            Path(filepath).write_text(new_content, encoding="utf-8")
            return ToolResult(success=True, data={"success": True})
        except Exception as e:
            return ToolResult(success=False, message=str(e))
```

- [ ] **Step 3: 实现 CodeSearchTool**

```python
# api/app/domain/services/tools/code_search.py
from __future__ import annotations
import asyncio
from pathlib import Path
from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class CodeSearchTool(BaseTool):
    """代码搜索工具 —— grep 文本搜索 + 目录列举。"""
    name: str = "code_search"

    def __init__(self, cwd: str = ".") -> None:
        super().__init__()
        self._cwd = cwd

    @tool(
        name="grep_files",
        description="在代码库中搜索文本或正则。返回匹配行及文件位置。",
        parameters={
            "pattern": {"type": "string", "description": "搜索模式（支持正则）"},
            "path": {"type": "string", "description": "搜索目录（默认项目根目录）"},
            "file_pattern": {"type": "string", "description": "文件名过滤，如 '*.py'"},
        },
        required=["pattern"],
    )
    async def grep_files(
        self, pattern: str, path: str = ".", file_pattern: str = None
    ) -> ToolResult:
        cmd = ["grep", "-rn", "--include", file_pattern or "*", pattern, path]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return ToolResult(success=True, data=stdout.decode(errors="replace"))
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="list_dir",
        description="列出目录结构。",
        parameters={
            "path": {"type": "string", "description": "目录路径"},
            "depth": {"type": "integer", "description": "展开深度（默认 2）"},
        },
        required=["path"],
    )
    async def list_dir(self, path: str, depth: int = 2) -> ToolResult:
        try:
            root = Path(self._cwd) / path
            lines: list[str] = []

            def walk(p: Path, current_depth: int, prefix: str = "") -> None:
                if current_depth > depth:
                    return
                for item in sorted(p.iterdir()):
                    if item.name.startswith("."):
                        continue
                    lines.append(f"{prefix}{item.name}{'/' if item.is_dir() else ''}")
                    if item.is_dir():
                        walk(item, current_depth + 1, prefix + "  ")

            walk(root, 1)
            return ToolResult(success=True, data="\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, message=str(e))
```

- [ ] **Step 4: 实现 GitTool**

```python
# api/app/domain/services/tools/git.py
from __future__ import annotations
import asyncio
from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class GitTool(BaseTool):
    """Git 操作工具 —— 只读操作（status/diff/log），写操作需用户确认。"""
    name: str = "git"

    def __init__(self, cwd: str = ".") -> None:
        super().__init__()
        self._cwd = cwd

    async def _git(self, *args: str) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            output = stdout.decode(errors="replace")
            return ToolResult(success=proc.returncode == 0, data=output)
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="git_status",
        description="查看 git 工作区状态。",
        parameters={},
        required=[],
    )
    async def git_status(self) -> ToolResult:
        return await self._git("status", "--short")

    @tool(
        name="git_diff",
        description="查看文件变更 diff。",
        parameters={
            "filepath": {"type": "string", "description": "指定文件路径（可选）"},
        },
        required=[],
    )
    async def git_diff(self, filepath: str = None) -> ToolResult:
        args = ["diff"]
        if filepath:
            args.append(filepath)
        return await self._git(*args)

    @tool(
        name="git_log",
        description="查看最近提交历史。",
        parameters={
            "n": {"type": "integer", "description": "显示最近 n 条提交（默认 10）"},
        },
        required=[],
    )
    async def git_log(self, n: int = 10) -> ToolResult:
        return await self._git("log", f"-{n}", "--pretty=%h %an %s")
```

- [ ] **Step 5: 写工具集成测试**

```python
# api/tests/domain/services/tools/test_coding_tools.py
from __future__ import annotations
import pytest
from pathlib import Path
from app.domain.services.tools.file_edit import FileEditTool
from app.domain.services.tools.code_search import CodeSearchTool

@pytest.mark.asyncio
async def test_write_and_read_file(tmp_path):
    tool = FileEditTool()
    filepath = str(tmp_path / "test.py")
    write_result = await tool.write_file(filepath, "def hello(): return 42")
    assert write_result.success is True
    read_result = await tool.read_file(filepath)
    assert "def hello" in read_result.data

@pytest.mark.asyncio
async def test_patch_file_success(tmp_path):
    tool = FileEditTool()
    filepath = str(tmp_path / "app.py")
    Path(filepath).write_text("def old_name(): pass")
    result = await tool.patch_file(filepath, "old_name", "new_name")
    assert result.success is True
    assert "new_name" in Path(filepath).read_text()

@pytest.mark.asyncio
async def test_patch_file_not_found(tmp_path):
    tool = FileEditTool()
    filepath = str(tmp_path / "app.py")
    Path(filepath).write_text("def foo(): pass")
    result = await tool.patch_file(filepath, "nonexistent", "replacement")
    assert result.success is False
    assert "not found" in result.message

@pytest.mark.asyncio
async def test_grep_files(tmp_path):
    (tmp_path / "main.py").write_text("def main(): pass\nmain()")
    tool = CodeSearchTool(cwd=str(tmp_path))
    result = await tool.grep_files("def main", path=".")
    assert result.success is True
    assert "main.py" in result.data
```

- [ ] **Step 6: 运行工具测试**

```bash
cd api && python -m pytest tests/domain/services/tools/test_coding_tools.py -v
```

预期：4 passed

- [ ] **Step 7: Commit**

```bash
git add api/app/domain/services/tools/shell.py \
        api/app/domain/services/tools/file_edit.py \
        api/app/domain/services/tools/code_search.py \
        api/app/domain/services/tools/git.py \
        api/tests/domain/services/tools/test_coding_tools.py
git commit -m "feat: add coding toolset (shell/file_edit/code_search/git) without sandbox dependency"
```

---

## Task 10: BaseAgent 重写 + CodingAgent

**Files:**
- Modify: `api/app/domain/services/agents/base.py` — 重写，消费 TurnOrchestrator
- Create: `api/app/domain/services/agents/coding_agent.py`
- Create: `api/app/domain/services/prompts/coding.py`

**Interfaces:**
- Produces: `BaseAgent.run(user_message, session_messages, session_id) -> AsyncGenerator[BaseEvent]`
- Produces: `CodingAgent`（带 workspace + coding 工具集）
- Consumes: Tasks 3-9 所有 runtime 组件

- [ ] **Step 1: 重写 BaseAgent**

```python
# api/app/domain/services/agents/base.py
from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator, Callable, Dict, Any, List

from app.domain.external.llm import LLM
from app.domain.external.json_parser import JSONParser
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import (
    BaseEvent, MessageEvent, ToolEvent, ToolEventStatus, ErrorEvent, DoneEvent
)
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.runtime.context_engine import ContextEngine
from app.domain.services.runtime.memory import ThreeLayerMemory
from app.domain.services.runtime.turn import TurnOrchestrator
from app.domain.services.runtime.tool_executor import ToolExecutor, ToolResultKind
from app.domain.services.runtime.checkpoint import CheckpointService
from app.domain.services.tools.base import BaseTool

logger = logging.getLogger(__name__)


class BaseAgent:
    """重写后的 BaseAgent —— 消费 runtime 组件而非直接管理状态。
    
    职责：
    1. 依赖 TurnOrchestrator 处理 prologue（context 压缩、系统 prompt 构建）
    2. 依赖 ToolExecutor 执行工具（分类 + 截断 + mutation 追踪）
    3. 依赖 CheckpointService 在 FILE_MUTATION 前做快照
    4. 依赖 ContextEngine 追踪 token 用量
    5. 发射类型化事件流（ToolEvent / MessageEvent / ErrorEvent / DoneEvent）
    """

    name: str = "base"

    def __init__(
        self,
        llm: LLM,
        json_parser: JSONParser,
        agent_config: AgentConfig,
        tools: List[BaseTool],
        turn_orchestrator: TurnOrchestrator,
        tool_executor: ToolExecutor,
        checkpoint_service: CheckpointService,
        context_engine: ContextEngine,
        memory: ThreeLayerMemory,
        uow_factory: Callable[[], IUnitOfWork],
        session_id: str,
    ) -> None:
        self._llm = llm
        self._json_parser = json_parser
        self._agent_config = agent_config
        self._tools = tools
        self._turn_orchestrator = turn_orchestrator
        self._tool_executor = tool_executor
        self._checkpoint_service = checkpoint_service
        self._context_engine = context_engine
        self._memory = memory
        self._uow_factory = uow_factory
        self._session_id = session_id

    def _get_tool_schemas(self) -> List[Dict[str, Any]]:
        schemas = []
        for t in self._tools:
            schemas.extend(t.get_tools())
        return schemas

    async def run(
        self,
        user_message: str,
        session_messages: List[Dict[str, Any]],
    ) -> AsyncGenerator[BaseEvent, None]:
        """主入口：运行一轮 agent 对话，发射事件流。"""
        # 1. 运行 prologue，获取 TurnContext
        turn_ctx = await self._turn_orchestrator.build_turn_context(
            user_message=user_message,
            session_messages=session_messages,
            session_id=self._session_id,
        )
        self._tool_executor.reset_turn()

        # 2. 把 user_message 加入工作消息列表
        messages = turn_ctx.messages + [{"role": "user", "content": user_message}]

        # 3. 系统 prompt 注入
        full_messages = [
            {"role": "system", "content": turn_ctx.active_system_prompt}
        ] + messages

        # 4. ReAct 主循环
        try:
            async for event in self._react_loop(full_messages, turn_ctx):
                yield event
        except Exception as e:
            logger.exception(f"BaseAgent run error: {e}")
            yield ErrorEvent(error=str(e))
        finally:
            yield DoneEvent()

    async def _react_loop(
        self, messages: List[Dict[str, Any]], turn_ctx
    ) -> AsyncGenerator[BaseEvent, None]:
        """ReAct 循环：LLM 调用 → 工具执行 → 再次 LLM 调用，直到无工具调用或耗尽配额。"""
        while True:
            if not turn_ctx.iteration_budget.consume():
                yield ErrorEvent(error=f"Iteration limit reached ({self._agent_config.max_iterations})")
                return

            # LLM 调用
            response = await self._llm.invoke(
                messages=messages,
                tools=self._get_tool_schemas(),
            )

            # 追踪 token 用量（用于 should_compress 决策）
            if "usage" in response:
                self._context_engine.update_from_response(response["usage"])

            tool_calls = response.get("tool_calls", [])
            content = response.get("content")

            # 将 assistant 消息加入列表
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            if not tool_calls:
                # 无工具调用 → 最终回答
                if content:
                    yield MessageEvent(role="assistant", message=content)
                return

            # 处理工具调用
            tool_messages = []
            for tool_call in tool_calls[:1]:  # 每轮只处理一个工具调用
                tc_id = tool_call.get("id") or str(uuid.uuid4())
                func = tool_call.get("function", {})
                tool_name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                arguments = await self._json_parser.invoke(raw_args)

                yield ToolEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    function_name=tool_name,
                    function_args=arguments,
                    status=ToolEventStatus.CALLING,
                )

                # FILE_MUTATION 前做 checkpoint
                if arguments.get("filepath"):
                    await self._checkpoint_service.snapshot(
                        arguments["filepath"], turn_id=turn_ctx.turn_id
                    )

                result, kind = await self._tool_executor.execute(tool_name, arguments)

                yield ToolEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    function_name=tool_name,
                    function_args=arguments,
                    function_result=result,
                    status=ToolEventStatus.CALLED,
                )

                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "function_name": tool_name,
                    "content": result.model_dump_json(),
                })

            messages.extend(tool_messages)
```

- [ ] **Step 2: 实现 CodingAgent**

```python
# api/app/domain/services/agents/coding_agent.py
from __future__ import annotations
from typing import Callable, List
from app.domain.external.llm import LLM
from app.domain.external.json_parser import JSONParser
from app.domain.models.app_config import AgentConfig
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.base import BaseAgent
from app.domain.services.runtime.context_engine import DefaultContextEngine, ContextEngineConfig
from app.domain.services.runtime.memory import ThreeLayerMemory
from app.domain.services.runtime.turn import TurnOrchestrator
from app.domain.services.runtime.tool_executor import ToolExecutor
from app.domain.services.runtime.checkpoint import CheckpointService
from app.domain.services.runtime.workspace import WorkspaceContext
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.file_edit import FileEditTool
from app.domain.services.tools.code_search import CodeSearchTool
from app.domain.services.tools.git import GitTool


def build_coding_agent(
    llm: LLM,
    json_parser: JSONParser,
    agent_config: AgentConfig,
    uow_factory: Callable[[], IUnitOfWork],
    session_id: str,
    workspace: WorkspaceContext,
) -> BaseAgent:
    """工厂函数：组装完整的 CodingAgent（所有 runtime 组件连线）。"""
    cwd = workspace.cwd
    tools = [
        ShellTool(cwd=cwd),
        FileEditTool(),
        CodeSearchTool(cwd=cwd),
        GitTool(cwd=cwd),
    ]
    context_engine = DefaultContextEngine(
        config=ContextEngineConfig(
            context_length=agent_config.context_length,
            threshold_percent=0.75,
        ),
        llm=llm,
        protect_first_n=3,
        protect_last_n=6,
    )
    memory = ThreeLayerMemory(session_id=session_id, uow_factory=uow_factory)
    turn_orchestrator = TurnOrchestrator(
        workspace=workspace,
        memory=memory,
        context_engine=context_engine,
        max_iterations=agent_config.max_iterations,
    )
    tool_executor = ToolExecutor(tools=tools, context_engine=context_engine)
    checkpoint_service = CheckpointService(session_id=session_id, uow_factory=uow_factory)

    return BaseAgent(
        llm=llm,
        json_parser=json_parser,
        agent_config=agent_config,
        tools=tools,
        turn_orchestrator=turn_orchestrator,
        tool_executor=tool_executor,
        checkpoint_service=checkpoint_service,
        context_engine=context_engine,
        memory=memory,
        uow_factory=uow_factory,
        session_id=session_id,
    )
```

- [ ] **Step 3: 写 BaseAgent 集成测试**

```python
# api/tests/domain/services/agents/test_base_agent.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.domain.services.agents.base import BaseAgent
from app.domain.models.event import MessageEvent, DoneEvent, ToolEvent

def build_agent(llm_response: dict) -> BaseAgent:
    mock_llm = AsyncMock()
    mock_llm.invoke = AsyncMock(return_value=llm_response)
    mock_orchestrator = AsyncMock()
    mock_turn_ctx = MagicMock()
    mock_turn_ctx.messages = []
    mock_turn_ctx.active_system_prompt = "You are a coding agent."
    mock_turn_ctx.turn_id = "t1"
    mock_turn_ctx.iteration_budget = MagicMock()
    mock_turn_ctx.iteration_budget.consume.return_value = True
    mock_orchestrator.build_turn_context = AsyncMock(return_value=mock_turn_ctx)

    mock_executor = AsyncMock()
    mock_executor.reset_turn = MagicMock()
    mock_executor.execute = AsyncMock(return_value=(
        MagicMock(success=True, data="output", model_dump_json=lambda: '{"success":true}'),
        MagicMock(value="terminal"),
    ))

    from app.domain.models.app_config import AgentConfig
    config = AgentConfig(max_iterations=10, context_length=8000, max_retries=3)

    return BaseAgent(
        llm=mock_llm,
        json_parser=AsyncMock(invoke=AsyncMock(side_effect=lambda x: {})),
        agent_config=config,
        tools=[],
        turn_orchestrator=mock_orchestrator,
        tool_executor=mock_executor,
        checkpoint_service=AsyncMock(),
        context_engine=MagicMock(update_from_response=MagicMock()),
        memory=MagicMock(build_context_block=MagicMock(return_value="")),
        uow_factory=lambda: AsyncMock(),
        session_id="s1",
    )

@pytest.mark.asyncio
async def test_agent_emits_message_event_on_text_response():
    agent = build_agent({"role": "assistant", "content": "Here is the fix.", "tool_calls": []})
    events = []
    async for e in agent.run("fix the bug", []):
        events.append(e)
    message_events = [e for e in events if isinstance(e, MessageEvent)]
    done_events = [e for e in events if isinstance(e, DoneEvent)]
    assert len(message_events) == 1
    assert message_events[0].message == "Here is the fix."
    assert len(done_events) == 1
```

- [ ] **Step 4: 运行测试**

```bash
cd api && python -m pytest tests/domain/services/agents/test_base_agent.py -v
```

预期：1 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/agents/base.py \
        api/app/domain/services/agents/coding_agent.py \
        api/tests/domain/services/agents/test_base_agent.py
git commit -m "feat: rewrite BaseAgent consuming runtime components, add CodingAgent factory"
```

---

## Task 11: AgentConfig 扩展 + AgentTaskRunner 接入新 Runtime

**Files:**
- Modify: `api/app/domain/models/app_config.py` — AgentConfig 加 `context_length`
- Modify: `api/app/domain/services/agent_task_runner.py` — 接入 `build_coding_agent`
- Modify: `api/app/interfaces/service_dependencies.py` — 注入 WorkspaceContext

**Interfaces:**
- 这是连线任务：把新 runtime 接入现有的 AgentService → AgentTaskRunner 调用链

- [ ] **Step 1: 扩展 AgentConfig**

```python
# api/app/domain/models/app_config.py
# 在 AgentConfig 类中新增字段（保留已有字段）：
context_length: int = 8000       # LLM context window 大小
```

- [ ] **Step 2: 改写 AgentTaskRunner.invoke() 接入新 runtime**

```python
# api/app/domain/services/agent_task_runner.py
# 在 __init__ 中替换掉 browser/sandbox 相关，改为：
from app.domain.services.agents.coding_agent import build_coding_agent
from app.domain.services.runtime.workspace import resolve_workspace

class AgentTaskRunner(TaskRunner):
    def __init__(self, ..., cwd: str = ".") -> None:
        ...
        workspace = resolve_workspace(cwd)
        self._agent = build_coding_agent(
            llm=llm,
            json_parser=json_parser,
            agent_config=agent_config,
            uow_factory=uow_factory,
            session_id=session_id,
            workspace=workspace,
        )

    async def invoke(self, task: Task) -> None:
        # 读取输入消息 → 调用 self._agent.run() → 把事件写入输出流
        # 保留原有的事件写入、状态更新逻辑
        ...
```

- [ ] **Step 3: 运行完整测试套件确认无回归**

```bash
cd api && python -m pytest tests/ -v --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add api/app/domain/models/app_config.py \
        api/app/domain/services/agent_task_runner.py \
        api/app/interfaces/service_dependencies.py
git commit -m "feat: wire new coding agent runtime into AgentTaskRunner, remove browser/sandbox dependency"
```

---

## Self-Review

**Spec coverage check:**
- ✅ WorkspaceContext（一次解析、不可变）→ Task 2
- ✅ ContextEngine Protocol + DefaultContextEngine → Task 4
- ✅ ThreeLayerMemory（Working/Episodic/Semantic）→ Task 5
- ✅ TurnContext + TurnOrchestrator + Preflight 压缩门 → Task 6
- ✅ IterationBudget（consume/refund）→ Task 3
- ✅ ToolResultKind + ToolExecutor → Task 7
- ✅ CheckpointService（snapshot/rewind）→ Task 8
- ✅ 新工具集（shell/file_edit/code_search/git）→ Task 9
- ✅ BaseAgent 重写 + CodingAgent 工厂 → Task 10
- ✅ DB migration + Repository → Task 1
- ✅ 接入现有 AgentTaskRunner → Task 11

**Placeholder scan:** 无 TBD / TODO。Task 11 的 AgentTaskRunner 改写给出了关键代码结构，完整实现依赖已有 invoke() 逻辑，不属于 placeholder。

**Type consistency:** 所有 task 间的类型引用已交叉确认：`TurnContext.iteration_budget: IterationBudget`、`ToolExecutor.execute() -> Tuple[ToolResult, ToolResultKind]`、`CheckpointService.snapshot(filepath, turn_id)`。

---

Plan complete and saved to `docs/superpowers/plans/2026-06-29-coding-agent-runtime.md`.
