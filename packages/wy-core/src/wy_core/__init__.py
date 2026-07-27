"""wy-core:极简 agent core runtime。

使用方式:继承 ``Model`` 适配任意 LLM API、继承 ``Tool`` 增加工具,
即得到完整 harness agent::

    agent = Agent(model=MyModel(api_key=...), tools=[MyTool()], system="...")
    async for event in agent.run("帮我看看这个仓库"):
        match event:
            case TextDelta(text=text): ...   # 实时渲染正文
            case ToolCall(name=name):  ...   # 展示工具调用
            case TurnEnd():            ...   # 回合结束

会话历史与自动上下文压缩在 ``Session``;审计日志默认写入
CWD/.wy_audit/(``Agent(audit=None)`` 关闭)。
"""

from wy_core.agent import (
    Agent,
    AgentError,
    AgentEvent,
    Compaction,
    ToolCall,
    ToolResult,
    TurnEnd,
)
from wy_core.log import AuditLog
from wy_core.message import (
    Block,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    user_message,
)
from wy_core.model import (
    Model,
    ModelEnd,
    ModelError,
    ModelEvent,
    TextDelta,
    ThinkingDelta,
)
from wy_core.session import Session
from wy_core.tool import Tool

__all__ = [
    "Agent",
    "AgentError",
    "AgentEvent",
    "AuditLog",
    "Block",
    "Compaction",
    "Message",
    "Model",
    "ModelEnd",
    "ModelError",
    "ModelEvent",
    "Session",
    "TextBlock",
    "TextDelta",
    "ThinkingBlock",
    "ThinkingDelta",
    "Tool",
    "ToolCall",
    "ToolResult",
    "ToolResultBlock",
    "ToolUseBlock",
    "TurnEnd",
    "Usage",
    "user_message",
]
