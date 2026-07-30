# wy-realtime-agent Module Conventions

包定位、依赖方向（`wy_realtime_agent → wy_core`，app 包之间互不依赖）、import 规则与路径解析约定见仓库根 `AGENTS.md`；`Tool`/`AuditLog`/realtime 契约等核心语义见 `packages/wy-core/AGENTS.md`。本文件是本应用包内各模块的详细约定。

本包 `README.md` 是面向外部集成方（含 AI）的 SDK 使用文档，经 pyproject 的 `readme` 字段随 wheel 元数据发布：公开导出、配置字段、事件语义或集成约定变更时必须同步更新它。

## 与 wy-core 的复用边界

- 实时协议（协议参考 `realtime_llm_ws.md`，Qwen-Audio realtime，OpenAI Realtime 风格事件）是**服务端维护对话上下文、VAD 主动触发响应的推送式全双工流**，与 `wy_core.Model.stream`/`Agent.run`/`Session` 的拉取式回合契约不匹配，走 wy-core 的 realtime 契约体系。**实时编排循环在 `wy_core.RealtimeAgent`**（打断、回声抑制、收集式 function calling、`send_user_text` 排队语义与审计 kinds 详见 `packages/wy-core/AGENTS.md`），本包只提供实现件与组装：`QwenRealtimeModel`（`wy_core.RealtimeModel` 的 wire 翻译层）、`MicSource`/`SpeakerSink`（`wy_core.AudioSource`/`AudioSink` 的 sounddevice 实现）、config/MCP/factory/main。
- 复用 wy-core 的：realtime 契约全家（`RealtimeModel` + 类型化事件词汇、`RealtimeAgent` + `RealtimeEvent`、`AudioSource`/`AudioSink`、`RealtimeError`）、`Tool` 契约（内置 read 工具与 MCP 工具都按它编写）、`AuditLog` 与 `ToolCall`/`ToolResult`。包根 `__init__` 把编排事件类型（`RealtimeAgent`/`RealtimeEvent`/`UserTranscript`/`AssistantTranscript`/`Interrupted`/`SessionEnded`/`RealtimeError`）从 wy_core re-export 保持兼容。不做本地会话镜像/SessionStore（服务端持上下文，审计日志已完整留痕）。

## 模块职责

