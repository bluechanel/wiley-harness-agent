# wiley-harness-agent

一个使用 Python 实现的、类似 Codex CLI 的 MVP 项目。

## 开发环境

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

依赖和虚拟环境由 `uv` 管理，终端界面使用 Textual 构建。

## 项目结构

仓库是 uv workspace，包含两个独立的包：

- `packages/wiley-agent`：UI 无关的 agent harness 库（LLM provider、agent 循环、工具、会话持久化、system prompt 组装、MCP client）。可以单独构建发布：`uv build --package wiley-agent`；其他项目安装后 `from wiley_agent import create_agent` 即可使用，配置、会话目录、工具集均可通过参数注入。
- `packages/wiley-harness-agent`：Textual TUI 应用，依赖 `wiley_agent` 的公开 API。

运行测试（覆盖两个包）：

```bash
uv run pytest
```

## 配置

复制配置模板并填写 Anthropic API Key、Base URL 和模型名称：

```bash
cp config.example.toml config.toml
```

`config.toml` 已加入 `.gitignore`，不会被 Git 跟踪。

## 运行

```bash
uv run python main.py
```

也可以使用项目命令启动：

```bash
uv run wiley-harness-agent
```

程序会在当前进程内保存消息历史，以支持多轮对话。消息列表固定跟随底部，用户输入和 AI 输出均支持 Markdown 渲染。Anthropic Messages API 采用流式响应：thinking 和 text 片段一旦返回就会立即更新界面，思考区域使用较淡颜色展示。将 `thinking_budget_tokens` 设为 `0` 可以关闭扩展思考。输入 `exit` 或 `quit` 结束。

模型请求通过项目内的 `provider` 包发送，不依赖 Anthropic SDK。API Key、Base URL 和模型参数统一从 `config.toml` 读取，再传入 provider。`AnthropicProvider` 使用 aiohttp 异步调用 Anthropic Messages API，在 `stream_request` 内解析 SSE，并返回统一的 `TextDelta`、`ReasoningDelta`、`ToolCall`、`ToolResult`、`ErrorEvent`、`UsageEvent` 和 `DoneEvent`。`UsageEvent` 使用 provider 层独立的 `ProviderUsage`，进入 TUI 前才转换为 `ChatUsage`；新增其他 LLM API 时，应继承 `BaseProvider` 并实现相同的流式请求接口。

每轮回答结束后，消息下方会显示输入、输出、缓存读写、上下文数量，以及跨多轮累计的统计。

## 会话记录

每次启动默认生成一个 UUID 作为 `session_id`，并在 `sessions/` 目录创建同名 JSONL 文件。用户输入、模型思考、正式回答，以及未来的工具调用和工具输出都会逐条追加到该文件。

退出界面后，终端会打印当前 `session_id`。下次启动时直接传入该 UUID 即可恢复历史消息、模型上下文和累计 Token 统计：

```bash
uv run wiley-harness-agent <session_id>
```
