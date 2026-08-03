# wy-coding-agent Module Conventions

包定位、依赖方向（`wy_coding_agent → wy_core`）、import 规则与路径解析约定见仓库根 `AGENTS.md`；核心契约（Agent/Model/Tool/Session/AuditLog、消息与事件）的语义见 `packages/wy-core/AGENTS.md`。本文件是应用包内各模块的详细约定。

## Factory（组装入口）

- `factory.py` 是组装的唯一入口，两层：`bootstrap(session_id=None, *, config_path=None)` 一站式组装——`load_config` 构造 `AnthropicModel` → 内置 `DEFAULT_TOOLS` → `load_compaction_config` → `create_agent`（`mcp_config` 即同一 config.toml，`[anthropic]`/`[compaction]`/`[skills]`/`[[mcp.servers]]` 共用这一份文件；skills 目录取 `load_skills_config`，未配置时用 `default_skills_dirs()` 官方默认，`dirs = []` 显式关闭），TUI 等宿主用它；`create_agent(session_id=None, *, model, instruction=None, tools=None, prompt_providers=None, workspace=None, sessions_dir=None, compaction=None, mcp_config=None, skills_dirs=None, audit=True)` 为可编程组装点。session_id 传入则恢复会话、省略则自动生成 UUID。create_agent 内把 system prompt 提升为局部变量，以追加前的工具集快照构造 `AgentTool` 后追加（`agent` 工具总是装配），工具名查重在追加之后——可捕获注入工具与 `agent` 的撞名。恢复语义：`SessionStore.conversation_messages()` 把已完成的问答对回灌 `wy_core.Session`，累计用量与上下文规模一并恢复,未完成回合不回灌。审计默认开启,写会话文件同目录的 `<session_id>.audit.jsonl`(`audit=False` 关闭);压缩参数经 `CompactionConfig` 透传给 `wy_core.Session`。外部（含 TUI）一律经这两个入口创建 agent，不手工拼装 Agent/SessionStore。

## AnthropicModel

- `anthropic.py` 的 `AnthropicModel` 实现 `wy_core.Model` 契约：官方 anthropic SDK（`AsyncAnthropic`，每次 stream 新建、用毕关闭），厂商参数全在构造期；base_url 兼容旧配置填完整 `/v1/messages` endpoint（构造期剥掉后缀交给 SDK）。流中只产出 `TextDelta`/`ThinkingDelta` 增量供渲染（含 content_block_start 自带的初始文本），SSE 解析、内容块与工具入参 JSON 累积、429/5xx 重试由 SDK 流式 helper 完成，流末把 `get_final_message()` 翻译为 wy-core assistant 消息交付 `ModelEnd`；stop_reason 原样透传（`"tool_use"` 驱动工具循环，缺省补 `"end_turn"`）。SDK 管线的一切异常（AnthropicError 族、httpx 传输错误、残缺流的累积器断言）统一收敛 raise `ModelError`。`RedactedThinkingBlock` 是本模块定义的应用侧扩展块（wy-core 的块联合允许应用扩展，核心逐块 isinstance 判断、未知块原样携带），`_message_to_wire`/`_from_sdk_message` 负责全部块类型在 wy-core 与 SDK/wire 格式间的双向翻译。thinking 预算为 0 时不发 thinking 字段。测试经 `httpx.MockTransport` 注入罐装 SSE 走真实 SDK 管线（monkeypatch 模块内 `AsyncAnthropic` 符号），fixture 必须是规范 Anthropic 流：带 `event:` 行、`message_start` 打头、内容块先 start 再 delta。

## 工具体系

