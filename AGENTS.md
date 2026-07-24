# Project Guidelines

## Module layout

- `src/wiley_harness_agent/` 按职责拆分为两个子包：
  - `agent/`：harness 部分（`config`、`usage`、`provider/`、`service` 中的 `AgentService` agent 循环、`prompt_template` system prompt 组装、`conversation`、`session` 持久化、`tools`/`text_editor` 工具）。该包与 UI 无关，禁止导入 `textual` 或 `tui` 包。对外 API 统一从 `agent/__init__.py` 导出。
  - `tui/`：Textual 界面，单向依赖 `wiley_harness_agent.agent` 的公开 API。`tui/render.py` 负责把 agent 层的记录/事件解析为展示用 Markdown（纯函数），`tui/app.py` 只负责界面组件与交互。
- `agent/factory.py` 的 `create_agent(session_id=None, *, instruction=None, tools=None)` 是 agent 模块的唯一入口：session_id 传入则恢复会话、省略则自动生成 UUID；instruction 为系统提示词；tools 省略时启用 `DEFAULT_TOOLS`。外部（含 TUI 组装）一律通过它创建 agent，不手工拼装 AgentService/SessionStore。
- 新增工具时在 `agent/tools.py` 用 `Tool`（API schema + 本地执行器）封装并按需加入 `DEFAULT_TOOLS`；工具执行失败以 `Error: ...` 字符串作为 tool_result 返回给模型，不中断流。
- system prompt 统一由 `agent/prompt_template.py` 的 `build_system_prompt(instruction)` 组装，不要在其他位置手工拼接系统提示词；工具的 schema/描述通过 API 请求的 `tools` 参数传递，不拼入 system prompt。
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
