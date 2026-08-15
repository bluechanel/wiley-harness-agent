# Project Guidelines

## Workspace layout

- 仓库是 uv workspace：根 `pyproject.toml` 是虚拟 workspace root（只声明 `[tool.uv.workspace]`、dev 依赖组与 pytest 配置，本身不是包），packages 分 core 与 app 两部分，各自有 `pyproject.toml`/`src`/`tests`：
  - `packages/wy-core/`（发行名 `wy-core`，import 名 `wy_core`）：零依赖（纯标准库）的极简 agent core runtime 库——统一消息定义（`message`）、`Tool`/`Model` 抽象父类与工具执行事件（`tool`/`model`）、直接加载/懒加载工具集（`toolset`：`Tool.deferred` 与 `ToolSet`，Agent 每轮只发 available）、内存态 `Session` 与自动上下文压缩（`session`）、`AgentState`/`StateExtension` 状态容器与扩展契约（`state`）、默认开启的 JSONL 审计日志（`log`）、`Agent` 循环（`agent`），以及端到端语音的 realtime 契约——`RealtimeModel` 抽象父类与类型化事件词汇（`realtime_model`）、`AudioSource`/`AudioSink` 音频 IO 抽象（`audio`）、`RealtimeAgent` 实时编排循环（`realtime_agent`：打断、回声抑制、收集式 function calling、后台文字指令注入）。使用方继承 `Model` 适配任意 LLM API、继承 `Tool` 加工具即得完整 harness agent；继承 `RealtimeModel` 适配厂商实时语音协议即得完整实时语音 agent。对外 API 统一从 `wy_core/__init__.py` 导出，禁止新增运行时依赖、禁止 import 本仓其他包；可独立构建发布（`uv build --package wy-core`）。
  - `packages/wy-coding-agent/`（发行名 `wy-coding-agent`，import 名 `wy_coding_agent`）：基于 wy-core 的编码 agent 应用（由原 wiley-agent 与 wiley-tui 合并而来）——`anthropic`（`AnthropicModel` 模型实现）、`tools/`（bash/glob/grep/read/edit/write 内置工具、工具搜索 `ToolSearchTool` 与 MCP/Skill/子 agent 工具执行器 `MCPTool`/`SkillTool`/`AgentTool`）、`mcp`、`skills`（Agent Skills：SKILL.md 目录发现/渲染，三级渐进披露）、`config`、`prompt_template`（`prompt_sections` 存分节模板常量）、`reminders`（动态 system-reminder 层承载 claudeMd 上下文注入 + `HarnessState` harness 全局状态，plan 模式经 system prompt 按状态组装，`/plan` 交互与 `exit_plan_mode` 工具配套）、`session`（`SessionStore` 持久化）、`conversation`（编排）、`factory`（`bootstrap` + `create_agent`）、`tui/`（Textual 前端）与 `main`（console script `wy-coding-agent`；根目录 `main.py` 是它的薄启动壳）。运行依赖 `wy-core`、`anthropic`（官方 SDK）、`mcp`、`ripgrep`（PyPI 二进制 wheel，供 grep/glob 工具调用）、`textual`。
  - `packages/wy-realtime-agent/`（发行名 `wy-realtime-agent`，import 名 `wy_realtime_agent`）：wy-core realtime 契约的 Qwen-Audio 具体实现，经 WebSocket 全双工协议驱动实时语音大模型（协议参考包内 `realtime_llm_ws.md` 与配套的 `realtime_llm_ws_client_event.md`/`realtime_llm_ws_server_event.md` 事件参考）——`config`（`[realtime]` 段 + `[[mcp.servers]]`）、`protocol`（`RealtimeClient` WS 传输层 + `build_session_config`）、`qwen`（`QwenRealtimeModel`：`wy_core.RealtimeModel` 的实现，wire 事件 ↔ 类型化事件翻译）、`audio`（`MicSource`/`SpeakerSink`：`wy_core.AudioSource`/`AudioSink` 的 sounddevice 实现，16k 入 / 24k 出）、`mcp` 与 `tools/`（read 内置工具 + `MCPTool`，均为 wy-coding-agent 同名实现的复刻件）、`factory`（`bootstrap` + `create_agent`，组装 `QwenRealtimeModel` + `wy_core.RealtimeAgent`）、`tui`（Textual 流式字幕前端：转写增量原位刷新、生命周期事件驱动状态栏，不经包根导出）与 `main`（console script `wy-realtime-agent`，默认启动 TUI，`--plain` 纯控制台逐行输出）。实时编排（打断、回声抑制、收集式 function calling、`send_user_text` 后台指令注入）在 `wy_core.RealtimeAgent`，本包不再自带编排循环；实时协议是服务端维护上下文的推送式流，不使用 `wy_core.Model`/`Agent`/`Session`（拉取式回合契约），复用 realtime 契约全家加 `Tool` 契约、`AuditLog` 与 `ToolCall`/`ToolResult` 事件词汇。运行依赖 `wy-core`、`websockets`、`sounddevice`（PyPI wheel 自带 PortAudio）、`mcp`、`textual`。