- 工具体系在 `tools/`：内置工具每个一个子模块（`bash.py`、`glob.py`、`grep.py`、`read.py`、`edit.py`、`write.py`），一律直接继承 `wy_core.Tool` 定义工具类（`name`/`description`/`parameters` 类属性 + `execute` 方法）并给出模块级实例——`__init__.py` 自动扫描收集实例为 `DEFAULT_TOOLS`（同名不同实例导入期报错），新增内置工具不需要手工注册；不含模块级 Tool 实例的模块（共享辅助如 `files.py`、`ripgrep.py`，或只有类的 `mcp_tool.py`/`skill.py`/`agent_tool.py`）可以并存，扫描自然跳过。`mcp_tool.py` 的 `MCPTool` 是 MCP 工具执行器，由 `mcp.py` 桥接层按连接构造；`skill.py` 的 `SkillTool` 是 Agent Skills 执行器，由 factory 按 `skills.py` 发现结果构造；`agent_tool.py` 的 `AgentTool` 是子 agent 派生执行器（详见下条）；三者均不进 `DEFAULT_TOOLS`。执行语义由 wy-core 统一：同步执行器经 `asyncio.to_thread` 调用、失败转 `Error: ...` 的 tool_result 不中断回合。
- `agent` 工具（`agent_tool.py` 的 `AgentTool`）：子 agent 派生执行器，由 factory 在完整工具集与 system prompt 就绪后构造并总是装配（无开关）。子 agent 复用父 agent 的 Model 与工具实例——前提是 Model 实现不持有事件循环绑定状态（`AnthropicModel` 每次 stream 新建客户端即满足，自定义 Model 需自查）；bash 持久会话随实例父子共享（白名单只读命令，接受此取舍）。工具集取追加 `agent` 之前的快照，构造上杜绝嵌套派生。`execute` 在 wy-core 工具线程内以 `asyncio.run` 驱动全新 `wy_core.Session`（压缩阈值沿用父级 `CompactionConfig`），`max_iterations=25` 作失控保险丝；任务文本自动附派发说明（告知子 agent 最终回复会原样返回）。黑盒展示：中间事件全部丢弃，结果取最后一条 assistant 消息正文、30000 字符截断（对齐 bash/grep），无正文时返回占位提示。审计每次派生独立写 `<session_id>.sub-<短id>.audit.jsonl`（与主审计同目录；`create_agent(audit=False)` 时子 agent 也不审计），文件在派生结束时关闭。
- `bash` 工具：持久 bash 会话挂在工具实例上（`DEFAULT_TOOLS` 每进程一份实例，惰性启动、stderr 并入 stdout、独立进程组），哨兵行捕获输出与退出码；`restart=true` 重建会话；超时（默认 120s）或会话意外退出时 kill 进程组、下次惰性重建；输出截断 200 行 / 30000 字符、读取阶段 1MB 捕获上限。安全措施：执行前经 `validate_command` 校验——shlex 解析后按 `ALLOWED_COMMANDS` 白名单放行首 token，拒绝独立 shell 运算符与 `$`/反引号展开；校验是绊线不是边界，无沙箱。审计经 `logging`（`...tools.bash` logger）记录所有命令。
- `grep` 工具：shell out 到真实 ripgrep（定位与调用 `rg` 的辅助在 `ripgrep.py`，与 glob 工具共享），搜索语义即 rg 语义；harness 层做三种 `output_mode`、分页（`head_limit` 默认 250、`offset`）、路径相对化；固定 `--hidden`、排除 VCS 目录、30000 字符上限、30s 超时（超时抛错不静默返回空）。
- `glob` 工具：按文件名模式找文件（对齐 Claude Code GlobTool）。`rg --files` 枚举候选（尊重 .gitignore、含隐藏文件、排除 VCS 目录），模式匹配在 harness 侧用自带的 gitignore 风格 glob→regex 翻译完成——不能用 rg 的 `--glob`，inclusion glob 会 override ignore 规则、把被忽略文件带回来。无斜杠模式匹配任意深度的文件名，含斜杠模式匹配相对搜索根的路径，`**` 跨零或多层目录；mtime 最新在前（同 grep 文件列表）、路径相对化、上限 100 条附截断提示；`path` 必须是存在的目录（缺失/非目录报错）。
- 文件三件套 `read`/`edit`/`write` 无跨调用状态，共享辅助集中在 `files.py`（`FileToolError`、路径解析、missing-file 提示、CRLF 规范化）。`read`：cat -n 格式、offset/limit 分页（默认 2000 行）、单行截 2000 字符、全量 256KB 上限、拒绝二进制与设备文件。`edit`：old_string 精确且唯一（多匹配提示 `replace_all`）、空 old_string 建新文件、CRLF 保留。`write`：内容原样落盘、自动建父目录。错误消息措辞对齐 Claude Code 参考实现，便于模型自纠。原 `file_state.py` 的读取状态注册表（read-before-edit、外部修改检测、重读去重）已整体移除：文件状态属于 agent 级状态，规划后续在 agent 状态层统一管理（当前仅预留、未实现），不要再往工具层塞这类跨调用状态。

## 会话持久化与编排

