# wy-realtime-agent SDK 使用文档

基于 [wy-core](../wy-core) 的实时语音 agent SDK：由 Qwen-Audio 实时语音大模型经 WebSocket 全双工协议驱动，实现"麦克风流式录音 → 服务端 VAD/语义轮次判定 → 流式语音播放"的实时对话，并支持 function calling（内置 read 工具 + MCP 工具 + 自定义工具）、语音打断、回声抑制、后台文字指令注入与 JSONL 审计。

本文面向把本包当作 SDK 集成到自己应用里的开发者（含 AI 编码助手）。协议细节见包内 [realtime_llm_ws.md](realtime_llm_ws.md)；包内部实现约定见 [AGENTS.md](AGENTS.md)。

## 架构一览

```
你的应用
  └─ RealtimeAgent.run()  ← async 事件流（转写/工具/打断/会话结束）
       ├─ RealtimeClient   WebSocket 传输层（客户端事件编码 / 服务端事件解码）
       ├─ MicSource        麦克风采集：16kHz int16 单声道，100ms 一块
       ├─ SpeakerSink      扬声器播放：24kHz int16 单声道
       └─ Tool[]           wy_core.Tool 契约：内置 read + MCP 工具 + 自定义工具
```

关键设计：**对话上下文由服务端维护**，响应由服务端 VAD/语义轮次主动触发（推送式流）。因此本包不使用 `wy_core.Model`/`Agent`/`Session`，只复用 wy-core 的三样东西：

- `wy_core.Tool` —— 工具契约（自定义工具按它编写）；
- `wy_core.AuditLog` —— JSONL 审计日志；
- `wy_core.ToolCall` / `wy_core.ToolResult` —— 工具事件数据类（与本包事件共同组成 `RealtimeEvent`）。

## 安装

要求 Python >= 3.12。运行依赖：`wy-core`、`websockets`、`sounddevice`（PyPI wheel 自带 PortAudio）、`mcp`。

```bash
# 方式一：在本 uv workspace 内的其他包中依赖
#（对方 pyproject.toml）
# dependencies = ["wy-realtime-agent"]
# [tool.uv.sources]
# wy-realtime-agent = { workspace = true }

# 方式二：构建 wheel 给外部项目安装。
# 注意 wy-realtime-agent 按名字依赖 wy-core，wy-core 未发布到公共 index，
# 必须两个 wheel 一起构建、一起安装：
uv build --package wy-core
uv build --package wy-realtime-agent
pip install dist/wy_core-*.whl dist/wy_realtime_agent-*.whl
```

macOS 上首次运行需要给终端授予麦克风权限；无音频设备的环境（CI、服务器）见下文「无设备环境与测试替身」。

## 五分钟上手

### 1. 写配置文件 `config.toml`（放在进程启动目录）

```toml
[realtime]
url = "wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"  # wss base，不含 ?model=
api_key = "sk-..."                        # DashScope API Key
model = "qwen-audio-3.0-realtime-plus"
# 以下全部可选，含义见「配置参考」
# voice = "longanqian"
# instructions = "你是一个简洁的中文语音助手。"
# mode = "server_vad"
# echo_suppression = true
```

### 2. 跑起来

装好后直接用控制台入口体验：

```bash
wy-realtime-agent        # 或 workspace 内：uv run wy-realtime-agent
```

或在代码里消费事件流：

```python
import asyncio

from wy_core import ToolCall, ToolResult
from wy_realtime_agent import (
    AssistantTranscript,
    Interrupted,
    SessionEnded,
    UserTranscript,
    bootstrap,
)


async def main() -> None:
    agent = bootstrap()  # 读 CWD/config.toml；缺配置抛 ConfigError
    try:
        async for event in agent.run():
            match event:
                case UserTranscript(text=text):
                    print(f"[你] {text}")
                case AssistantTranscript(text=text):
                    print(f"[AI] {text}")
                case ToolCall(name=name, input=args):
                    print(f"[工具调用] {name} {args}")
                case ToolResult(name=name, is_error=is_error):
                    print(f"[工具结果] {name} {'失败' if is_error else '完成'}")
                case Interrupted():
                    print("(用户打断了回复)")
                case SessionEnded(reason=reason):
                    print(f"会话结束：{reason}")
    finally:
        agent.close()  # 必须调用：释放 MCP 连接等资源；幂等


asyncio.run(main())
```

`run()` 是 async 生成器：建连、发一次会话配置、起麦克风推流任务，然后持续产出事件；服务端关闭连接或传输失败时产出 `SessionEnded` 后正常结束（不抛异常），其余异常写一条 error 审计后原样上抛。

