# wy-core SDK 使用文档

零依赖（纯标准库）的极简 agent core runtime：统一消息词汇、`Model`/`Tool` 抽象契约、内存态会话与自动上下文压缩、JSONL 审计日志、完整的 agent 工具循环，以及端到端语音的 realtime 契约（`RealtimeModel`/`RealtimeAgent`/音频抽象）。**继承 `Model` 适配任意 LLM API、继承 `Tool` 添加工具，即得到一个完整的 harness agent；继承 `RealtimeModel` 适配厂商实时语音协议，即得到一个完整的实时语音 agent。**

本文面向把本包当作 SDK 使用的开发者（含 AI 编码助手）。包内部实现约定见 [AGENTS.md](AGENTS.md)；两个参考消费方：[wy-coding-agent](../wy-coding-agent)（文本回合式完整用法：`AnthropicModel` 模型实现、本地工具、持久化、TUI）、[wy-realtime-agent](../wy-realtime-agent)（realtime 契约完整用法：`QwenRealtimeModel` 协议翻译 + sounddevice 音频）。

## 架构一览

```
Agent.run(user_input)  ← async 事件流（增量/工具/压缩/回合结束）
  ├─ Model    抽象契约：你继承它适配厂商 API（stream → TextDelta/ThinkingDelta/ModelEnd）
  ├─ Tool[]   抽象契约：你继承它添加工具（name/description/parameters + 同步 execute）
  ├─ Session  内存态消息历史 + 用量统计 + 自动上下文压缩
  └─ AuditLog JSONL 审计（默认开启，写 CWD/.wy_audit/）

RealtimeAgent.run()    ← async 事件流（转写/工具/打断/会话结束），见「Realtime」章
  ├─ RealtimeModel      抽象契约：你继承它适配厂商实时协议（wire 事件 ↔ 类型化事件翻译）
  ├─ Tool[]             与文本侧同一套工具契约
  ├─ AudioSource/Sink   流式音频 IO 抽象（麦克风/扬声器）
  └─ AuditLog           同一套 JSONL 审计
```

模块依赖方向：文本回合侧 `agent → session/log/model/tool → message`、实时侧 `realtime_agent → realtime_model/audio/log/tool`（`realtime_model → tool`），无环。全库只用一套消息词汇（`message` 模块，Anthropic 风格中立块）；非 Anthropic 后端由你的 `Model` 实现自行完成两侧格式翻译。

## 安装

要求 Python >= 3.10。**零运行时依赖**，可放心嵌入任何环境。

```bash
# 方式一：在本 uv workspace 内的其他包中依赖
#（对方 pyproject.toml）
# dependencies = ["wy-core"]
# [tool.uv.sources]
# wy-core = { workspace = true }

# 方式二：构建 wheel 给外部项目安装（无依赖，单个 wheel 即可）
uv build --package wy-core
pip install dist/wy_core-*.whl
```

## 五分钟上手

下面是一个**完整可运行**的最小示例：用一个不调任何 API 的演示 Model 先把接线跑通，之后替换为真实厂商实现即可。

```python
import asyncio

from wy_core import (
    Agent,
    Message,
    Model,
    ModelEnd,
    TextBlock,
    TextDelta,
    ToolCall,
    ToolResult,
    Tool,
    TurnEnd,
    Usage,
)


class EchoModel(Model):
    """演示实现：原样回显用户输入，不调用工具。"""

    name = "echo-demo"

    async def stream(self, messages, *, system=None, tools=None):
        reply = f"你说了：{messages[-1].text}"
        for ch in reply:                     # 增量只供实时渲染
            yield TextDelta(text=ch)
        yield ModelEnd(                      # 权威内容在这里，必须是最后一个事件
            message=Message(role="assistant", content=[TextBlock(reply)]),
            usage=Usage(input_tokens=10, output_tokens=len(reply)),
            stop_reason="end_turn",
        )


async def main() -> None:
    agent = Agent(model=EchoModel(), system="你是简洁的助手", audit=None)
    async for event in agent.run("你好"):
        match event:
            case TextDelta(text=text):
                print(text, end="", flush=True)   # 实时渲染正文
            case ToolCall(name=name, input=args):
                print(f"\n[工具调用] {name} {args}")
            case ToolResult(name=name, is_error=is_error):
                print(f"[工具结果] {name} {'失败' if is_error else '完成'}")
            case TurnEnd(usage=usage, context_tokens=ctx):
                print(f"\n(回合结束：累计 {usage.output_tokens} 输出 tokens，上下文 {ctx})")


asyncio.run(main())
```

