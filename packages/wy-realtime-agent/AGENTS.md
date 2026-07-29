# wy-realtime-agent Module Conventions

包定位、依赖方向（`wy_realtime_agent → wy_core`，app 包之间互不依赖）、import 规则与路径解析约定见仓库根 `AGENTS.md`；`Tool`/`AuditLog` 等核心契约语义见 `packages/wy-core/AGENTS.md`。本文件是本应用包内各模块的详细约定。

## 与 wy-core 的复用边界

- 实时协议（协议参考 `realtime_llm_ws.md`，Qwen-Audio realtime，OpenAI Realtime 风格事件）是**服务端维护对话上下文、VAD 主动触发响应的推送式全双工流**，与 `wy_core.Model.stream`（整历史进、单流出的拉取式契约）和 `Agent.run`（文本输入回合循环）不匹配，因此本包**不使用** `wy_core.Model`/`Agent`/`Session`，由 `agent.py` 的 `RealtimeAgent` 自成事件循环；也不做本地会话镜像/SessionStore（服务端持上下文，审计日志已完整留痕）。
- 实际复用 wy-core 的三样：`Tool` 契约（内置 read 工具与 MCP 工具都按它编写；执行语义完全对齐——同步 `execute` 经 `asyncio.to_thread` 调用、异常/未知工具名转 `Error: ...` 文本不中断会话）、`AuditLog`（语义级 JSONL 审计，默认 CWD/.wy_audit/，不逐 delta 留痕）、`ToolCall`/`ToolResult` 事件数据类（与本包 `UserTranscript`/`AssistantTranscript`/`Interrupted`/`SessionEnded` 组成 `RealtimeEvent` 联合）。

## 模块职责

- `config.py`：`[realtime]` 段解析（url/api_key/model 必填 + voice/instructions/mode/vad 参数/echo_suppression/max_history_turns 可选，全量校验）与 `[[mcp.servers]]` 解析。后者是 wy-coding-agent `config.py` 的复刻件。
- `protocol.py`：纯传输层。`RealtimeClient` 只管建连（`{url}?model=`、Bearer 头）、客户端事件编码发送（自动补自增 `event_id`）与服务端事件 JSON 解码迭代；连接工厂可注入（测试用假 ws，只需 `send`/`close`/async 迭代）。传输/握手/异常关闭统一收敛 `RealtimeError`；服务端正常关闭表现为 `events()` 自然结束。`build_session_config` 纯函数把 `RealtimeConfig` + 工具集组装为 session.update 载荷（`wy_core.Tool` → `{"type":"function","function":{...}}`）；voice/turn_detection 协议上仅首次 session.update 生效，本包建连后只发这一次，天然满足。
- `audio.py`：流式音频 IO，规格由协议定死（输入 16k / 输出 24k，int16 单声道，100ms 分块常量）。`MicSource` 后台线程阻塞读设备入队、队满丢最旧保实时；`SpeakerSink` play() 先按 100ms 切块再入队、后台线程顺序写，`clear()` 打断残余不超一块，`is_playing()` 供回声抑制。sounddevice **只在缺省流工厂内懒加载**，注入假流即完全不触碰设备；PortAudio 流的所有调用都留在各自后台线程内（先收线程再关流），不得跨线程操作设备句柄。
- `agent.py`：`RealtimeAgent.run()` 是核心编排——建连 → 发一次 session.update → 起 `_send_audio` 任务 + 消费服务端事件流，产出 `RealtimeEvent`；服务端关闭/传输失败以 `SessionEnded` 收尾，其余异常写 error 审计后上抛，出口统一停任务/停音频/关连接。约定的关键语义：
  - **Function calling 是收集式**（刻意不同于官方 demo 的"逐个 arguments.done 即执行+每次 response.create"）：一次响应可含多个 function_call，先收集，`response.done` 且 status 非 cancelled 时统一顺序执行、逐个回写 `function_call_output`，**最后只发一次** `response.create`；被打断（cancelled）的响应丢弃未执行调用。arguments.done 与 response.done 间隔毫秒级，延迟代价可忽略。
  - **打断**：`speech_started` 即 `speaker.clear()`；若在回复中则再发 `response.cancel` 并抑制残余 `audio.delta` 直到下一个 `response.created`。
  - **回声抑制**（`_send_audio`）：`echo_suppression=true` 回复/播放期间闭麦 + 结束后 0.5s 冷却，不支持打断；`false`（耳机模式）用能量门限（阈值 500，`_audio_energy` 自实现，3.13 无 audioop）滤回声、高能量语音照发以支持打断。
  - **后台指令注入**：公开方法 `send_user_text()` 把后台文字指令经 `conversation.item.create`（`role=user` + `input_text`）注入并紧跟一次 `response.create` 让模型立即执行。仅空闲时发出，忙碌一律入队：在听（`speech_started` 起，到该轮 `response.created` 或 ambient completed 判非轮次止）、在答（`response.created`→`response.done`）、响应待建（客户端已发 `response.create` 而 `response.created` 未到的防抖窗口，`error` 事件兜底清除）都算忙。回合真正结束（无待执行工具的 `response.done`，或 ambient completed）时按序补发全部排队指令、只触发一次 `response.create`；带工具的回合等二轮结束，打断不丢队列。须在 `run()` 所在事件循环调用，未建连时抛 `RealtimeError`。
  - 审计 kinds：`agent_start`/`session_update`/`user_transcript`/`assistant_transcript`/`user_text`/`interrupted`/`tool_call`/`tool_result`/`error`。
  - 未知服务端事件（含 voiceprint 注册事件）一律静默忽略；smart_turn 的 ambient 转写不透出事件，仅 `conversation.item.ambient_audio_transcription.completed` 用于结束"在听"状态；`error` 事件只记审计不断流（服务端致命错误会随后关连接）。
