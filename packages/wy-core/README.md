# wy-core SDK 使用文档

零依赖（纯标准库）的极简 agent core runtime：统一消息词汇、`Model`/`Tool` 抽象契约、内存态会话与自动上下文压缩、JSONL 审计日志、完整的 agent 工具循环。**继承 `Model` 适配任意 LLM API、继承 `Tool` 添加工具，即得到一个完整的 harness agent。**

本文面向把本包当作 SDK 使用的开发者（含 AI 编码助手）。包内部实现约定见 [AGENTS.md](AGENTS.md)；两个参考消费方：[wy-coding-agent](../wy-coding-agent)（完整用法：`AnthropicModel` 模型实现、本地工具、持久化、TUI）、[wy-realtime-agent](../wy-realtime-agent)（只复用 `Tool`/`AuditLog`/事件词汇的最小用法）。

## 架构一览

```
Agent.run(user_input)  ← async 事件流（增量/工具/压缩/回合结束）
  ├─ Model    抽象契约：你继承它适配厂商 API（stream → TextDelta/ThinkingDelta/ModelEnd）
  ├─ Tool[]   抽象契约：你继承它添加工具（name/description/parameters + 同步 execute）
  ├─ Session  内存态消息历史 + 用量统计 + 自动上下文压缩
  └─ AuditLog JSONL 审计（默认开启，写 CWD/.wy_audit/）
```

模块依赖方向：`agent → session/log/model/tool → message`，无环。全库只用一套消息词汇（`message` 模块，Anthropic 风格中立块）；非 Anthropic 后端由你的 `Model` 实现自行完成两侧格式翻译。

## 安装

要求 Python >= 3.12。**零运行时依赖**，可放心嵌入任何环境。

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

## Agent 循环与事件

```python
Agent(
    *,
    model: Model,                    # 必填
    tools: Sequence[Tool] = (),      # 工具名重复抛 ValueError
    system: str | None = None,       # 系统提示词
    session: Session | None = None,  # 省略 = 新建默认 Session
    audit: AuditLog | None = ...,    # 省略 = 写 CWD/.wy_audit/；显式 None 关闭
    max_iterations: int = 50,        # 单回合模型请求次数上限，超限抛 AgentError
)
```

`run(user_input: str)` 执行一个用户回合：压缩检查 → 模型流 → 并发执行 tool_use → 结果回填 → 再次请求模型……直到 `stop_reason` 非 `"tool_use"`，以 `TurnEnd` 收尾。

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

## 消息词汇

全库统一的消息定义，位于 `wy_core.message`：

- 内容块：`TextBlock(text)`、`ThinkingBlock(thinking, signature="")`、`ToolUseBlock(id, name, input)`、`ToolResultBlock(tool_use_id, content, is_error=False)`；每个块自带 `type` 标签，`Block` 为四者联合。
- `Message(role, content)`：`role` 为 `"user"` 或 `"assistant"`，`content` 为块列表；`.text` 属性拼接全部文本块，`.to_dict()` 转可 JSON 序列化字典。
- `user_message(text)`：构造纯文本 user 消息的快捷函数。
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

## 公开 API 一览

均从包根导入：`from wy_core import ...`

| 导出 | 说明 |
|---|---|
| `Agent` / `AgentError` / `AgentEvent` | agent 循环、其错误类型与事件联合类型 |
| `TurnEnd` / `ToolCall` / `ToolResult` / `Compaction` | agent 事件 dataclass |
| `Model` / `ModelError` / `ModelEvent` | 模型抽象契约、错误类型与流事件联合类型 |
| `TextDelta` / `ThinkingDelta` / `ModelEnd` | 模型流事件 dataclass |
| `Tool` | 工具抽象契约 |
| `Session` | 内存态会话与自动压缩 |
| `AuditLog` | JSONL 审计日志 |
| `Message` / `Block` / `TextBlock` / `ThinkingBlock` / `ToolUseBlock` / `ToolResultBlock` | 统一消息词汇 |
| `Usage` / `user_message` | 用量统计与 user 消息快捷构造 |

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