多回合对话：**复用同一个 Agent 实例连续调用 `run()`** 即可，`Session` 会保留历史（单实例不支持并发 `run`）。

## Model 契约（唯一的重活）

接入真实 LLM 就是实现一个 `Model` 子类。签名固定：

```python
class Model(ABC):
    name: str = ""  # 模型标识，仅用于展示与审计

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: Sequence[Tool] | None = None,
    ) -> AsyncIterator[ModelEvent]: ...
```

实现方义务（违反任何一条都会破坏 Agent 循环）：

1. **厂商参数全部在构造期注入**（模型名、鉴权、max_tokens、endpoint、temperature……），`stream` 只接收逐轮变化的会话状态。
2. **增量事件（`TextDelta`/`ThinkingDelta`）仅供 UI 实时渲染**，权威内容在 `ModelEnd.message`：实现方负责把厂商流组装为**完整的 assistant `Message`**（thinking/text/tool_use 块；thinking signature 等厂商特有字段也在此处填入）。核心刻意不做 JSON 增量拼装。
3. **每次流必须恰好以一个 `ModelEnd` 结束，且它是最后一个事件**（缺失会导致 Agent 抛 `ModelError`）。
4. 模型请求执行工具时 **`stop_reason` 必须为 `"tool_use"`**（驱动工具循环），其余取值一律视为回合结束。
5. 传输 / 解码 / 流内厂商错误**一律 raise `ModelError`**。
6. `tools` 只读取 `name`/`description`/`parameters` 生成厂商 schema，**永不调用 `execute`**；`None` 表示本次请求不带工具。

真实适配的骨架（Anthropic 风格伪代码，完整参考实现见 wy-coding-agent 的 `AnthropicModel`）：

```python
class MyModel(Model):
    name = "my-llm"

    def __init__(self, api_key: str, model_id: str, max_tokens: int = 8192) -> None:
        self._client = VendorSDK(api_key=api_key)
        self._model_id = model_id
        self._max_tokens = max_tokens

    async def stream(self, messages, *, system=None, tools=None):
        request = self._to_wire(messages, system, tools)   # 统一词汇 → 厂商格式
        blocks: list[Block] = []                           # 边转发增量，边本地累积完整块
        try:
            async for chunk in self._client.stream(request):
                if chunk.is_text_delta:
                    yield TextDelta(text=chunk.text)
                    ...  # 累积进当前 TextBlock
                elif chunk.is_tool_use_done:
                    blocks.append(ToolUseBlock(id=..., name=..., input=...))
        except VendorError as exc:
            raise ModelError(f"请求失败：{exc}") from exc
        yield ModelEnd(
            message=Message(role="assistant", content=blocks),
            usage=Usage(input_tokens=..., output_tokens=...),
            stop_reason="tool_use" if any(isinstance(b, ToolUseBlock) for b in blocks) else "end_turn",
        )
```

## Tool 契约

三个类属性 + 一个同步 `execute`：

```python
from wy_core import Tool


class ReadFile(Tool):
    name = "read_file"                        # 工具唯一名
    description = "读取文本文件内容"            # 给模型看的用途说明
    parameters = {                            # 入参 JSON Schema
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def execute(self, input: dict) -> str:
        # 同步方法，允许阻塞 IO（Agent 经 asyncio.to_thread 执行，不冻结事件循环）。
        # 失败直接 raise：Agent 统一转 "Error: ..." 的 tool_result（is_error=True），不中断回合。
        with open(input["path"], encoding="utf-8") as f:
            return f.read()
```

执行语义：

- 一条 assistant 消息含多个 tool_use 时，Agent **并发执行**全部调用（`asyncio.gather`），但事件产出与结果回填顺序仍与调用顺序一致——工具实现需可被并发调用（线程安全）；
- 工具抛任何异常 → `Error: <异常信息>`（`is_error=True`）回给模型，回合继续；
- 模型调用了不存在的工具名 → `Error: unknown tool <name>`，同样不中断；
- `parameters` 的 JSON Schema 由你的 `Model` 实现映射到厂商字段（Anthropic 的 `input_schema`、OpenAI 的 `parameters`）。

## 工具审批 Hook