- 依赖方向：app 包各自只依赖 `wy_core`（`wy_coding_agent → wy_core`、`wy_realtime_agent → wy_core`），app 包之间互不依赖（重复的 read/MCP 实现是刻意的复刻件，见各包 AGENTS.md）。核心运行时契约（Agent/Model/Tool/Session/AuditLog、消息与事件类型）一律定义在 `wy_core`，应用层与 TUI 直接消费；`wy_core` 永远不 import 应用包、不引入 UI 依赖。
- 包内模块一律以 `from <包>.<sub> import ...` 子模块路径互引，**禁止 import 本包包根**（包根 `__init__` 导入各子模块，反向引用会循环导入）。`wy_coding_agent.tui` 不经包根导出，由 `main` 组装。
- 库代码不得用 `__file__` 反推仓库根或假设自己活在本仓库里：默认路径一律按调用方 CWD 解析（会话默认 `CWD/.agent_session/`，审计日志随会话文件同目录，`bootstrap` 默认读 `CWD/config.toml`，`wy_core.AuditLog.default()` 写 `CWD/.wy_audit/`），且必须提供显式参数覆盖。`create_agent` 不隐式读任何配置文件；显式读配置的组装入口只有 `bootstrap(config_path=...)`。装进 site-packages 后行为必须与源码运行一致。
- 常用命令：`uv sync` 安装全部成员与 dev 组；`uv run pytest` 从根跑全部测试（testpaths 配在根 pyproject，测试分属 `packages/*/tests/`；各包测试目录不建 `__init__.py`，测试文件与辅助模块名跨包必须唯一，如 `helpers.py` 对 `app_helpers.py`、`realtime_helpers.py`）；`uv run wy-coding-agent`（或 `uv run python main.py`）启动编码 agent TUI；`uv run wy-realtime-agent` 启动实时语音 agent TUI（`--plain` 纯控制台输出）；`uv build --package <名>` 单独打包任一成员。
- 新增 harness/runtime 能力放入 `packages/wy-core/`（保持零依赖与极简，宁缺毋滥）；新增模型实现、本地工具、MCP、配置解析、持久化与展示逻辑按所属应用放入 `packages/wy-coding-agent/` 或 `packages/wy-realtime-agent/`（实时语音协议/音频相关归后者）。各包的公开导出是对外契约，增删导出或改签名时注意兼容性。

## Module guidelines

- 各包的详细模块约定按目录就近存放，改哪个包的代码就先读哪份（Claude Code 在读写对应目录文件时会自动加载它们，故不在此处全量引入）：
  - `packages/wy-core/AGENTS.md`：统一消息词汇、Model/Tool 契约语义、Session 压缩算法、AuditLog 格式、Agent 循环与异常回滚。
  - `packages/wy-coding-agent/AGENTS.md`：factory 组装、AnthropicModel、工具体系（bash/grep/read/edit/write）、MCP、持久会话、TUI 展示约定。
  - `packages/wy-realtime-agent/AGENTS.md`：与 wy-core 的复用边界、实时协议编排（打断/回声抑制/收集式 function calling）、音频 IO、复刻件与 wy-coding-agent 的对应关系、测试约定。

## Code organization

- 编写或修改项目代码时，必须考虑模块化设计。
- 应按照功能和职责拆分代码，将不同功能集合放在不同的文件或模块中。
- 避免把配置读取、模型调用、界面展示、状态管理等不同职责集中在同一个文件中。
- 新增功能前，应先判断它属于现有模块还是需要建立新的独立模块，并保持模块边界清晰。
- 入口文件只负责程序启动和模块组装，不应包含大量具体业务实现。

## Task completion

- 每次任务完成后，必须评估是否需要更新 AGENTS.md，并写到正确的层级：全局约定、workspace 结构、跨包规则更新根 `AGENTS.md`；包内模块约定更新对应的 `packages/*/AGENTS.md`。
- 如果任务引入了新的执行约定、项目结构变化、工具使用方式或后续任务需要了解的注意事项，应及时补充，以便指导后续任务的执行。
- 由于限流问题，不要使用forkSubagent工具。