- `session.py` 的 `SessionStore`/`SessionRecord`：JSONL 逐条追加(flush + fsync)、按 id 恢复。文件格式与旧 wiley-agent 会话完全兼容——usage 序列化沿用 `cache_creation_input_tokens`/`cache_read_input_tokens` 键名（对应 `wy_core.Usage` 的 `cache_write_tokens`/`cache_read_tokens`，经 `usage_to_dict`/`usage_from_mapping` 映射）。记录形态：user/input（metadata.reminders 为本回合注入的 system-reminder 列表）、tool_call、tool_output、assistant/thinking、assistant/answer（usage 为本回合增量、total_usage 为累计、metadata.context_tokens 为回合后上下文规模）、assistant/error、assistant/compaction（content 为摘要、metadata.dropped 为被总结条数）、state/state（content 为 `AgentState.snapshot()` 聚合快照，见"Agent 状态管理"）。
- `conversation.py` 的 `ConversationService` 包 `wy_core.Agent`：把 `AgentEvent` 流写入会话记录并原样透传给 UI。落盘的思考/正文取自本回合组装完成的 assistant 消息（增量事件只是实时渲染的装饰，可能不完整）；回合起点在收到 `Compaction` 事件时按 `dropped` 重新锚定。异常写 assistant/error 记录后向上抛（wy-core 侧已回滚内存会话）。`close()` 释放 MCP 连接（factory 注入 closer），宿主 finally 里调用。

## Reminders 与 plan 模式

- `reminders.py` 是动态 system-reminder 层,与 `prompt_template` 的静态 provider 对称:`ReminderProvider` 协议(`provide() -> str | None`)每个用户回合被 `ConversationService` 轮询一次,结果经 `wy_core.Agent.run(reminders=...)` 注入本回合 user 消息尾部的 `<system-reminder>` 文本块——前缀(system prompt/工具/既有历史)不变以保前缀缓存,过期提示随早期历史被压缩摘要掉。新增"每回合可变状态提示"一律走这层,不改 system prompt。
- plan 模式即第一个应用:`PlanModeState`(factory 构造并总是装配)激活期**每回合重复注入**约束提示(消息流尾部约束力弱于 system prompt,重复注入是刻意补偿),`disable()` 后下一回合一次性注入"已退出"提示。它同时是 `wy_core.StateExtension`(key=`plan_mode`):`active` 随状态快照持久化、恢复会话不丢模式,一次性退出提示是易失状态不持久化。`tools/plan.py` 的 `ExitPlanModeTool` 与 `SkillTool`/`MCPTool` 同型(模块只有类,不进 `DEFAULT_TOOLS`),持共享状态,模型提交 `plan`(Markdown)即翻转状态退出;在 factory 中与 `AgentTool` 一样于工具集快照**之后**追加——子 agent 不含也不可控制 plan 模式。TUI 在 `on_input_submitted` 本地拦截 `/plan [args]`(harness 状态切换,不发给模型;有 args 则开模式后把 args 作为用户输入继续发送),切换后即调 `save_state()` 落盘,状态栏就绪文案显示"PLAN 模式";`render.tool_call_view` 对 `exit_plan_mode` 的非空 plan 直接以 Markdown 展开展示(不折叠不围栏)。**当前 v1 无用户审批对话框、无工具硬拦截**(plan 模式下 edit/write/bash 仍可执行,仅靠提示约束),两者随后续工具权限层补充。
- 持久化保真:`SessionStore.append_user(content, reminders=...)` 把 reminders 存入 metadata(content 仍是原始输入,TUI 历史渲染不受影响),`conversation_messages()` 恢复时按 metadata 重建含 reminder 块的 user 消息,保证回灌历史与模型当时所见一致。

## Agent 状态管理

- 状态容器用 `wy_core.AgentState`(factory 组装:`AgentState(session=..., extensions=(plan_mode,))` 传 `Agent(state=...)`),应用侧扩展继承 `wy_core.StateExtension`。持久化走现有会话 JSONL:`SessionStore.append_state(snapshot)` 追加 role=`state` 记录(content 即 `AgentState.snapshot()` 聚合快照),`latest_state()` 倒序取最近一条;恢复路径在 factory 回灌消息后 `state.restore(latest_state)`。落盘时机两处:`ConversationService` 在回合收尾(append_assistant 之后)调 `save_state()`——快照与最近一条 state 记录不同才追加;回合外的切换(TUI `/plan`)由宿主显式调 `save_state()` 即时落盘。`conversation_messages()`/TUI 渲染天然跳过 state 记录(render_record 对未知 role 返回空列表)。
- 预留:file-read 状态(read-before-edit、外部修改检测,原 `file_state.py` 的职责)将作为第二个 `StateExtension` 补入,前置条件是工具获取 per-agent 状态的机制定型(工具实例化 vs execute 上下文,与权限层一并设计);在那之前不要往工具层塞跨调用状态。realtime agent 无持久化状态语义(服务端持上下文),不接状态层。

## MCP client