`ToolHook` 允许在工具执行前对每次调用进行审批（批准或拒绝），适用于实现用户确认对话框、权限策略等场景。`Agent` 与 `RealtimeAgent` 均支持。

```python
from wy_core import ToolApproval, ToolCall, ToolHook


class MyApproval(ToolHook):
    """弹对话框让用户确认每个工具调用。"""

    async def approve(self, call: ToolCall) -> ToolApproval:
        # call.id / call.name / call.input   —— 工具调用信息
        # 返回 ToolApproval(allowed=True)       批准执行
        # 返回 ToolApproval(allowed=False, reason="用户取消")  拒绝执行
        ...
```

传给 Agent：

```python
agent = Agent(model=..., tools=[...], tool_hook=MyApproval())
# 或 RealtimeAgent：
agent = RealtimeAgent(model=..., tools=[...], tool_hook=MyApproval(), mic=..., speaker=...)
```

语义：

- `approve` 为异步方法，实现方可做 I/O（弹对话框、查远程策略等）；
- 批准 → 工具正常执行，与无 hook 时一致；
- 拒绝 → 返回 `ToolResult(is_error=True, content="工具调用被拒绝: {reason}")` 给模型，回合不中断；
- 审批钩子本身抛异常 → 视为否决（reason 为异常信息），不中断回合；
- 不传 `tool_hook`（默认 `None`）时行为完全不变——零开销，向后兼容；
- Agent 并发场景下 `approve` 可能被多个协程同时调用，实现方按需加锁；
- 审批决定计入审计日志（kind=`tool_approval`）。

## Agent 循环与事件

```python
Agent(
    *,
    model: Model,                    # 必填
    tools: Sequence[Tool] = (),      # 工具名重复抛 ValueError
    system: str | None = None,       # 系统提示词
    session: Session | None = None,  # 省略 = 新建默认 Session
    state: AgentState | None = None, # 与 session 互斥；见「状态管理」章
    audit: AuditLog | None = ...,    # 省略 = 写 CWD/.wy_audit/；显式 None 关闭
    max_iterations: int = 50,        # 单回合模型请求次数上限，超限抛 AgentError
)
```

`state` 与 `session` 只能传其一：只传 `session`（或都不传）时内部包装为无扩展的 `AgentState`；`agent.session` 始终是 `agent.state.session` 的别名，既有代码不受影响。

`run(user_input: str, *, reminders: Sequence[str] = ())` 执行一个用户回合：压缩检查 → 模型流 → 并发执行 tool_use → 结果回填 → 再次请求模型……直到 `stop_reason` 非 `"tool_use"`，以 `TurnEnd` 收尾。`reminders` 中的每条提示以 `<system-reminder>` 包裹后作为额外文本块追加在本回合 user 消息尾部——供 harness 注入模式、通知等动态状态：前缀（system prompt/工具/既有历史）保持不变，不破坏厂商的前缀缓存；核心不理解提示内容，注入什么、何时注入由调用方决定。

产出的 `AgentEvent` 是六个 dataclass 的联合类型：

| 事件 | 字段 | 何时产出 |
|---|---|---|
| `TextDelta` | `text: str` | 正文文本增量（实时渲染用） |
| `ThinkingDelta` | `thinking: str` | 思考文本增量（实时渲染用） |
| `ToolCall` | `id, name, input: dict` | 某个工具即将执行 |
| `ToolResult` | `id, name, content: str, is_error: bool` | 某个工具执行完毕 |
| `Compaction` | `dropped: int, summary: str` | 上下文压缩已发生 |
| `TurnEnd` | `usage: Usage, context_tokens: int` | 回合结束（usage 为会话累计用量） |

**异常与回滚**：回合内任何异常（模型 `ModelError`、超限 `AgentError`，也包括**消费方中途 break/关闭事件流**）都会写一条 error 审计，并回滚本回合追加的全部消息，把会话保持在上一个完整回合的状态（本回合已发生过压缩时历史结构已变，跳过回滚）。换言之：**中途停止消费 = 本回合作废**。

## 状态管理

`wy_core.state` 提供 agent 状态的统一容器，位于 `AgentState`：