## 组装入口（factory）

外部一律经这两个入口创建 agent，不要手工拼装内部对象：

### `bootstrap(*, config_path: Path | None = None) -> RealtimeAgent`

一站式组装：读 TOML 配置（缺省 `CWD/config.toml`）中的 `[realtime]`（必填段）与 `[[mcp.servers]]`（可选），返回可直接 `run()` 的 agent。配置缺失/非法抛 `ConfigError`。

### `create_agent(...) -> RealtimeAgent`

可编程组装点，全部参数 keyword-only：

```python
def create_agent(
    *,
    config: RealtimeConfig,                # 必填，可用 load_realtime_config() 读文件，也可直接构造
    tools: Sequence[Tool] | None = None,   # 省略 = 内置 DEFAULT_TOOLS（只有 read）；传入即整体替换
    client: RealtimeClient | None = None,  # 注入替身（测试/自定义传输）
    mic: MicSource | None = None,          # 注入替身（自定义音频源）
    speaker: SpeakerSink | None = None,    # 注入替身（自定义播放端）
    mcp_config: Path | None = None,        # MCP 配置文件路径；None 即不启用 MCP
    audit: bool = True,                    # 默认写 CWD/.wy_audit/；False 关闭
) -> RealtimeAgent
```

注意：

- `tools` 是**整体替换**而非追加——想保留内置 read 需自己带上：`tools=(*DEFAULT_TOOLS, MyTool())`。
- `mcp_config` 传入时文件必须存在且可解析（无 `[mcp]` 段等价于零个 server）；MCP 工具与本地工具重名抛 `ConfigError`。
- `RealtimeConfig` 是 frozen dataclass，可不经文件直接构造：`RealtimeConfig(url=..., api_key=..., model=...)`，其余字段用默认值。

```python
from pathlib import Path

from wy_realtime_agent import create_agent, load_realtime_config
from wy_realtime_agent.tools import DEFAULT_TOOLS

agent = create_agent(
    config=load_realtime_config(Path("config.toml")),
    tools=(*DEFAULT_TOOLS, WeatherTool()),   # 自定义工具见下文
    mcp_config=Path("config.toml"),          # 与 [realtime] 共用同一文件即可
)
```

## 配置参考

### `[realtime]` 段

| 键 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `url` | str | ✅ | — | WebSocket base 地址（**不含** `?model=` 查询串，客户端自动拼接） |
| `api_key` | str | ✅ | — | DashScope API Key（占位值 `"your-api-key"` 会被拒绝） |
| `model` | str | ✅ | — | 如 `qwen-audio-3.0-realtime-plus` |
| `voice` | str | | `"longanqian"` | 音色；协议规定仅首次会话配置生效，会话中途改不了 |
| `instructions` | str | | `""` | 系统指令（人设/风格），空串则不发送 |
| `mode` | str | | `"server_vad"` | `"server_vad"`（能量 VAD + 静音判停）或 `"smart_turn"`（服务端语义轮次判定，非有效轮次的语音不触发回答） |
| `vad_threshold` | float | | `0.5` | 仅 server_vad，取值 `[-1.0, 1.0]` |
| `vad_silence_ms` | int | | `800` | 仅 server_vad，取值 `[200, 6000]` |
| `echo_suppression` | bool | | `true` | `true` = 免耳机模式：AI 说话/播放期间闭麦（**不支持语音打断**）；`false` = 耳机模式：能量门限滤回声，支持语音打断 |
| `max_history_turns` | int | | `20` | 服务端保留的历史轮数，取值 `[1, 50]` |

所有字段均做全量校验，非法值抛 `ConfigError`（带中文错误信息）。

### `[[mcp.servers]]` 段（可选，每个表一个 server）

| 键 | 类型 | 说明 |
|---|---|---|
| `name` | str | server 名，全局唯一；其工具以 `mcp__<name>__<tool>` 并入工具集 |
| `transport` | str | `"stdio"` 或 `"http"` |
| `command` / `args` / `env` | str / [str] / {str:str} | stdio 必填 command；env 与默认安全环境变量合并 |
| `url` / `headers` | str / {str:str} | http 必填 url |

```toml
[[mcp.servers]]
name = "fetch"
transport = "stdio"
command = "uvx"
args = ["mcp-server-fetch"]

[[mcp.servers]]
name = "docs"
transport = "http"
url = "https://example.com/mcp"
headers = { Authorization = "Bearer xxx" }
```

