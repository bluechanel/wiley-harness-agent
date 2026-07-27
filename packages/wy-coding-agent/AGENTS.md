# wy-coding-agent Module Conventions

包定位、依赖方向（`wy_coding_agent → wy_core`）、import 规则与路径解析约定见仓库根 `AGENTS.md`；核心契约（Agent/Model/Tool/Session/AuditLog、消息与事件）的语义见 `packages/wy-core/AGENTS.md`。本文件是应用包内各模块的详细约定。

## Factory（组装入口）

- `factory.py` 是组装的唯一入口，两层：`bootstrap(session_id=None, *, config_path=None)` 一站式组装——`load_config` 构造 `AnthropicModel` → 内置 `DEFAULT_TOOLS` → `load_compaction_config` → `create_agent`（`mcp_config` 即同一 config.toml，`[anthropic]`/`[compaction]`/`[[mcp.servers]]` 共用这一份文件），TUI 等宿主用它；`create_agent(session_id=None, *, model, instruction=None, tools=None, prompt_providers=None, workspace=None, sessions_dir=None, compaction=None, mcp_config=None, audit=True)` 为可编程组装点。session_id 传入则恢复会话、省略则自动生成 UUID。恢复语义：`SessionStore.conversation_messages()` 把已完成的问答对回灌 `wy_core.Session`，累计用量与上下文规模一并恢复,未完成回合不回灌。审计默认开启,写会话文件同目录的 `<session_id>.audit.jsonl`(`audit=False` 关闭);压缩参数经 `CompactionConfig` 透传给 `wy_core.Session`。外部（含 TUI）一律经这两个入口创建 agent，不手工拼装 Agent/SessionStore。

## AnthropicModel

- `anthropic.py` 的 `AnthropicModel` 实现 `wy_core.Model` 契约：aiohttp 直连 Anthropic Messages API、SSE 解析,厂商参数全在构造期。按 wy-core 的"实现方组装"取舍：流中只产出 `TextDelta`/`ThinkingDelta` 增量供渲染，同时在内部按 index 累积内容块与工具参数 JSON 片段，流末组装完整 assistant 消息（thinking 的 signature、tool_use 的入参都在此填好）交付 `ModelEnd`；`message_delta.stop_reason` 原样透传（`"tool_use"` 驱动工具循环）。流内 error 事件、HTTP 非 2xx、传输/解码失败一律 raise `ModelError`。`RedactedThinkingBlock` 是本模块定义的应用侧扩展块（wy-core 的块联合允许应用扩展，核心逐块 isinstance 判断、未知块原样携带），`_message_to_wire` 负责全部块类型与 wire 格式的双向翻译。thinking 预算为 0 时不发 thinking 字段。

## 工具体系

- 工具体系在 `tools/`：`base.py` re-export `wy_core.Tool` 并提供 `FunctionTool`（definition 字典 + 执行函数 → Tool 实例），内置工具每个一个子模块（`bash.py`、`grep.py`、`read.py`、`edit.py`、`write.py`），模块级定义 Tool 实例即可——`__init__.py` 自动扫描收集为 `DEFAULT_TOOLS`（同名不同实例导入期报错），新增内置工具不需要手工注册；不含 Tool 实例的共享辅助模块（如 `file_state.py`）可以并存。执行语义由 wy-core 统一：同步执行器经 `asyncio.to_thread` 调用、失败转 `Error: ...` 的 tool_result 不中断回合。
- `bash` 工具：进程级单例的持久 bash 会话（惰性启动、stderr 并入 stdout、独立进程组），哨兵行捕获输出与退出码；`restart=true` 重建会话；超时（默认 120s）或会话意外退出时 kill 进程组、下次惰性重建；输出截断 200 行 / 30000 字符、读取阶段 1MB 捕获上限。安全措施：执行前经 `validate_command` 校验——shlex 解析后按 `ALLOWED_COMMANDS` 白名单放行首 token，拒绝独立 shell 运算符与 `$`/反引号展开；校验是绊线不是边界，无沙箱。审计经 `logging`（`...tools.bash` logger）记录所有命令。
- `grep` 工具：shell out 到真实 ripgrep（PyPI `ripgrep` 依赖定位 `rg`），搜索语义即 rg 语义；harness 层做三种 `output_mode`、分页（`head_limit` 默认 250、`offset`）、路径相对化；固定 `--hidden`、排除 VCS 目录、30000 字符上限、30s 超时（超时抛错不静默返回空）。
- 文件三件套 `read`/`edit`/`write` 靠 `file_state.py` 的进程级读取状态注册表联动：`read` 记录所见内容/mtime/范围，`edit` 与 `write`（覆盖已有文件时）要求先读过且磁盘未变（内容一致则容忍时间戳抖动），改写成功后刷新记录。`read`：cat -n 格式、offset/limit 分页（默认 2000 行）、单行截 2000 字符、全量 256KB 上限、拒绝二进制与设备文件、同范围重读未变返回 unchanged 存根；默认截断视为 partial view 不授权编辑。`edit`：old_string 精确且唯一（多匹配提示 `replace_all`）、CRLF 保留。`write`：内容原样落盘、自动建父目录。错误消息措辞对齐 Claude Code 参考实现，便于模型自纠。

