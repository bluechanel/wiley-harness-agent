# wiley-harness-agent

一个使用 Python 实现的、类似 Codex CLI 的 MVP 项目。

## 开发环境

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

依赖和虚拟环境由 `uv` 管理，终端界面使用 Textual 构建。

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

每轮回答结束后，消息下方会显示输入、输出、缓存读写、上下文数量，以及跨多轮累计的统计。