- `AgentState(session=None, extensions=())`：聚合 `Session`（对话历史/用量）与命名状态扩展；`get(key)` 取扩展，`snapshot()` 聚合各扩展快照为 `{key: data}`（跳过返回 None 的扩展），`restore(data)` 按 key 分发恢复（未知 key 忽略，前向兼容）。
- `StateExtension`：继承并覆写所需方法即得一个扩展——`key`（唯一名）、`snapshot() -> dict | None`（None = 不持久化的易失状态）、`restore(data)`，以及四个生命周期钩子 `on_turn_start`/`on_turn_end`/`on_rollback`/`on_compaction(dropped)`，由 Agent 在回合入口、TurnEnd 前、异常回滚后、压缩后分别调用（钩子不得抛异常）。

**持久化的分层**：内存状态是权威，持久化是它的投影——核心只定义快照/恢复契约，何时落盘、落到哪里由使用方决定（参考实现：wy-coding-agent 把聚合快照作为 `state` 记录追加进会话 JSONL，恢复会话时取最后一条回灌）。

## 消息词汇

全库统一的消息定义，位于 `wy_core.message`：

- 内容块：`TextBlock(text)`、`ThinkingBlock(thinking, signature="")`、`ToolUseBlock(id, name, input)`、`ToolResultBlock(tool_use_id, content, is_error=False)`；每个块自带 `type` 标签，`Block` 为四者联合。
- `Message(role, content)`：`role` 为 `"user"` 或 `"assistant"`，`content` 为块列表；`.text` 属性拼接全部文本块，`.to_dict()` 转可 JSON 序列化字典。
- `user_message(text, *, reminders=())`：构造纯文本 user 消息的快捷函数；`reminders` 逐条以 `<system-reminder>` 包裹为额外文本块追加在正文之后（语义见「Agent 循环」）。
- `Usage(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)`：`.context_tokens` 属性为四分量之和（近似请求完成后的上下文规模），`.add(other)` 用于累计。

**块联合允许应用侧扩展**：核心逐块 `isinstance` 判断，未知块随消息原样携带（例：wy-coding-agent 的 `RedactedThinkingBlock`），由你的 `Model` 实现负责其 wire 翻译。

## Session 与自动上下文压缩

```python
Session(*, max_context_tokens: int = 150_000, keep_recent: int = 8)
```

- 状态：`messages`（消息列表）、`context_tokens`（最近一次请求后的上下文规模）、`total_usage`（累计用量）；方法 `append(message)`、`record_usage(usage)`。
- **仅内存态**：落盘与恢复由使用方自行实现（参考 wy-coding-agent 的 `SessionStore`；审计日志本身已完整留痕）。
- 自动压缩：`context_tokens >= max_context_tokens` 时，Agent 在**下一次模型请求前**（含工具轮之间）触发 `compact(model)`——保留最近 `keep_recent` 条消息（分割点自动前移，保证不拆散 tool_use/tool_result 对），更早历史渲染为纯文本请**同一个模型**总结，替换为一条 `[早前对话摘要]` 开头的 user 消息；`context_tokens` 归零待下轮刷新。
- 压缩对消费方可见：产出 `Compaction(dropped, summary)` 事件并写 `compaction` 审计。

## 审计日志

`AuditLog` 是 append-only JSONL，逐条 flush（不 fsync），中文原样存储。每行：`{"ts": <UTC ISO8601>, "kind": ..., **data}`。

- `AuditLog.default()`：写 `CWD/.wy_audit/<UTC时间戳>-<8位随机>.jsonl`；`AuditLog(path)` 指定路径；`write(kind, data)` / `close()`。
- Agent 默认开启审计（**构造瞬间即写 `agent_start` 一条**），kind 全集：`agent_start` / `request` / `model_end` / `tool_call` / `tool_result` / `compaction` / `error`。
- 注意 `request` 记录**每轮请求的完整消息列表**——长会话下文件会明显增长，且可能含敏感内容；不需要时 `Agent(audit=None)` 关闭，或传自定义 `AuditLog(path)` 控制落盘位置。

## Realtime：端到端语音契约

文本回合式之外，wy-core 提供第二套契约用于端到端语音等**推送式全双工**实时协议（服务端维护对话上下文、VAD 主动触发响应，如 Qwen-Audio realtime / OpenAI Realtime）。这类协议不适配 `Model.stream` 的拉取式回合契约，因此单独成体系，**不使用** `Session`（服务端持上下文，审计日志完整留痕）。参考实现见 wy-realtime-agent。

### RealtimeModel 契约

厂商参数（endpoint、鉴权、音色、VAD 参数、历史轮数……）全部构造期注入；九个抽象方法：

