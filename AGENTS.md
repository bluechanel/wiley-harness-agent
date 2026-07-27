# Project Guidelines

## Workspace layout

- 仓库是 uv workspace：根 `pyproject.toml` 是虚拟 workspace root（只声明 `[tool.uv.workspace]`、dev 依赖组与 pytest 配置，本身不是包），packages 分 core 与 app 两部分，各自有 `pyproject.toml`/`src`/`tests`：
  - `packages/wy-core/`（发行名 `wy-core`，import 名 `wy_core`）：零依赖（纯标准库）的极简 agent core runtime 库——统一消息定义（`message`）、`Tool`/`Model` 抽象父类、内存态 `Session` 与自动上下文压缩（`session`）、默认开启的 JSONL 审计日志（`log`）、`Agent` 循环（`agent`）。使用方继承 `Model` 适配任意 LLM API、继承 `Tool` 加工具即得完整 harness agent。对外 API 统一从 `wy_core/__init__.py` 导出，禁止新增运行时依赖、禁止 import 本仓其他包；可独立构建发布（`uv build --package wy-core`）。
  - `packages/wy-coding-agent/`（发行名 `wy-coding-agent`，import 名 `wy_coding_agent`）：基于 wy-core 的编码 agent 应用（由原 wiley-agent 与 wiley-tui 合并而来）——`anthropic`（`AnthropicModel` 模型实现）、`tools/`（bash/grep/read/edit/write 内置工具与 `FunctionTool` 适配器）、`mcp`、`config`、`prompt_template`、`session`（`SessionStore` 持久化）、`conversation`（编排）、`factory`（`bootstrap` + `create_agent`）、`tui/`（Textual 前端）与 `main`（console script `wy-coding-agent`；根目录 `main.py` 是它的薄启动壳）。运行依赖 `wy-core`、`anthropic`（官方 SDK）、`mcp`、`ripgrep`（PyPI 二进制 wheel，供 grep 工具调用）、`textual`。
- 依赖方向唯一：`wy_coding_agent → wy_core`。核心运行时契约（Agent/Model/Tool/Session/AuditLog、消息与事件类型）一律定义在 `wy_core`，应用层与 TUI 直接消费；`wy_core` 永远不 import 应用包、不引入 UI 依赖。
- 包内模块一律以 `from <包>.<sub> import ...` 子模块路径互引，**禁止 import 本包包根**（包根 `__init__` 导入各子模块，反向引用会循环导入）。`wy_coding_agent.tui` 不经包根导出，由 `main` 组装。
- 库代码不得用 `__file__` 反推仓库根或假设自己活在本仓库里：默认路径一律按调用方 CWD 解析（会话默认 `CWD/.agent_session/`，审计日志随会话文件同目录，`bootstrap` 默认读 `CWD/config.toml`，`wy_core.AuditLog.default()` 写 `CWD/.wy_audit/`），且必须提供显式参数覆盖。`create_agent` 不隐式读任何配置文件；显式读配置的组装入口只有 `bootstrap(config_path=...)`。装进 site-packages 后行为必须与源码运行一致。
- 常用命令：`uv sync` 安装两个成员与 dev 组；`uv run pytest` 从根跑全部测试（testpaths 配在根 pyproject，测试分属 `packages/*/tests/`；两包测试目录不建 `__init__.py`，测试文件与辅助模块名跨包必须唯一，如 `helpers.py` 对 `app_helpers.py`）；`uv run wy-coding-agent`（或 `uv run python main.py`）启动 TUI；`uv build --package wy-core` / `uv build --package wy-coding-agent` 单独打包。
- 新增 harness/runtime 能力放入 `packages/wy-core/`（保持零依赖与极简，宁缺毋滥）；新增模型实现、本地工具、MCP、配置解析、持久化与展示逻辑放入 `packages/wy-coding-agent/`。两包的公开导出是对外契约，增删导出或改签名时注意兼容性。

## Module guidelines

- 各包的详细模块约定按目录就近存放，改哪个包的代码就先读哪份（Claude Code 在读写对应目录文件时会自动加载它们，故不在此处全量引入）：
  - `packages/wy-core/AGENTS.md`：统一消息词汇、Model/Tool 契约语义、Session 压缩算法、AuditLog 格式、Agent 循环与异常回滚。
  - `packages/wy-coding-agent/AGENTS.md`：factory 组装、AnthropicModel、工具体系（bash/grep/read/edit/write）、MCP、持久会话、TUI 展示约定。

## Code organization

- 编写或修改项目代码时，必须考虑模块化设计。
- 应按照功能和职责拆分代码，将不同功能集合放在不同的文件或模块中。
- 避免把配置读取、模型调用、界面展示、状态管理等不同职责集中在同一个文件中。
- 新增功能前，应先判断它属于现有模块还是需要建立新的独立模块，并保持模块边界清晰。
- 入口文件只负责程序启动和模块组装，不应包含大量具体业务实现。

## Task completion

- 每次任务完成后，必须评估是否需要更新 AGENTS.md，并写到正确的层级：全局约定、workspace 结构、跨包规则更新根 `AGENTS.md`；包内模块约定更新对应的 `packages/*/AGENTS.md`。
- 如果任务引入了新的执行约定、项目结构变化、工具使用方式或后续任务需要了解的注意事项，应及时补充，以便指导后续任务的执行。
