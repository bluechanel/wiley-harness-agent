# wiley-harness-agent

一个使用 Python 实现的、类似 Codex CLI 的 MVP 项目。

## 开发环境

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

依赖和虚拟环境由 `uv` 管理，终端界面使用 Textual 构建。

## 项目结构

仓库是 uv workspace，packages 分 core 与 app 两部分：

- `packages/wy-core`：零依赖（纯标准库）的极简 agent core runtime 库——统一消息定义、`Tool`/`Model` 抽象父类、内存态 `Session` 与自动上下文压缩、默认开启的 JSONL 审计日志、`Agent` 循环。使用方依赖它、继承 `Model` 适配任意 LLM API、继承 `Tool` 增加工具，即得到完整 harness agent。可单独构建发布：`uv build --package wy-core`。
- `packages/wy-coding-agent`：基于 wy-core 的编码 agent 应用——`AnthropicModel` 模型实现（官方 anthropic SDK 流式接入）、内置工具集（`bash`、`grep`、`read`/`edit`/`write`，auto-scan 组成 `DEFAULT_TOOLS`）、MCP client、config.toml 解析、system prompt 组装、会话持久化与 Textual TUI。`bootstrap(session_id, config_path=...)` 读 config.toml 返回就绪 agent；需要自定义模型/工具时用 `create_agent(model=..., tools=...)`。

运行测试（覆盖两个包）：

```bash
uv run pytest
```

## 配置

复制配置模板并填写 Anthropic API Key、Base URL 和模型名称：

```bash
cp config.example.toml config.toml
```

`config.toml` 已加入 `.gitignore`，不会被 Git 跟踪。可选段：`[compaction]` 调整自动上下文压缩阈值，`[[mcp.servers]]` 接入 MCP server。

## 运行

```bash
uv run python main.py
```

也可以使用项目命令启动：

```bash
uv run wy-coding-agent
```

消息列表固定跟随底部，用户输入和 AI 输出均支持 Markdown 渲染。Anthropic Messages API 采用流式响应：thinking 和 text 片段一旦返回就会立即更新界面，思考区域使用较淡颜色展示。将 `thinking_budget_tokens` 设为 `0` 可以关闭扩展思考。输入 `exit` 或 `quit` 结束。

模型接入契约在 `wy_core`：`Model.stream(messages, *, system, tools)` 签名固定，流中产出 `TextDelta`/`ThinkingDelta` 增量供实时渲染，流末以一个 `ModelEnd`（组装完成的 assistant 消息 + 用量 + 停止原因）收尾；厂商参数（模型名、max_tokens、thinking、鉴权、endpoint 等）全部在实现类构造期注入。`AnthropicModel` 是内置参考实现；新增其他 LLM API 时实现 `wy_core.Model` 后注入 `create_agent(model=...)` 即可。

每轮回答结束后，底部用量条会显示输入、输出、缓存的累计统计与当前上下文规模。上下文超过阈值时会自动把较早历史压缩为一条摘要（界面出现"上下文压缩"块）。

## 会话记录

每次启动默认生成一个 UUID 作为 `session_id`，并在 `.agent_session/` 目录创建同名 JSONL 文件。用户输入、模型思考、正式回答、工具调用与输出、上下文压缩都会逐条追加到该文件；同目录的 `<session_id>.audit.jsonl` 是完整审计日志（每次模型请求/响应、工具调用/结果、压缩与错误）。

退出界面后，终端会打印当前 `session_id`。下次启动时直接传入该 UUID 即可恢复历史消息、模型上下文和累计 Token 统计：

```bash
uv run wy-coding-agent <session_id>
```
