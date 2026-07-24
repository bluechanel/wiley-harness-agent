# Project Guidelines

## Module layout

- `src/wiley_harness_agent/` 按职责拆分为两个子包：
  - `agent/`：harness 部分（`config`、`usage`、`provider/`、`service` 中的 `AgentService` agent 循环、`prompt_template` system prompt 组装、`conversation`、`session` 持久化、`tools/` 基础工具包）。该包与 UI 无关，禁止导入 `textual` 或 `tui` 包。对外 API 统一从 `agent/__init__.py` 导出。
  - `tui/`：Textual 界面，单向依赖 `wiley_harness_agent.agent` 的公开 API。`tui/render.py` 负责把 agent 层的记录/事件解析为展示用 Markdown（纯函数），`tui/app.py` 只负责界面组件与交互。
- `agent/factory.py` 的 `create_agent(session_id=None, *, instruction=None, tools=None, prompt_providers=None)` 是 agent 模块的唯一入口：session_id 传入则恢复会话、省略则自动生成 UUID；instruction 为系统提示词首段；tools 省略时启用 `DEFAULT_TOOLS`；prompt_providers 省略时使用 `default_prompt_providers(config)` 的默认组合。外部（含 TUI 组装）一律通过它创建 agent，不手工拼装 AgentService/SessionStore。
- 基础工具集中在 `agent/tools/` 包：`base.py` 定义 `Tool`（API schema + 本地执行器），每个工具一个子模块（现有 `text_editor.py`、`bash.py`），在模块级定义 `Tool` 实例即可——`tools/__init__.py` 自动扫描包内所有子模块收集全部 `Tool` 实例组成 `DEFAULT_TOOLS`（同名不同实例在导入期报错），新增工具不需要手工注册。工具执行失败以 `Error: ...` 字符串作为 tool_result 返回给模型（同时置 `is_error: true`；成功结果不带该字段），不中断流；`AgentService` 用 `asyncio.to_thread` 执行工具，执行器保持同步实现即可，允许阻塞（如 bash 命令）而不冻结事件循环。
- `bash` 工具（`tools/bash.py`）参照 Anthropic bash tool 文档实现：进程级单例的持久 bash 会话（惰性启动、stderr 并入 stdout、`start_new_session` 独立进程组），用每次调用唯一的哨兵行捕获输出与退出码（非零退出在结果尾部附 `Exit code: N`）。`restart=true` 重建会话；命令超时（默认 120s，`_COMMAND_TIMEOUT_SECONDS`）或会话意外退出时 kill 整个进程组、下次调用惰性重建。输出截断：200 行 / 30000 字符；读取阶段另有 1MB 捕获上限（单次 readline 100KB），失控输出只丢弃不撑爆内存；子进程输出以 `errors="replace"` 解码，坏字节不会杀死读线程。审计：经 `logging`（`...tools.bash` logger）记录所有命令——执行前记命令（挂起也留痕）、完成后记退出码与输出前 200 字符、拒绝/超时记 warning。安全措施：命令执行前必须经 `validate_command` 显式校验——shlex 解析后按 `ALLOWED_COMMANDS` 白名单（而非黑名单）放行首个 token，并拒绝独立成词的 shell 运算符（`SHELL_OPERATORS`）及 `$`/反引号展开，解析失败即拒绝；校验不通过以 `Error: ...` 返回、不执行。扩展可用命令改 `ALLOWED_COMMANDS`。校验是绊线不是边界：仍无沙箱，放行的命令以当前用户权限执行。
- debug 模式由 `agent/debug.py` 的 `DebugRecorder` 实现：config.toml 配置 `[debug] enabled = true` 开启（`load_debug_config` 读取，文件/配置段缺失即关闭），由 `create_agent` 装配、经 `AgentService(debug_recorder=...)` 注入（None 即关闭）。开启后把执行轨迹追加写 `sessions/<session_id>.debug.jsonl`（恢复会话时继续追加），记录类型：`session_start`、`request`（每轮完整请求体，含 messages 与 stream 标志，不含 api_key）、`response_event`（逐条 provider 中立事件，刻意不聚合，用于定位"有请求无响应/流中断在哪"）、`response_end`（轮次完成标记，request 无配对 response_end 即该轮未完成）、`tool_call`/`tool_result`、`error`。挂钩只放在 `AgentService`（业务层），不改 provider 契约；debug 写入 flush 不 fsync（区别于 `SessionStore`）。
- system prompt 统一由 `agent/prompt_template.py` 的 `build_prompt(instruction, providers)` 组装：instruction 为首段，其后按序拼接各 prompt provider 的段落。每个段落由 `BasePromptProvider` 子类提供（`ModelProvider`/`WorkspaceProvider`/`AgentMDProvider`/`SkillProvider`/`MemoryProvider`），`provide()` 返回完整 markdown 段落、返回 None 则跳过该段，且不得因来源缺失/不可读抛异常。新增段落时实现 `BasePromptProvider` 并加入 `default_prompt_providers`；注意与 LLM 侧 `agent/provider/base.py` 的 `BaseProvider` 是两套体系，不要混用。`SkillProvider`（扫描 `workspace/skills/*.md` 列清单）与 `MemoryProvider`（读 `workspace/MEMORY.md` 全文）目前为骨架，注入格式与路径约定后续迭代。不要在其他位置手工拼接系统提示词；工具的 schema/描述通过 API 请求的 `tools` 参数传递，不拼入 system prompt。
- 包顶层的 `app.py` 是组装入口：只负责读取参数、调用 `create_agent` 并启动 TUI，不包含业务实现。
- 新增 harness 能力放入 `agent/`，新增展示逻辑放入 `tui/`；两侧共享的数据类型定义在 `agent/` 中，由 `tui` 消费。

## Code organization

- 编写或修改项目代码时，必须考虑模块化设计。
- 应按照功能和职责拆分代码，将不同功能集合放在不同的文件或模块中。
- 避免把配置读取、模型调用、界面展示、状态管理等不同职责集中在同一个文件中。
- 新增功能前，应先判断它属于现有模块还是需要建立新的独立模块，并保持模块边界清晰。
- 入口文件只负责程序启动和模块组装，不应包含大量具体业务实现。

## Task completion

- 每次任务完成后，必须评估是否需要更新 `AGENTS.md`。
- 如果任务引入了新的执行约定、项目结构变化、工具使用方式或后续任务需要了解的注意事项，应及时将其补充到 `AGENTS.md`，以便指导后续任务的执行。

## LLM providers

- 模型 API 实现放在 `src/wiley_harness_agent/agent/provider/`。API Key、Base URL 等配置统一从 `config.toml` 读取后传入 provider，不从环境变量读取。新 provider 必须继承 `BaseProvider`，实现 `stream_request`：`messages` 为必填参数，其余厂商支持的请求参数全部显式声明为 keyword-only 形参（参考 `AnthropicProvider`），不允许 `**options` 透传——未知参数应在调用处直接 TypeError。
- provider 负责把显式参数中非 None 的项组装进请求体并附加传输细节（endpoint、headers、stream 标志）；各参数的取值仍统一由 `AgentService._request_options` 决定，业务语义不下沉到 provider。
- `stream_request` 必须在 provider 内完成厂商 SSE 协议解析，并返回 `provider.events` 中定义的 provider-neutral 类型；usage 使用 `ProviderUsage`，不得依赖面向 TUI 的 `ChatUsage`。业务层不得解析厂商原始事件字段。
- 业务层通过 provider 接口发起模型请求；不要在 `AgentService` 中直接引入厂商 SDK。
