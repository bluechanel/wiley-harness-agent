# Project Guidelines

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

- 模型 API 实现放在 `src/wiley_harness_agent/provider/`。API Key、Base URL 等配置统一从 `config.toml` 读取后传入 provider，不从环境变量读取。新 provider 必须继承 `BaseProvider`，并实现以 `messages` 为必填参数的 `stream_request` 流式请求接口。
- `stream_request` 必须在 provider 内完成厂商 SSE 协议解析，并返回 `provider.events` 中定义的 provider-neutral 类型；usage 使用 `ProviderUsage`，不得依赖面向 TUI 的 `ChatUsage`。业务层不得解析厂商原始事件字段。
- 业务层通过 provider 接口发起模型请求；不要在 `ChatService` 中直接引入厂商 SDK。