- `config.py`：`[realtime]` 段解析（url/api_key/model 必填 + voice/instructions/mode/vad 参数/echo_suppression/max_history_turns 可选，全量校验）与 `[[mcp.servers]]` 解析。后者是 wy-coding-agent `config.py` 的复刻件。
- `protocol.py`：纯传输层。`RealtimeClient` 只管建连（`{url}?model=`、Bearer 头）、客户端事件编码发送（自动补自增 `event_id`）与服务端事件 JSON 解码迭代；连接工厂可注入（测试用假 ws，只需 `send`/`close`/async 迭代）。传输/握手/异常关闭统一收敛 `wy_core.RealtimeError`（本模块 re-export）；服务端正常关闭表现为 `events()` 自然结束。`build_session_config(config, tools, *, system=None)` 纯函数组装 session.update 载荷（`wy_core.Tool` → `{"type":"function","function":{...}}`；instructions 来自 `system` 参数，不再读 config.instructions）；voice/turn_detection 协议上仅首次 session.update 生效，建连后只发这一次，天然满足。
- `qwen.py`：`QwenRealtimeModel`（`wy_core.RealtimeModel` 实现），组合 `RealtimeClient`（`client` 可注入替身），`name = config.model`。发送侧契约方法 → wire 事件：`send_user_text` → `conversation.item.create`（`role=user` + `input_text`）、`send_tool_result` → `conversation.item.create`（`function_call_output`）、`update_session` 用 `build_session_config` 组装并**返回实发载荷**（供 core 审计）、其余直通。接收侧 `_translate` 一个 wire dict → 一个类型化事件：base64 解码 `AudioDelta`、arguments JSON 宽松解析（残缺/非对象回退空 dict）、`conversation.item.ambient_audio_transcription.completed` → `TurnDiscarded`（smart_turn 判非轮次，ambient 转写不透出）；词汇之外的 wire 事件（session.created、voiceprint 注册等）返回 None 静默忽略。
- `audio.py`：`wy_core.AudioSource`/`AudioSink` 的 sounddevice 实现，规格由协议定死（输入 16k / 输出 24k，int16 单声道，100ms 分块常量）。`MicSource` 后台线程阻塞读设备入队、队满丢最旧保实时；`SpeakerSink` play() 先按 100ms 切块再入队、后台线程顺序写，`clear()` 打断残余不超一块，`is_playing()` 供回声抑制。sounddevice **只在缺省流工厂内懒加载**，注入假流即完全不触碰设备；PortAudio 流的所有调用都留在各自后台线程内（先收线程再关流），不得跨线程操作设备句柄。
- `mcp.py` 与 `tools/mcp_tool.py`：wy-coding-agent 同名模块的复刻件（仅 import 路径不同）——后台线程独立 event loop 桥接、`mcp__<server>__<tool>` 命名、连接失败记 warning 跳过；`MCPTool.execute` 同步、`run_coroutine_threadsafe` 桥调（120s 超时）。`tools/read.py` 复刻 ReadTool 并内联了原 `files.py` 中它用到的辅助。**修改任一复刻件时必须评估 wy-coding-agent 侧的同名实现是否要同步改**（反之亦然）。
- `tools/__init__.py`：只有 read 一个内置工具，`DEFAULT_TOOLS = (READ,)` 显式列出，不做 wy-coding-agent 那套自动扫描。
- `factory.py`：组装唯一入口，两层：`bootstrap(config_path=None)` 读 CWD/config.toml（`[realtime]` + `[[mcp.servers]]` 共用同一文件）一站式组装；`create_agent(config, tools=None, client=None, mic=None, speaker=None, mcp_config=None, audit=True)` 为可编程组装点（对外签名不变），内部组装 `QwenRealtimeModel(config, client=client)` + `wy_core.RealtimeAgent`——config 的 instructions/echo_suppression 分别映射为 agent 的 system/echo_suppression，mic/speaker 缺省在此构造真设备件，工具名冲突抛 ConfigError，MCP closer 注入 agent 由 `close()` 释放。外部一律经这两个入口创建 agent。
- `main.py`：唯一控制台入口：argparse → `bootstrap()` → `asyncio.run` 消费事件打印（`[你]`/`[AI]`/`[工具]`/打断/会话结束）→ KeyboardInterrupt 优雅退出 → finally `close()`；事件类型从 `wy_core` 导入。

## v1 明确不做（后续可加）

- 断线自动重连/指数退避：连接关闭即 `SessionEnded` 优雅退出（core 编排行为）。
- 工具执行期间的新打断不取消执行中的工具，结果照常回写，由服务端裁决新一轮（core 编排行为）。
- push-to-talk 手动模式；smart_turn 的 ambient 转写与说话人增强事件透出（`_translate` 对 ambient completed 只产出 `TurnDiscarded`，转写文本不透出）。

## 测试

- 测试在 `tests/`，不引入 pytest-asyncio：async 流程用 `realtime_helpers.run_agent`（内部 `asyncio.run`，可传 async `on_event` 回调在事件产出点中途驱动 agent）收集事件。`make_agent` 组装 FakeWebSocket → `RealtimeClient` → `QwenRealtimeModel` → `wy_core.RealtimeAgent` 全链路；`FakeWebSocket` 以脚本化服务端 wire 事件驱动（`WaitFor` 控制项用于与发送任务同步，谓词收 ws 本体）；`FakeMic`/`FakeSpeaker` 与 `test_realtime_audio.py` 的假设备流保证测试不碰真实网络与音频设备。
- **编排语义用例在 wy-core**（`tests/test_core_realtime_agent.py`，`FakeRealtimeModel` 类型化事件驱动）；本包 `test_realtime_qwen.py` 只测翻译层（收发两个方向的 wire ↔ 类型化映射、宽松 arguments 解析、未知事件忽略）与少量全链路冒烟。改编排行为去 core 加用例，改 wire 翻译在本包加用例。
- 测试文件名跨包必须唯一（本包统一 `test_realtime_*` 前缀，辅助为 `realtime_helpers.py`）。涉及默认审计的用例必须 `monkeypatch.chdir(tmp_path)`；其余一律 `audit=None`/`audit=False`。
- `test_realtime_mcp.py` 用 `sys.executable -c` 内联 FastMCP 脚本起真实 stdio server 做全链路验证（与复刻源保持同等测试强度）。
