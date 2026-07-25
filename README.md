# wiley-harness-agent

一个使用 Python 实现的、类似 Codex CLI 的 MVP 项目。

## 开发环境

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

依赖和虚拟环境由 `uv` 管理，终端界面使用 Textual 构建。

## 项目结构

仓库是 uv workspace，packages 只分 agent 与 tui 两部分：

- `packages/wiley-agent`：大而全的 agent 应用核心（UI 无关）——provider（契约 + 内置 `AnthropicProvider`）、内置工具集（`bash`、`text_editor`，auto-scan 组成 `DEFAULT_TOOLS`）、config.toml 解析、agent 循环、会话持久化、system prompt 组装、MCP client、debug。`bootstrap(session_id, config_path=...)` 读 config.toml 返回就绪 agent；需要自定义 provider/工具时用 `create_agent(provider=..., tools=...)`。可单独构建发布：`uv build --package wiley-agent`。
- `packages/wiley-tui`：纯展示的 Textual TUI 前端 + 薄启动入口，单向依赖 `wiley_agent` 的公开 API：`render.py` 把 agent 层记录/事件渲染成 Markdown，`app.py` 是界面组件，`main.py` 只做 argparse + `bootstrap()` + 启动界面。

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
uv run wiley-tui
```

程序会在当前进程内保存消息历史，以支持多轮对话。消息列表固定跟随底部，用户输入和 AI 输出均支持 Markdown 渲染。Anthropic Messages API 采用流式响应：thinking 和 text 片段一旦返回就会立即更新界面，思考区域使用较淡颜色展示。将 `thinking_budget_tokens` 设为 `0` 可以关闭扩展思考。输入 `exit` 或 `quit` 结束。

provider 体系都在 `wiley_agent` 内：`BaseProvider` 契约的 `stream_request(messages, *, system, tools)` 签名固定，返回统一的 `TextDelta`、`ReasoningDelta`、`ToolCall`、`ToolResult`、`ErrorEvent`、`UsageEvent` 和 `DoneEvent` 事件流；厂商参数（模型名、max_tokens、thinking、鉴权、endpoint 等）全部在实现类构造期注入。内置实现是 `wiley_agent/provider/anthropic.py` 的 `AnthropicProvider`：aiohttp 直连 Anthropic Messages API、不依赖 SDK，API Key、Base URL 和模型参数由 `wiley_agent/config.py` 从 `config.toml` 读取后经 `bootstrap` 在构造时传入。`UsageEvent` 使用 provider 层独立的 `ProviderUsage`，进入 TUI 前才转换为 `ChatUsage`。新增其他 LLM API 时，在库内新增 `provider/<vendor>.py` 实现契约，或在外部实现 `BaseProvider` 后注入 `create_agent(provider=...)`。

每轮回答结束后，消息下方会显示输入、输出、缓存读写、上下文数量，以及跨多轮累计的统计。

## 会话记录

每次启动默认生成一个 UUID 作为 `session_id`，并在 `.agent_session/` 目录创建同名 JSONL 文件。用户输入、模型思考、正式回答，以及工具调用和工具输出都会逐条追加到该文件。

退出界面后，终端会打印当前 `session_id`。下次启动时直接传入该 UUID 即可恢复历史消息、模型上下文和累计 Token 统计：

```bash
uv run wiley-tui <session_id>
```