```python
class MyRealtimeModel(RealtimeModel):
    name = "my-realtime"                                    # 仅用于展示与审计

    async def connect(self) -> None: ...                    # 建连；失败 raise RealtimeError
    async def update_session(self, *, system=None, tools=()) -> dict: ...
                                                            # 下发 system+工具；返回实发载荷（供审计）
    async def send_audio(self, pcm: bytes) -> None: ...     # 推一块用户语音 PCM
    async def send_user_text(self, text: str) -> None: ...  # 注入 user 文字消息（不触发响应）
    async def send_tool_result(self, call_id: str, output: str) -> None: ...
                                                            # 回写工具结果（不触发响应）
    async def create_response(self) -> None: ...            # 请求模型立即开始一次回复
    async def cancel_response(self) -> None: ...            # 取消进行中的回复（打断）
    def events(self) -> AsyncIterator[RealtimeModelEvent]: ...
                                                            # 厂商 wire 事件 → 类型化事件
    async def close(self) -> None: ...                      # 幂等，未建连可调
```

实现方义务：wire 事件自行翻译为下表词汇，**词汇之外不产出**（静默忽略）；`AudioDelta.pcm` 必须已解码、`FunctionCall.arguments` 必须已解析（残缺回退空 dict）；转写增量（`UserTranscriptDelta`/`AssistantTranscriptDelta`）仅供实时渲染，厂商协议不提供流式转写时可不产出，权威全文始终以 `UserTranscript`/`AssistantTranscript` 为准；生命周期信号（`SessionReady`/`SpeechStopped`/`TurnCommitted`）同理按厂商能力尽力产出，供下游状态管理，编排正确性不依赖它们；服务端正常关闭时 `events()` 自然结束，握手/传输/异常关闭一律 raise `RealtimeError`（未建连时调用发送类方法同样如此）；`update_session` 由 Agent 在建连后调用恰好一次；`tools` 只读 schema **永不调用 `execute`**。

| 模型事件（`RealtimeModelEvent`） | 字段 | 语义 |
|---|---|---|
| `SessionReady` | — | 服务端确认会话配置生效，可以开始对话 |
| `SpeechStarted` | — | VAD 检测到用户开始说话 |
| `UserTranscriptDelta` | `text, stash` | 用户语音转写增量：`text` 为新确定文本（逐段累加），`stash` 为未定暂存尾部（整体替换） |
| `SpeechStopped` | `reason` | VAD 检测到用户语音结束；smart_turn 判无效轮时 `reason="turn_invalid"`，否则为 None |
| `UserTranscript` | `text` | 用户语音转写完成 |
| `TurnCommitted` | — | 用户语音已提交为对话轮次，模型随即开始推理 |
| `TurnDiscarded` | — | 服务端判定语音不构成有效轮次，不会产出回复 |
| `ResponseStarted` | `response_id` | 服务端开始产出一次回复 |
| `AudioDelta` | `pcm: bytes` | 回复语音增量（已解码 PCM） |
| `AssistantTranscriptDelta` | `text` | 模型回复转写（字幕）增量 |
| `AssistantTranscript` | `text` | 模型回复转写完成 |
| `FunctionCall` | `call_id, name, arguments: dict` | 模型请求执行工具 |
| `ResponseDone` | `cancelled: bool` | 一次回复结束（被打断取消则 cancelled=True） |
| `ErrorEvent` | `type, message` | 服务端非致命错误（致命错误随后表现为连接关闭） |

### 音频抽象

`AudioSource`（`start` / `read(timeout) -> bytes | None` / `stop`）与 `AudioSink`（`start` / `play(pcm)` / `clear` / `is_playing` / `stop`）。PCM 一律 int16 单声道字节；**采样率不属于契约**，由音频实现与 RealtimeModel 实现两侧按厂商协议自行约定，core 只搬运字节。`read` 为同步阻塞方法（agent 经 `asyncio.to_thread` 调用），超时返回 `None`；`play` 不得阻塞事件循环；`clear` 用于打断；`is_playing` 供回声抑制。

### RealtimeAgent 编排

```python
RealtimeAgent(
    *,
    model: RealtimeModel,            # 必填
    tools: Sequence[Tool] = (),      # 与文本侧同一套 Tool；重名抛 ValueError
    system: str | None = None,       # 经 update_session 下发
    mic: AudioSource,                # 必填（无设备环境注入假件）
    speaker: AudioSink,              # 必填
    echo_suppression: bool = True,   # True=AI 说话时闭麦；False=耳机模式，支持打断
    audit: AuditLog | None = ...,    # 省略 = 写 CWD/.wy_audit/；显式 None 关闭
    closer: Callable[[], None] | None = None,  # close() 时释放的注入资源（如 MCP 连接）
)
```