MCP 语义：`create_agent` 时后台线程建连（30s 超时），**连接失败的 server 记 warning 并跳过，不阻止启动**；单次工具调用超时 120s；`agent.close()` 统一断开。MCP server 以当前用户权限运行/访问，无沙箱。

## 事件参考

`run()` 产出 `RealtimeEvent`，它是六个 dataclass 的联合类型：

| 事件 | 字段 | 何时产出 |
|---|---|---|
| `UserTranscript` | `text: str` | 一段用户语音的转写完成 |
| `AssistantTranscript` | `text: str` | 一次模型语音回复的转写完成 |
| `ToolCall`（来自 wy_core） | `id, name, input: dict` | 某个工具即将执行 |
| `ToolResult`（来自 wy_core） | `id, name, content: str, is_error: bool` | 某个工具执行完毕 |
| `Interrupted` | `response_id: str \| None` | 用户语音打断了正在进行的回复（仅耳机模式可能发生） |
| `SessionEnded` | `reason: str` | 连接结束（服务端关闭或传输错误），`run()` 以此收尾 |

事件只是**透出**给宿主展示/记录用；音频播放、工具执行、结果回写全部由 agent 内部完成，宿主不需要（也不应该）对事件做出协议层响应。

## 自定义工具

按 `wy_core.Tool` 契约编写：三个类属性 + 一个同步 `execute`。

```python
from wy_core import Tool


class WeatherTool(Tool):
    name = "get_weather"                       # 工具唯一名
    description = "查询指定城市当前天气。"        # 给模型看的用途说明
    parameters = {                             # 入参 JSON Schema
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名，如 '北京'"},
        },
        "required": ["city"],
        "additionalProperties": False,
    }

    def execute(self, input: dict) -> str:
        # 同步方法，允许阻塞 IO（agent 经 asyncio.to_thread 调用，不会冻结事件循环）。
        # 失败直接 raise：agent 统一转 "Error: ..." 文本回给模型，不会中断会话。
        return f"{input['city']}：晴，26°C"
```

执行语义（与 wy-core 对齐）：

- 一次响应可含多个 function_call，agent **先收集、响应结束后按序顺序执行**，全部结果回写完只触发一次二轮推理；
- 工具抛任何异常 → 结果变为 `Error: <异常信息>`（`is_error=True`）回给模型，会话继续；
- 模型调用了不存在的工具名 → `Error: unknown tool <name>`，同样不中断；
- 被语音打断（cancelled）的响应，其未执行的工具调用直接丢弃。

内置工具只有一个：`read`（读本地文本文件，`file_path` 必填，支持 `offset`/`limit` 分页，相对路径按进程 CWD 解析）。

## 后台文字指令注入

`await agent.send_user_text(text)` 把一条文字指令以用户身份注入对话并让模型立即执行——适合"边语音对话、边由宿主程序下发任务"的场景。

语义：

- **空闲时**（既不在听也不在答）：立即注入 + 触发响应；
- **忙碌时**（用户在说话 / 模型在回答 / 响应待建）：自动入队，待回合真正结束后按序补发全部排队指令、只触发一次响应；打断不丢队列；
- 必须在 `run()` 所在的事件循环中调用；尚未建连时抛 `RealtimeError`。

```python
async def main() -> None:
    agent = bootstrap()
    try:
        consume = asyncio.create_task(consume_events(agent))  # 后台消费 run() 事件流
        await asyncio.sleep(2)  # 示意：等建连完成；严谨做法是收到首个事件后再注入
        await agent.send_user_text("请读取 README.md 并口头总结要点")
        await consume
    finally:
        agent.close()
```

## 打断与回声抑制

- `echo_suppression = true`（默认，免耳机）：AI 回复/播放期间麦克风数据直接丢弃，播放结束后再冷却 0.5s——外放不会自触发，但**用户无法用语音打断**。
- `echo_suppression = false`（耳机模式）：回复期间用能量门限过滤低能量回声/噪声，高能量人声照常上行——支持语音打断。打断发生时 agent 立即清空播放队列（残余播放不超过 100ms）、取消进行中的响应，并产出 `Interrupted` 事件。

## 审计日志

默认开启：`create_agent(audit=True)`（或直接构造 `RealtimeAgent` 时省略 `audit`）即写入 `CWD/.wy_audit/<UTC时间戳>-<8位随机>.jsonl`，agent 构造瞬间就会落第一条记录。每行一个 JSON 对象：`{"ts": <UTC ISO8601>, "kind": ..., **data}`，中文原样存储。