- MCP 拆桥接与执行两层。`mcp.py` 的 `MCPClientManager` 只做桥接：TOML `[[mcp.servers]]` 配置（stdio/http 两种 transport），SDK 全 async 且 session 是 task 作用域 context manager——manager 用专用后台线程跑独立 event loop，管连接生命周期与 `mcp__<server>__<tool>` 命名，按连接构造 `MCPTool` 并入工具集；`start()` 阻塞至各 server 就绪或超时（失败记 warning 跳过，不阻断启动），组装后显式查重工具名（冲突抛 ConfigError）。执行在 `tools/mcp_tool.py` 的 `MCPTool`：经构造期注入的桥接侧 `acquire()` 取当前 (session, loop)（未连接/已关闭抛 RuntimeError），`run_coroutine_threadsafe(...).result(120s)` 桥进后台 loop，`result_to_text` 把 CallToolResult 翻译成文本（server 侧 isError → raise，由 wy-core 循环统一转 `Error: ...`）；该模块不 import MCP SDK。测试用 `sys.executable -c` 内联 FastMCP 脚本起真实 stdio server 做全链路验证。

## Skills（Agent Skills）

- 对齐 Anthropic Agent Skills 规范（agentskills.io / 官方文档）的应用侧实现，三级渐进披露：系统提示只列 name+description（L1，`SkillProvider`）；模型经 `skill` 工具加载 SKILL.md 正文（L2，`tools/skill.py` 的 `SkillTool`）；随包文件（参考文档/脚本）由模型用 read/glob/bash 工具按需取用（L3）。用户在输入框敲 `/name args` 不需要 TUI 特判——系统提示与工具描述已注明该语法即调用请求，模型自行调 `skill` 工具。
- `skills.py` 负责发现与渲染：每个 skill 是含 `SKILL.md` 的目录；`discover_skills(dirs)` 目录顺序即优先级、同名先者胜，skill 名取目录名（对齐 Claude Code，frontmatter `name` 仅展示用、当前忽略），description 缺省取正文首个非标题段落、清单截 1536 字符；`disable-model-invocation: true` 的 skill 不进系统提示但仍可经工具调用；坏 skill 记 warning 跳过不阻断启动。frontmatter 解析是手写 YAML 子集（顶层 `key: value`、引号值、`>`/`|` 块标量），列表/嵌套字段一律忽略，不引入 yaml 依赖。
- `render_skill` 每次调用重读 SKILL.md（编辑即时生效），替换 `$ARGUMENTS`（无占位符且有参数时文末追加 `ARGUMENTS: <args>`）与 `${CLAUDE_SKILL_DIR}`（兼容现有 Claude skills），结果头部携带 skill 目录供模型解析相对路径。
- 组装语义同 MCP：`create_agent(skills_dirs=...)` 显式传目录才启用（None 即关闭）；`bootstrap` 默认启用官方目录 `default_skills_dirs()`——`~/.claude/skills`（个人）优先于 `CWD/.claude/skills`（项目），对齐官方"个人覆盖项目"的优先级。`SkillTool` 与 `MCPTool` 同型：模块只有类、无模块级实例，不进 `DEFAULT_TOOLS`，由 factory 按发现结果构造并参与工具名查重。

## System prompt 组装

- `prompt_template.py` 的 `build_prompt(instruction, providers)` 统一组装 system prompt：instruction 首段 + 各 `BasePromptProvider` 段落（`ModelProvider`/`WorkspaceProvider`/`AgentMDProvider`/`SkillProvider`/`MemoryProvider`），`provide()` 返回 None 跳过且不得因来源缺失抛异常。`SkillProvider` 接收 factory 发现的 `Skill` 元组、只列 `listed` 条目；`default_prompt_providers(model, workspace, skills=())` 由 `create_agent` 传入 skills。注意与 `wy_core.Model` 是两套体系。工具 schema 经 API `tools` 参数传递，不拼入 system prompt。

## TUI 展示层

- `tui/` 是纯展示层：`render.py` 保持纯函数（`SessionRecord`/事件 → `MessageView` Markdown），新增展示逻辑放这里并配测试；`app.py` 只做界面组件与交互，不解析业务数据。TUI 只消费 `wy_core` 的 `AgentEvent` 事件与本包 `SessionRecord` 等公开数据类型，不触碰工具层。
- 思考过程、工具调用/输出、上下文压缩块默认收起、点击标题展开（`MessageView.collapsible_title` 非空即收起块，Textual `Collapsible` 承载）；工具参数/输出做动态围栏与展示截断（30 行 / 2000 字符），会话文件始终保留完整内容。
- 用量不进会话区，由输入框下方的用量条展示（`usage_bar_text`：输入/输出/缓存为累计值、上下文为最近一次请求的规模），在 `TurnEnd` 事件时刷新。
- `main.py` 是唯一入口：argparse → `bootstrap(session_id)` → 启动 TUI → finally `close()`。
