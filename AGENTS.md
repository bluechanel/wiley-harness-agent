# Project Guidelines

## Workspace layout

- 仓库是 uv workspace：根 `pyproject.toml` 是虚拟 workspace root（只声明 `[tool.uv.workspace]`、dev 依赖组与 pytest 配置，本身不是包），packages 只分 agent 与 tui 两部分，各自有 `pyproject.toml`/`src`/`tests`：
  - `packages/wiley-agent/`（发行名 `wiley-agent`，import 名 `wiley_agent`）：UI 无关的 agent 应用核心——`config`、`usage`、`provider/`、`service`（`AgentService` agent 循环）、`prompt_template`、`conversation`、`session` 持久化、`tools/`、`mcp`、`debug`、`factory`（`bootstrap` + `create_agent`）。运行依赖为 `mcp`、`aiohttp` 与 `ripgrep`（PyPI 二进制 wheel，供 grep 工具调用），禁止导入 `textual` 或 UI 包、禁止新增 UI 相关依赖。对外 API 统一从 `wiley_agent/__init__.py` 导出，可独立构建发布（`uv build --package wiley-agent`）。
  - `packages/wiley-tui/`（import 名 `wiley_tui`）：纯展示的 Textual TUI 前端 + 薄启动入口——`render.py` 把 agent 层记录/事件解析为展示用 Markdown（纯函数），`app.py` 只负责界面组件与交互（`ChatApp`/`ChatBackend`），`main.py` 是唯一入口：argparse → `wiley_agent.bootstrap(session_id)` → 启动 TUI → finally `close()`（console script `wiley-tui`；根目录 `main.py` 是它的薄启动壳）。TUI 只消费流事件与 `SessionRecord` 等公开数据类型，不含业务逻辑。
- 依赖方向唯一：`wiley_tui → wiley_agent`。两侧共享的数据类型一律定义在 `wiley_agent` 中，由 TUI 消费；agent 侧永远不 import UI。
- `wiley_agent` 包内模块一律以 `from wiley_agent.<sub>.<mod> import ...` 子模块路径互引，**禁止 import 包根 `wiley_agent`**（包根 `__init__` 导入各子包，反向引用会循环导入）。
- 库代码不得用 `__file__` 反推仓库根或假设自己活在本仓库里：默认路径一律按调用方 CWD 解析（会话默认 `CWD/.agent_session/`，`bootstrap` 默认读 `CWD/config.toml`），且必须提供显式参数覆盖。`create_agent` 不隐式读任何配置文件；显式读配置的组装入口只有 `bootstrap(config_path=...)`。装进 site-packages 后行为必须与源码运行一致。
- 常用命令：`uv sync` 安装两个成员与 dev 组；`uv run pytest` 从根跑全部测试（testpaths 配在根 pyproject，测试分属 `packages/*/tests/`）；`uv run wiley-tui`（或 `uv run python main.py`）启动 TUI；`uv build --package wiley-agent` 单独打库包。
- 新增 harness 能力、本地工具、provider、配置解析都放入 `packages/wiley-agent/`，新增展示逻辑放入 `packages/wiley-tui/`。`wiley_agent` 的公开导出是对外契约，增删导出或改签名时注意兼容性。

## Module guidelines

- 各包的详细模块约定按目录就近存放，改哪个包的代码就先读哪份（Claude Code 在读写对应目录文件时会自动加载它们，故不在此处全量引入）：
  - `packages/wiley-agent/AGENTS.md`：factory 组装、工具体系（bash/grep/read/edit/write）、`AgentService` 流事件、MCP、debug、system prompt 组装、LLM provider 契约与实现。
  - `packages/wiley-tui/AGENTS.md`：TUI 展示约定（工具块渲染、收起/展开、用量条）。

## Code organization

- 编写或修改项目代码时，必须考虑模块化设计。
- 应按照功能和职责拆分代码，将不同功能集合放在不同的文件或模块中。
- 避免把配置读取、模型调用、界面展示、状态管理等不同职责集中在同一个文件中。
- 新增功能前，应先判断它属于现有模块还是需要建立新的独立模块，并保持模块边界清晰。
- 入口文件只负责程序启动和模块组装，不应包含大量具体业务实现。

## Task completion

- 每次任务完成后，必须评估是否需要更新 AGENTS.md，并写到正确的层级：全局约定、workspace 结构、跨包规则更新根 `AGENTS.md`；包内模块约定更新对应的 `packages/*/AGENTS.md`。
- 如果任务引入了新的执行约定、项目结构变化、工具使用方式或后续任务需要了解的注意事项，应及时补充，以便指导后续任务的执行。