- `mcp.py` 与 `tools/mcp_tool.py`：wy-coding-agent 同名模块的复刻件（仅 import 路径不同）——后台线程独立 event loop 桥接、`mcp__<server>__<tool>` 命名、连接失败记 warning 跳过；`MCPTool.execute` 同步、`run_coroutine_threadsafe` 桥调（120s 超时）。`tools/read.py` 复刻 ReadTool 并内联了原 `files.py` 中它用到的辅助。**修改任一复刻件时必须评估 wy-coding-agent 侧的同名实现是否要同步改**（反之亦然）。
- `tools/__init__.py`：只有 read 一个内置工具，`DEFAULT_TOOLS = (READ,)` 显式列出，不做 wy-coding-agent 那套自动扫描。
- `factory.py`：组装唯一入口，两层：`bootstrap(config_path=None)` 读 CWD/config.toml（`[realtime]` + `[[mcp.servers]]` 共用同一文件）一站式组装；`create_agent(config, tools=None, client=None, mic=None, speaker=None, mcp_config=None, audit=True)` 为可编程组装点（client/mic/speaker 可注入替身，工具名冲突抛 ConfigError，MCP closer 注入 agent 由 `close()` 释放）。外部一律经这两个入口创建 agent。
- `main.py`：唯一控制台入口：argparse → `bootstrap()` → `asyncio.run` 消费事件打印（`[你]`/`[AI]`/`[工具]`/打断/会话结束）→ KeyboardInterrupt 优雅退出 → finally `close()`。

## v1 明确不做（后续可加）

- 断线自动重连/指数退避：连接关闭即 `SessionEnded` 优雅退出。
- 工具执行期间的新打断不取消执行中的工具，结果照常回写，由服务端裁决新一轮。
- push-to-talk 手动模式；smart_turn 的 ambient 转写与说话人增强事件透出。

## 测试

- 测试在 `tests/`，不引入 pytest-asyncio：async 流程用 `realtime_helpers.run_agent`（内部 `asyncio.run`，可传 async `on_event` 回调在事件产出点中途驱动 agent，如注入文字消息）收集事件。`FakeWebSocket` 以脚本化服务端事件驱动全链路（`WaitFor` 控制项用于与发送任务同步，谓词收 ws 本体）；`FakeMic`/`FakeSpeaker` 与 `test_realtime_audio.py` 的假设备流保证测试不碰真实网络与音频设备。
- 测试文件名跨包必须唯一（本包统一 `test_realtime_*` 前缀，辅助为 `realtime_helpers.py`）。涉及默认审计的用例必须 `monkeypatch.chdir(tmp_path)`；其余一律 `audit=None`/`audit=False`。
- `test_realtime_mcp.py` 用 `sys.executable -c` 内联 FastMCP 脚本起真实 stdio server 做全链路验证（与复刻源保持同等测试强度）。
