"""Provider contract used by the agent service.

`wiley_agent` 只定义约定：宿主项目按本模块的契约实现自己的模型请求方法，
并返回 `wiley_agent.provider.events` 中定义的 provider 中立事件。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from wiley_agent.provider.events import ProviderEvent


class ProviderError(RuntimeError):
    """Raised when a provider request cannot be completed or decoded."""


class BaseProvider(ABC):
    """Asynchronous streaming-model contract implemented by library consumers.

    实现方义务（这段 docstring 即约定本体）：

    - 厂商参数全部在实现类构造期注入（模型名、max_tokens、thinking、
      鉴权、endpoint、temperature……），`stream_request` 的签名固定不变，
      只接收逐轮变化的会话状态。
    - **输入**：`messages` 使用 Anthropic 风格的中立块 schema——assistant
      内容块为 `thinking{thinking, signature}` / `redacted_thinking{data}` /
      `text{text}` / `tool_use{id, name, input, caller?}`；工具结果以 user
      消息携带 `{type: "tool_result", tool_use_id, content, is_error?}` 块。
      非 Anthropic 后端由实现自行完成两侧格式翻译。`system` 为组装完成的
      系统提示串或 None；`tools` 为 `{name, description, input_schema}`
      字典列表或 None。
    - **输出事件语义**（harness 的 agent 循环依赖这些约定）：
      * `ToolCall` 按 `index` 流式拼装——某 index 的首个事件携带
        `tool_call_id` / `name`（及可选 `caller`），后续同 index 事件携带
        `input_json` 增量，由 harness 串接成完整入参；
      * `UsageEvent.stop_reason` 在模型请求执行工具时必须映射为
        `"tool_use"`（驱动工具循环），其他取值视为回合结束；同一轮内
        多个 `UsageEvent` 的用量分量会被 harness 累加；
      * `ErrorEvent` 表示流内厂商错误，harness 收到即抛 `ProviderError`
        中止本轮；`DoneEvent` 为回合终止标记；
      * 传输 / 解码失败必须 raise `ProviderError`。
    """

    @property
    def model(self) -> str:
        """Model identity for prompts and telemetry; empty string means unknown."""
        return ""

    @abstractmethod
    def stream_request(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Run one streaming request and yield provider-neutral events."""