`run()` 建连 → 一次 `update_session` → 后台任务持续推麦克风音频 + 消费模型事件流，产出 `RealtimeEvent`。除 `FunctionCall`（收集待执行）与被抑制的残余增量外，模型事件一律原样透出，加上编排自产的四个事件（`ToolCall`/`ToolResult`/`ToolResultsSubmitted`/`Interrupted`/`SessionEnded`），覆盖完整生命周期，供下游做细粒度状态管理（按典型时序排列）：

| 事件 | 何时产出 |
|---|---|
| `SessionReady` | 服务端确认会话配置生效（模型事件透传，厂商可选） |
| `SpeechStarted` | 用户开始说话（VAD） |
| `UserTranscriptDelta` | 用户说话中：转写流式增量（仅供渲染、不审计） |
| `SpeechStopped(reason)` | 用户说话结束（VAD）；smart_turn 判无效轮时 `reason="turn_invalid"` |
| `UserTranscript` | 用户语音转写完成 |
| `TurnCommitted` | 轮次已提交，模型开始推理（"思考中"起点） |
| `TurnDiscarded` | 判非轮次：不会有回复，状态回到空闲 |
| `ResponseStarted(response_id)` | 模型开始响应 |
| `AudioDelta(pcm)` | 响应中·语音增量（已送扬声器后透出；被打断回复的残余被抑制） |
| `AssistantTranscriptDelta` | 响应中·文本（字幕）增量（仅供渲染、不审计；残余同样被抑制） |
| `AssistantTranscript` | 模型回复转写完成 |
| `ResponseDone(cancelled)` | 一次响应结束（被打断取消则 `cancelled=True`） |
| `ToolCall` / `ToolResult` | 工具执行开始/结束（与文本侧同一对 dataclass；两者之间即"执行中"） |
| `ToolResultsSubmitted(count)` | 全部工具结果已回写并触发二轮推理（"提交给模型"） |
| `Interrupted(response_id)` | 用户语音打断了进行中的回复 |
| `ErrorEvent(type, message)` | 服务端非致命错误（含 ASR 转写失败；已写 error 审计后透传） |
| `SessionEnded(reason)` | 连接结束（服务端正常关闭或传输失败），`run()` 以此收尾 |

内置编排语义（实现方无须关心）：

- **打断**：`SpeechStarted` 即清空扬声器；回复进行中则再 `cancel_response`，并抑制残余 `AudioDelta` 与 `AssistantTranscriptDelta` 直到下一个 `ResponseStarted`。
- **回声抑制**：`echo_suppression=True`（免耳机）回复/播放期间闭麦 + 结束后 0.5s 冷却，不支持语音打断；`False`（耳机模式）用能量门限滤回声，高能量语音照发以支持打断。
- **收集式 function calling**：一次回复的多个 `FunctionCall` 先收集，非 cancelled 的 `ResponseDone` 后统一顺序执行、逐个 `send_tool_result`，最后只触发一次二轮推理；被打断（cancelled）的回复丢弃未执行调用。工具执行语义与 `Agent` 一致（`to_thread`、异常/未知工具转 `Error: ...` 文本不中断会话）。
- **后台文字指令注入**：`await agent.send_user_text(text)`（须在 `run()` 所在事件循环调用，未建连抛 `RealtimeError`）——空闲立即注入并触发响应；在听（`SpeechStarted` 起）/在答/响应待建时排队，回合真正结束（无待执行工具的 `ResponseDone` 或 `TurnDiscarded`）后按序补发、只触发一次响应；`ErrorEvent` 兜底清除"响应待建"防止队列卡死。
- 传输失败（`RealtimeError`）→ `SessionEnded` 优雅收尾；其余异常审计后上抛；出口统一停发送任务、停音频、关连接。

审计 kind 全集：`agent_start` / `session_update` / `user_transcript` / `assistant_transcript` / `user_text` / `interrupted` / `tool_call` / `tool_result` / `error`。其中 `session_update` 记录 `update_session` 返回的实发载荷——音色、VAD 等厂商字段靠它留痕。转写增量不留痕（仅供渲染，完整转写在 `user_transcript`/`assistant_transcript`）。