## 会话持久化与编排

- `session.py` 的 `SessionStore`/`SessionRecord`：JSONL 逐条追加(flush + fsync)、按 id 恢复。文件格式与旧 wiley-agent 会话完全兼容——usage 序列化沿用 `cache_creation_input_tokens`/`cache_read_input_tokens` 键名（对应 `wy_core.Usage` 的 `cache_write_tokens`/`cache_read_tokens`，经 `usage_to_dict`/`usage_from_mapping` 映射）。记录形态：user/input、tool_call、tool_output、assistant/thinking、assistant/answer（usage 为本回合增量、total_usage 为累计、metadata.context_tokens 为回合后上下文规模）、assistant/error、assistant/compaction（content 为摘要、metadata.dropped 为被总结条数）。
- `conversation.py` 的 `ConversationService` 包 `wy_core.Agent`：把 `AgentEvent` 流写入会话记录并原样透传给 UI。落盘的思考/正文取自本回合组装完成的 assistant 消息（增量事件只是实时渲染的装饰，可能不完整）；回合起点在收到 `Compaction` 事件时按 `dropped` 重新锚定。异常写 assistant/error 记录后向上抛（wy-core 侧已回滚内存会话）。`close()` 释放 MCP 连接（factory 注入 closer），宿主 finally 里调用。

## MCP client

- `mcp.py` 的 `MCPClientManager`：TOML `[[mcp.servers]]` 配置（stdio/http 两种 transport），SDK 全 async 且 session 是 task 作用域 context manager，而工具执行器是同步契约——manager 用专用后台线程跑独立 event loop，工具执行经 `run_coroutine_threadsafe(...).result(120s)` 桥接，以 `FunctionTool` 形态并入工具集（`mcp__<server>__<tool>` 命名；server 侧 isError → raise，由 wy-core 循环统一转 `Error: ...`）。`start()` 阻塞至各 server 就绪或超时（失败记 warning 跳过，不阻断启动），组装后显式查重工具名（冲突抛 ConfigError）。测试用 `sys.executable -c` 内联 FastMCP 脚本起真实 stdio server 做全链路验证。

## System prompt 组装

- `prompt_template.py` 的 `build_prompt(instruction, providers)` 统一组装 system prompt：instruction 首段 + 各 `BasePromptProvider` 段落（`ModelProvider`/`WorkspaceProvider`/`AgentMDProvider`/`SkillProvider`/`MemoryProvider`），`provide()` 返回 None 跳过且不得因来源缺失抛异常。注意与 `wy_core.Model` 是两套体系。工具 schema 经 API `tools` 参数传递，不拼入 system prompt。

## TUI 展示层

- `tui/` 是纯展示层：`render.py` 保持纯函数（`SessionRecord`/事件 → `MessageView` Markdown），新增展示逻辑放这里并配测试；`app.py` 只做界面组件与交互，不解析业务数据。TUI 只消费 `wy_core` 的 `AgentEvent` 事件与本包 `SessionRecord` 等公开数据类型，不触碰工具层。
- 思考过程、工具调用/输出、上下文压缩块默认收起、点击标题展开（`MessageView.collapsible_title` 非空即收起块，Textual `Collapsible` 承载）；工具参数/输出做动态围栏与展示截断（30 行 / 2000 字符），会话文件始终保留完整内容。
- 用量不进会话区，由输入框下方的用量条展示（`usage_bar_text`：输入/输出/缓存为累计值、上下文为最近一次请求的规模），在 `TurnEnd` 事件时刷新。
- `main.py` 是唯一入口：argparse → `bootstrap(session_id)` → 启动 TUI → finally `close()`。
