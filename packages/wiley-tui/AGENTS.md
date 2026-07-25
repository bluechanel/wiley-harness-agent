# wiley-tui Module Conventions

包定位（纯展示前端、单向依赖 `wiley_agent` 公开 API、`main.py` 唯一入口）见仓库根 `AGENTS.md`；本文件是 TUI 展示层的详细约定。

## 展示约定

- 会话区渲染消息与工具块（`wiley_tui/render.py` 的 `tool_call_view`/`tool_output_view`，参数/输出做动态围栏与展示截断，会话文件始终保留完整内容）；思考过程与工具调用/输出块默认收起、点击标题展开（`MessageView.collapsible_title` 非空即收起块，`wiley_tui/app.py` 用 Textual `Collapsible` 承载，标题即内容摘要，正文不再重复标题）。
- 用量不进会话区，由输入框下方与状态并列的用量条展示（`usage_bar_text`：输入/输出/缓存为累计值、上下文为最近一次请求的值；`context_tokens` 语义见 `packages/wiley-agent/AGENTS.md` 的 AgentService 流事件一节）。
- `render.py` 保持纯函数（记录/事件 → Markdown），新增展示逻辑放这里并配测试；`app.py` 只做界面组件与交互，不解析业务数据。
- TUI 只消费 `ChatStreamEvent` 流事件与 `SessionRecord` 等公开数据类型，不触碰工具层，也不 import `wiley_agent` 的私有子模块。