kind 全集：`agent_start` / `session_update` / `user_transcript` / `assistant_transcript` / `user_text` / `interrupted` / `tool_call` / `tool_result` / `error`。语义级留痕，不逐音频/文本 delta 记录。

关闭：`create_agent(..., audit=False)`；或直接构造 `RealtimeAgent(..., audit=None)`（也可传入自定义 `wy_core.AuditLog(path)` 指定落盘位置）。

## 无设备环境与测试替身

`sounddevice` 只在缺省流工厂里懒加载——注入替身即完全不触碰音频设备与真实网络，适合 CI 与自定义音频链路：

```python
from wy_realtime_agent import MicSource, RealtimeClient, SpeakerSink, create_agent

client = RealtimeClient(url, api_key, model, connect=my_connect)  # 假 ws 只需 send/close/async 迭代
mic = MicSource(stream_factory=my_input_stream)      # 流对象需 start/stop/read(frames)
speaker = SpeakerSink(stream_factory=my_output_stream)  # 流对象需 start/stop/write(chunk)

agent = create_agent(config=config, client=client, mic=mic, speaker=speaker, audit=False)
```

音频规格由协议定死，替身必须遵守：输入 16kHz、输出 24kHz，均为 int16 单声道 PCM，100ms 一块（输入块 3200 字节）。相关常量从 `wy_realtime_agent.audio` 导入：`INPUT_RATE` / `OUTPUT_RATE` / `CHUNK_MS` / `INPUT_CHUNK_BYTES` / `OUTPUT_CHUNK_BYTES`。

## 公开 API 一览

均从包根导入：`from wy_realtime_agent import ...`

| 导出 | 说明 |
|---|---|
| `bootstrap` / `create_agent` | 组装入口（见上文） |
| `RealtimeAgent` | 核心编排：`run()` 产出事件流；`send_user_text()` 注入指令；`close()` 释放资源 |
| `RealtimeEvent` | 事件联合类型 |
| `UserTranscript` / `AssistantTranscript` / `Interrupted` / `SessionEnded` | 本包事件 dataclass（`ToolCall`/`ToolResult` 从 `wy_core` 导入） |
| `RealtimeConfig` / `load_realtime_config` | `[realtime]` 配置模型与解析 |
| `MCPServerConfig` / `load_mcp_config` / `MCPClientManager` | MCP 配置与桥接层 |
| `ConfigError` | 配置错误（`RuntimeError` 子类） |
| `RealtimeError` | 实时连接错误：握手、传输或异常关闭（`RuntimeError` 子类） |
| `RealtimeClient` / `build_session_config` | 底层传输客户端与 session.update 载荷组装（高级用法/测试用） |
| `MicSource` / `SpeakerSink` | 流式音频 IO |
| `DEFAULT_TOOLS` | 内置工具元组（目前只有 `read`） |

## 集成检查单（给 AI 的注意事项）

1. **`agent.close()` 必须放在 `finally` 里调用**——释放 MCP 后台线程与连接；幂等，多次调用安全。
2. **单实例不支持并发 `run()`；不做自动重连**——连接断开后 `run()` 以 `SessionEnded` 正常结束，需要重连就重新 `bootstrap()`/`create_agent()` 一个新实例再 `run()`。
3. **`send_user_text` 必须在 `run()` 所在事件循环调用**；未建连时抛 `RealtimeError`；忙碌时自动排队，不会报错也不会立即生效。
4. **所有默认路径按进程 CWD 解析**——`bootstrap` 的 `config.toml`、审计的 `.wy_audit/`、read 工具的相对路径。部署时用显式参数覆盖，不要依赖启动目录。
5. **`create_agent(tools=...)` 是整体替换**——想保留内置 read 要写 `tools=(*DEFAULT_TOOLS, ...)`；工具重名抛 `ConfigError`。
6. **工具 `execute` 是同步方法**，经 `asyncio.to_thread` 顺序执行；内部不要自建事件循环；长任务自行控制超时（MCP 工具内置 120s）。
7. **工具异常不会向上传播**——统一转 `Error: ...` 文本回给模型；不要依赖异常做流程控制。
8. **无音频设备的环境必须注入 mic/speaker 替身**，否则 `run()` 启动音频时 sounddevice 报错。
9. **`echo_suppression=true` 下用户无法语音打断**；需要打断体验就配 `false` 并要求用户戴耳机。
10. **`voice` 与 turn_detection 只在首次会话配置生效**——运行中改不了，换音色/模式需要新建连接。
11. 审计默认开启且**构造 agent 即开始写文件**——不希望在 CWD 留痕就显式 `audit=False`。