## 公开 API 一览

均从包根导入：`from wy_core import ...`

| 导出 | 说明 |
|---|---|
| `Agent` / `AgentError` / `AgentEvent` | agent 循环、其错误类型与事件联合类型 |
| `TurnEnd` / `ToolCall` / `ToolResult` / `Compaction` | agent 事件 dataclass（`ToolCall`/`ToolResult` 与实时侧共用） |
| `Model` / `ModelError` / `ModelEvent` | 模型抽象契约、错误类型与流事件联合类型 |
| `TextDelta` / `ThinkingDelta` / `ModelEnd` | 模型流事件 dataclass |
| `Tool` | 工具抽象契约 |
| `ToolApproval` / `ToolHook` | 工具审批结果与审批钩子抽象契约 |
| `Session` | 内存态会话与自动压缩 |
| `AgentState` / `StateExtension` | agent 状态容器与命名扩展契约（快照/恢复 + 生命周期钩子） |
| `AuditLog` | JSONL 审计日志 |
| `Message` / `Block` / `TextBlock` / `ThinkingBlock` / `ToolUseBlock` / `ToolResultBlock` | 统一消息词汇 |
| `Usage` / `user_message` | 用量统计与 user 消息快捷构造 |
| `RealtimeAgent` / `RealtimeEvent` | 实时编排循环与其事件联合类型 |
| `ToolResultsSubmitted` / `Interrupted` / `SessionEnded` | 实时编排自产事件 dataclass |
| `RealtimeModel` / `RealtimeError` / `RealtimeModelEvent` | 实时模型抽象契约、错误类型与事件联合类型 |
| `SessionReady` / `ResponseStarted` / `AudioDelta` / `SpeechStarted` / `SpeechStopped` / `TurnCommitted` / `TurnDiscarded` / `UserTranscriptDelta` / `UserTranscript` / `AssistantTranscriptDelta` / `AssistantTranscript` / `FunctionCall` / `ResponseDone` / `ErrorEvent` | 实时模型事件 dataclass（除 `FunctionCall` 外均随 `RealtimeEvent` 透传） |
| `AudioSource` / `AudioSink` | 流式音频 IO 抽象 |

## 集成检查单（给 AI 的注意事项）

1. **实现 `Model` 是唯一的重活**——义务清单（上文六条）逐条照办；最常见错误是忘了组装完整 `ModelEnd.message`（增量不是权威内容）或漏发 `ModelEnd`。
2. **`Agent` 构造瞬间就写审计文件**（CWD/.wy_audit/）——不希望在 CWD 留痕就显式 `audit=None`，或传 `AuditLog(path)` 指定位置。
3. **单实例不支持并发 `run()`**；多回合对话复用同一实例串行调用即可保留历史。
4. **中途停止消费事件流 = 本回合作废**——`async for` 中 break 会触发回滚，本回合消息不会留在 session 里；要保留结果就消费到 `TurnEnd`。
5. **工具在同一轮内是并发执行的**（`asyncio.gather` + `to_thread`）——`execute` 实现需线程安全；异常不会向上传播，统一转 `Error: ...` 文本回给模型。
6. `stop_reason` 只有 `"tool_use"` 有特殊语义，其余任何取值都按回合结束处理——厂商的 `max_tokens`/`stop_sequence` 等停止原因原样传即可。
7. 压缩只发生在**两次模型请求之间**，且依赖 `record_usage` 刷新的 `context_tokens`——Agent 内部已自动处理；绕开 Agent 手工驱动 Session 时记得自己调 `record_usage`。
8. `max_iterations`（默认 50）是失控循环保险丝，超限抛 `AgentError` 并回滚本回合。
9. 本包**零运行时依赖**且承诺保持——不要在扩展它时引入第三方库；应用层能力（厂商 SDK、持久化、UI）放在你自己的包里。
10. **实时侧：实现 `RealtimeModel` 的翻译层是唯一重活**——词汇之外的 wire 事件不要产出；`update_session` 要返回实际发送的载荷（音色、VAD 等厂商字段的审计留痕靠它）；`RealtimeError` 是 core 与实现之间的失败契约（core 捕获它转 `SessionEnded` 优雅收尾）。
11. `RealtimeAgent` 的 `mic`/`speaker` 必填——无设备环境注入假件即可（`read` 返回 `None` 表示暂无数据；参考 wy-realtime-agent 测试的 FakeMic/FakeSpeaker）。
